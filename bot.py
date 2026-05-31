import logging
import os
import pandas as pd
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, BotCommand, MenuButtonCommands
from telegram.ext import (
    ApplicationBuilder, CommandHandler, MessageHandler,
    CallbackQueryHandler, ContextTypes, filters
)
import anthropic
from io import BytesIO

# ─── CONFIGURAÇÃO ───────────────────────────────────────────
TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN")
ANTHROPIC_KEY  = os.environ.get("ANTHROPIC_KEY")

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO
)

# ─── ESTADO TEMPORÁRIO (em memória) ─────────────────────────
dados_usuario = {}  # {chat_id: {"vendas": df, "produtos": df}}

# ─── LIMITE DE CARACTERES DO TELEGRAM ───────────────────────
LIMITE = 4000  # Telegram aceita até 4096 por mensagem

def dividir_mensagem(texto: str) -> list[str]:
    """Divide texto longo em blocos respeitando o limite do Telegram."""
    if len(texto) <= LIMITE:
        return [texto]
    
    partes = []
    while len(texto) > LIMITE:
        # Tenta quebrar em parágrafo
        corte = texto.rfind("\n\n", 0, LIMITE)
        if corte == -1:
            # Se não achar parágrafo, quebra na última linha
            corte = texto.rfind("\n", 0, LIMITE)
        if corte == -1:
            # Último recurso: corta no limite
            corte = LIMITE
        partes.append(texto[:corte].strip())
        texto = texto[corte:].strip()
    
    if texto:
        partes.append(texto)
    
    return partes


async def enviar_mensagem(update_or_query, texto: str, parse_mode: str = None):
    """Envia mensagem dividindo automaticamente se ultrapassar o limite."""
    partes = dividir_mensagem(texto)
    
    for parte in partes:
        if hasattr(update_or_query, 'message') and update_or_query.message:
            await update_or_query.message.reply_text(parte, parse_mode=parse_mode)
        else:
            await update_or_query.reply_text(parte, parse_mode=parse_mode)


# ─── HELPERS ────────────────────────────────────────────────
def resumo_dados(chat_id: int) -> str:
    """Monta um resumo textual dos DataFrames para enviar à IA."""
    d = dados_usuario.get(chat_id, {})
    vendas   = d.get("vendas")
    produtos = d.get("produtos")
    partes = []

    if vendas is not None:
        total      = vendas["valor"].sum()
        ticket     = vendas["valor"].mean()
        n_vendas   = len(vendas)
        cancelados = vendas["ValorItensCancelados"].sum()
        filiais    = vendas.groupby("nomeFilial")["valor"].agg(["sum","count","mean"])

        partes.append(f"RESUMO DE VENDAS\n"
                      f"Total de transações: {n_vendas}\n"
                      f"Faturamento total: R$ {total:.2f}\n"
                      f"Ticket médio: R$ {ticket:.2f}\n"
                      f"Valor cancelado total: R$ {cancelados:.2f}\n")

        partes.append("POR FILIAL:\n" + filiais.to_string())

        vendas2 = vendas.copy()
        vendas2["hora"] = pd.to_datetime(
            vendas2["HoraAbertura"], format="%H:%M:%S"
        ).dt.hour
        pico = (vendas2.groupby("hora")["valor"]
                       .count()
                       .sort_values(ascending=False)
                       .head(4))
        partes.append(f"\nHORÁRIOS DE PICO:\n{pico.to_string()}")

        mix = vendas.groupby("FormaRecebimento")["valor"].agg(["count","sum"])
        mix["pct"] = (mix["count"] / mix["count"].sum() * 100).round(1)
        partes.append(f"\nMIX DE PAGAMENTO:\n{mix.to_string()}")

    if produtos is not None:
        top = (produtos.sort_values("quantidade", ascending=False)
                       .groupby("nomeloja")
                       .head(5))
        partes.append(f"\nTOP PRODUTOS POR FILIAL:\n{top[['nomeloja','produto','quantidade','valor']].to_string()}")

        grupos = produtos.groupby("grupo")["valor"].sum().sort_values(ascending=False)
        total_p = grupos.sum()
        grupos_pct = grupos.apply(lambda x: f"R$ {x:.2f} ({x/total_p*100:.1f}%)")
        partes.append(f"\nRECEITA POR GRUPO:\n{grupos_pct.to_string()}")

    return "\n\n".join(partes) if partes else "Nenhum dado carregado ainda."


