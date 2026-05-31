import logging
import os
import pandas as pd
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    ApplicationBuilder, CommandHandler, MessageHandler,
    CallbackQueryHandler, ContextTypes, filters
)
import anthropic
from io import BytesIO

# ─── CONFIGURAÇÃO ───────────────────────────────────────────
import os
TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN")
ANTHROPIC_KEY  = os.environ.get("ANTHROPIC_KEY")

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO
)

# ─── ESTADO TEMPORÁRIO (em memória) ─────────────────────────
dados_usuario = {}  # {chat_id: {"vendas": df, "produtos": df}}

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
    prompt = f"""Você é o MercadoBot, um assistente inteligente para operadores de mercadinhos autônomos em condomínios brasileiros.

Abaixo estão os dados reais do operador:

{contexto}

Com base nesses dados, responda de forma clara, objetiva e em português brasileiro.
Use emojis para facilitar a leitura. Seja direto e destaque números importantes.
Quando identificar algo preocupante (queda, cancelamentos altos, possível ruptura), alerte.
Quando houver oportunidade (produto para testar, horário explorar), sugira.

Pergunta/Comando do operador: {pergunta}"""

    msg = client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=1000,
        messages=[{"role": "user", "content": prompt}]
    )
    return msg.content[0].text


# ─── HANDLERS ───────────────────────────────────────────────

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    texto = (
        "👋 *Bem-vindo ao MercadoBot!*\n\n"
        "Sou seu assistente de inteligência para mercadinhos autônomos.\n\n"
        "📎 *Como começar:*\n"
        "1. Exporte o *Resumo Geral de Vendas* do PDV Legal em Excel\n"
        "2. Exporte os *Produtos Mais Vendidos* em Excel\n"
        "3. Envie os dois arquivos aqui\n\n"
        "Depois use os comandos abaixo 👇"
    )
    keyboard = [
        [InlineKeyboardButton("📊 Briefing do dia", callback_data="briefing")],
        [InlineKeyboardButton("📦 Produtos", callback_data="produtos"),
         InlineKeyboardButton("💳 Pagamentos", callback_data="pagamentos")],
        [InlineKeyboardButton("📅 Semana", callback_data="semana"),
         InlineKeyboardButton("⚠️ Alertas", callback_data="alertas")],
    ]
    await update.message.reply_text(
        texto,
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup(keyboard)
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

        # Detecta qual relatório é pelo nome das colunas
        if "nomefilial" in colunas and "formarecebimento" in colunas:
            dados_usuario[chat_id]["vendas"] = df
            await update.message.reply_text(
                "✅ *Resumo de Vendas carregado!*\n"
                f"📊 {len(df)} transações encontradas.\n\n"
                "Agora envie o arquivo de *Produtos Mais Vendidos* ou use /briefing",
                parse_mode="Markdown"
            )
        elif "produto" in colunas and "quantidade" in colunas:
            dados_usuario[chat_id]["produtos"] = df
            await update.message.reply_text(
                "✅ *Produtos carregados!*\n"
                f"📦 {df['produto'].nunique()} SKUs encontrados.\n\n"
                "Use /briefing para ver a análise completa!",
                parse_mode="Markdown"
            )
        else:
            await update.message.reply_text(
                "⚠️ Não reconheci o formato deste arquivo.\n"
                "Envie o *Resumo Geral de Vendas* ou os *Produtos Mais Vendidos* do PDV Legal.",
                parse_mode="Markdown"
            )
    except Exception as e:
        await update.message.reply_text(f"❌ Erro ao ler o arquivo: {str(e)}")


async def comando_briefing(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    if chat_id not in dados_usuario or not dados_usuario[chat_id]:
        await update.message.reply_text(
            "📎 Primeiro envie seus arquivos Excel aqui.\n"
            "Use /start para ver as instruções."
        )
        return
    await update.message.reply_text("⏳ Gerando seu briefing...")
    ctx = resumo_dados(chat_id)
    resposta = await perguntar_ia(ctx, "Gere um briefing completo do dia com os principais números, alertas e oportunidades.")
    await update.message.reply_text(resposta)


async def comando_produtos(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    ctx = resumo_dados(chat_id)
    await update.message.reply_text("⏳ Analisando produtos...")
    resposta = await perguntar_ia(ctx, "Analise os produtos: top vendidos por filial, oportunidades de mix entre unidades, e algum produto que deveria estar em ambas as lojas mas só está em uma.")
    await update.message.reply_text(resposta)


async def comando_pagamentos(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    ctx = resumo_dados(chat_id)
    await update.message.reply_text("⏳ Analisando pagamentos...")
    resposta = await perguntar_ia(ctx, "Analise o mix de pagamentos (PIX, Débito, Crédito) por filial. Destaque oportunidades de incentivar PIX para reduzir taxas e qualquer anomalia.")
    await update.message.reply_text(resposta)


async def comando_semana(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    ctx = resumo_dados(chat_id)
    await update.message.reply_text("⏳ Calculando semanas...")
    resposta = await perguntar_ia(ctx, "Analise a evolução semanal do faturamento por filial. Identifique semanas de queda, crescimento e explique possíveis causas com base nos dados disponíveis.")
    await update.message.reply_text(resposta)


async def comando_alertas(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    ctx = resumo_dados(chat_id)
    await update.message.reply_text("⏳ Verificando alertas...")
    resposta = await perguntar_ia(ctx, "Liste todos os alertas importantes: cancelamentos acima do normal, queda de faturamento em alguma unidade, produtos com baixo giro, horários sem venda. Seja específico com os números.")
    await update.message.reply_text(resposta)


async def mensagem_livre(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Responde perguntas em linguagem natural."""
    chat_id = update.effective_chat.id
    pergunta = update.message.text
    ctx = resumo_dados(chat_id)
    await update.message.reply_text("⏳ Pensando...")
    resposta = await perguntar_ia(ctx, pergunta)
    await update.message.reply_text(resposta)


async def callback_botoes(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Trata os botões do menu."""
    query = update.callback_query
    await query.answer()
    chat_id = query.message.chat_id
    ctx = resumo_dados(chat_id)

    mapa = {
        "briefing":   "Gere um briefing completo com os principais números, alertas e oportunidades.",
        "produtos":   "Analise os top produtos por filial e oportunidades de mix entre as unidades.",
        "pagamentos": "Analise o mix de pagamentos e oportunidades de incentivar PIX.",
        "semana":     "Analise a evolução semanal do faturamento por filial com insights.",
        "alertas":    "Liste todos os alertas críticos com números específicos.",
    }

    pergunta = mapa.get(query.data, query.data)
    await query.message.reply_text("⏳ Processando...")
    resposta = await perguntar_ia(ctx, pergunta)
    await query.message.reply_text(resposta)


# ─── MAIN ────────────────────────────────────────────────────
def main():
    app = ApplicationBuilder().token(TELEGRAM_TOKEN).build()

    app.add_handler(CommandHandler("start",       start))
    app.add_handler(CommandHandler("briefing",    comando_briefing))
    app.add_handler(CommandHandler("produtos",    comando_produtos))
    app.add_handler(CommandHandler("pagamentos",  comando_pagamentos))
    app.add_handler(CommandHandler("semana",      comando_semana))
    app.add_handler(CommandHandler("alertas",     comando_alertas))
    app.add_handler(MessageHandler(filters.Document.ALL, receber_arquivo))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, mensagem_livre))
    app.add_handler(CallbackQueryHandler(callback_botoes))

    print("🤖 MercadoBot rodando...")
    app.run_polling()


if __name__ == "__main__":
    main()