async def perguntar_ia(contexto: str, pergunta: str) -> str:
    """Chama a API do Claude com os dados e a pergunta do usuário."""
    client = anthropic.Anthropic(api_key=ANTHROPIC_KEY)
    prompt = f"""Você é o MercadoBot, assistente de inteligência para operadores de mercadinhos autônomos em condomínios brasileiros.

Dados do operador:
{contexto}

REGRAS DE FORMATAÇÃO — siga sempre:
- Escreva em português brasileiro, linguagem direta e natural
- Use emojis para organizar seções, mas com moderação
- Separe seções com uma linha em branco
- NUNCA use traços, hífens ou linhas como "———" ou "---" para separar seções
- NUNCA use asteriscos duplos (**texto**) para negrito
- Para destacar números importantes, coloque-os no início da linha com emoji
- Listas de itens: use bullet "•" simples, nunca traços
- Quando houver alerta, comece com 🚨 ou ⚠️
- Quando houver oportunidade, comece com 💡
- Seja objetivo: máximo 3 parágrafos por seção

Pergunta do operador: {pergunta}"""

    msg = client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=1500,
        messages=[{"role": "user", "content": prompt}]
    )
    return msg.content[0].text


# ─── MENU DE COMANDOS (botão "/" do Telegram) ────────────────
async def configurar_menu(app):
    """Registra os comandos no menu nativo do Telegram."""
    comandos = [
        BotCommand("start",      "Início e instruções"),
        BotCommand("briefing",   "📊 Briefing completo do período"),
        BotCommand("produtos",   "📦 Top produtos e oportunidades"),
        BotCommand("pagamentos", "💳 Mix PIX Débito Crédito"),
        BotCommand("semana",     "📅 Evolução semanal por unidade"),
        BotCommand("alertas",    "⚠️ Alertas e pontos de atenção"),
        BotCommand("menu",       "🔄 Ver menu de opções"),
    ]
    await app.bot.set_my_commands(comandos)
    await app.bot.set_chat_menu_button(menu_button=MenuButtonCommands())


# ─── KEYBOARD REUTILIZÁVEL ───────────────────────────────────
def menu_principal():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("📊 Briefing", callback_data="briefing"),
         InlineKeyboardButton("⚠️ Alertas",  callback_data="alertas")],
        [InlineKeyboardButton("📦 Produtos",   callback_data="produtos"),
         InlineKeyboardButton("💳 Pagamentos", callback_data="pagamentos")],
        [InlineKeyboardButton("📅 Evolução semanal", callback_data="semana")],
    ])


# ─── HANDLERS ───────────────────────────────────────────────

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    texto = (
        "👋 Bem-vindo ao MercadoBot!\n\n"
        "Sou seu assistente de inteligência para mercadinhos autônomos.\n\n"
        "Como começar:\n"
        "1. Exporte o Resumo Geral de Vendas do PDV Legal em Excel\n"
        "2. Exporte os Produtos Mais Vendidos em Excel\n"
        "3. Envie os dois arquivos aqui\n\n"
        "Depois escolha uma opção abaixo ou use o menu de comandos 👇"
    )
    await update.message.reply_text(texto, reply_markup=menu_principal())


async def comando_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "Escolha uma opção:",
        reply_markup=menu_principal()
    )


async def receber_arquivo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Recebe arquivos Excel enviados pelo operador."""
    chat_id = update.effective_chat.id
    doc = update.message.document

    if not doc.file_name.endswith((".xlsx", ".xls")):
        await update.message.reply_text("⚠️ Por favor envie um arquivo Excel (.xlsx)")
        return

    await update.message.reply_text("⏳ Processando arquivo...")

    file = await doc.get_file()
    bio = BytesIO()
    await file.download_to_memory(bio)
    bio.seek(0)

    try:
        df = pd.read_excel(bio)
        colunas = [c.lower() for c in df.columns]

        if chat_id not in dados_usuario:
            dados_usuario[chat_id] = {}

        if "nomefilial" in colunas and "formarecebimento" in colunas:
            dados_usuario[chat_id]["vendas"] = df
            await update.message.reply_text(
                f"✅ Resumo de Vendas carregado\n"
                f"📊 {len(df)} transações encontradas.\n\n"
                "Envie agora o arquivo de Produtos Mais Vendidos ou use o menu abaixo.",
                reply_markup=menu_principal()
            )
        elif "produto" in colunas and "quantidade" in colunas:
            dados_usuario[chat_id]["produtos"] = df
            await update.message.reply_text(
                f"✅ Produtos carregados\n"
                f"📦 {df['produto'].nunique()} SKUs encontrados.\n\n"
                "Tudo pronto! Escolha uma análise:",
                reply_markup=menu_principal()
            )
        else:
            await update.message.reply_text(
                "⚠️ Não reconheci o formato deste arquivo.\n"
                "Envie o Resumo Geral de Vendas ou os Produtos Mais Vendidos do PDV Legal."
            )
    except Exception as e:
        await update.message.reply_text(f"❌ Erro ao ler o arquivo: {str(e)}")


async def comando_briefing(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    if chat_id not in dados_usuario or not dados_usuario[chat_id]:
        await update.message.reply_text(
            "📎 Primeiro envie seus arquivos Excel.\n"
            "Use /start para ver as instruções."
        )
        return
    await update.message.reply_text("⏳ Gerando seu briefing...")
    ctx = resumo_dados(chat_id)
    resposta = await perguntar_ia(ctx, "Gere um briefing completo com os principais números, alertas e oportunidades. Divida em seções claras: Faturamento, Produtos, Pagamentos e Alertas.")
    for parte in dividir_mensagem(resposta):
        await update.message.reply_text(parte)
    await update.message.reply_text("O que deseja ver agora?", reply_markup=menu_principal())


async def comando_produtos(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    ctx = resumo_dados(chat_id)
    await update.message.reply_text("⏳ Analisando produtos...")
    resposta = await perguntar_ia(ctx, "Analise os produtos: top vendidos por filial, oportunidades de mix entre unidades, e produtos que deveriam estar em ambas as lojas mas só estão em uma.")
    for parte in dividir_mensagem(resposta):
        await update.message.reply_text(parte)
    await update.message.reply_text("O que deseja ver agora?", reply_markup=menu_principal())


async def comando_pagamentos(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    ctx = resumo_dados(chat_id)
    await update.message.reply_text("⏳ Analisando pagamentos...")
    resposta = await perguntar_ia(ctx, "Analise o mix de pagamentos PIX, Débito e Crédito por filial. Destaque oportunidades de incentivar PIX para reduzir taxas e qualquer anomalia.")
    for parte in dividir_mensagem(resposta):
        await update.message.reply_text(parte)
    await update.message.reply_text("O que deseja ver agora?", reply_markup=menu_principal())


async def comando_semana(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    ctx = resumo_dados(chat_id)
    await update.message.reply_text("⏳ Calculando semanas...")
    resposta = await perguntar_ia(ctx, "Analise a evolução semanal do faturamento por filial. Identifique semanas de queda e crescimento com os números reais.")
    for parte in dividir_mensagem(resposta):
        await update.message.reply_text(parte)
    await update.message.reply_text("O que deseja ver agora?", reply_markup=menu_principal())


async def comando_alertas(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    ctx = resumo_dados(chat_id)
    await update.message.reply_text("⏳ Verificando alertas...")
    resposta = await perguntar_ia(ctx, "Liste todos os alertas importantes: cancelamentos acima do normal, queda de faturamento, produtos com baixo giro, horários sem venda. Seja específico com os números.")
    for parte in dividir_mensagem(resposta):
        await update.message.reply_text(parte)
    await update.message.reply_text("O que deseja ver agora?", reply_markup=menu_principal())


async def mensagem_livre(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Responde perguntas em linguagem natural."""
    chat_id = update.effective_chat.id
    pergunta = update.message.text
    ctx = resumo_dados(chat_id)
    await update.message.reply_text("⏳ Pensando...")
    resposta = await perguntar_ia(ctx, pergunta)
    for parte in dividir_mensagem(resposta):
        await update.message.reply_text(parte)
    await update.message.reply_text("O que deseja ver agora?", reply_markup=menu_principal())


async def callback_botoes(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Trata os botões do menu inline."""
    query = update.callback_query
    await query.answer()
    chat_id = query.message.chat_id
    ctx = resumo_dados(chat_id)

    mapa = {
        "briefing":   "Gere um briefing completo com os principais números, alertas e oportunidades. Divida em seções: Faturamento, Produtos, Pagamentos e Alertas.",
        "produtos":   "Analise os top produtos por filial e oportunidades de mix entre as unidades.",
        "pagamentos": "Analise o mix de pagamentos e oportunidades de incentivar PIX para reduzir taxas.",
        "semana":     "Analise a evolução semanal do faturamento por filial com os números reais.",
        "alertas":    "Liste todos os alertas críticos com números específicos.",
    }

    pergunta = mapa.get(query.data, query.data)
    await query.message.reply_text("⏳ Processando...")
    resposta = await perguntar_ia(ctx, pergunta)
    for parte in dividir_mensagem(resposta):
        await query.message.reply_text(parte)
    await query.message.reply_text("O que deseja ver agora?", reply_markup=menu_principal())


# ─── MAIN ────────────────────────────────────────────────────
def main():
    app = ApplicationBuilder().token(TELEGRAM_TOKEN).post_init(configurar_menu).build()

    app.add_handler(CommandHandler("start",      start))
    app.add_handler(CommandHandler("menu",       comando_menu))
    app.add_handler(CommandHandler("briefing",   comando_briefing))
    app.add_handler(CommandHandler("produtos",   comando_produtos))
    app.add_handler(CommandHandler("pagamentos", comando_pagamentos))
    app.add_handler(CommandHandler("semana",     comando_semana))
    app.add_handler(CommandHandler("alertas",    comando_alertas))
    app.add_handler(MessageHandler(filters.Document.ALL, receber_arquivo))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, mensagem_livre))
    app.add_handler(CallbackQueryHandler(callback_botoes))

    print("🤖 MercadoBot rodando...")
    app.run_polling()


if __name__ == "__main__":
    main()
