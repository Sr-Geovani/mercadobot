import logging
import os
import pandas as pd
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.patches as mpatches
from io import BytesIO

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, BotCommand, MenuButtonCommands
from telegram.ext import (
    ApplicationBuilder, CommandHandler, MessageHandler,
    CallbackQueryHandler, ContextTypes, filters
)
import anthropic

# ─── CONFIGURAÇÃO ───────────────────────────────────────────
TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN")
ANTHROPIC_KEY  = os.environ.get("ANTHROPIC_KEY")

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO
)

# ─── ESTADO TEMPORÁRIO ──────────────────────────────────────
dados_usuario = {}

# ─── PALETA DE CORES (tema escuro) ──────────────────────────
COR_BG        = "#0e0f11"
COR_SURFACE   = "#1e2027"
COR_BORDA     = "#2a2d35"
COR_VERDE     = "#00e676"
COR_VERMELHO  = "#ff5252"
COR_AMARELO   = "#ffd740"
COR_AZUL      = "#40c4ff"
COR_ROXO      = "#ce93d8"
COR_TEXTO     = "#e8eaf0"
COR_MUTED     = "#6b7280"
CORES_BARRAS  = [COR_VERDE, COR_AZUL, COR_AMARELO, COR_ROXO,
                 "#ff8a65", "#80cbc4", "#fff176", "#ef9a9a"]

def estilo_base(fig, ax):
    fig.patch.set_facecolor(COR_BG)
    ax.set_facecolor(COR_SURFACE)
    ax.tick_params(colors=COR_TEXTO, labelsize=9)
    ax.xaxis.label.set_color(COR_TEXTO)
    ax.yaxis.label.set_color(COR_TEXTO)
    ax.title.set_color(COR_TEXTO)
    for spine in ax.spines.values():
        spine.set_edgecolor(COR_BORDA)
    ax.grid(axis="y", color=COR_BORDA, linewidth=0.5, linestyle="--")
    ax.set_axisbelow(True)

def fig_para_bytes(fig) -> BytesIO:
    buf = BytesIO()
    fig.savefig(buf, format="png", bbox_inches="tight",
                facecolor=fig.get_facecolor(), dpi=150)
    plt.close(fig)
    buf.seek(0)
    return buf

# ─── GRÁFICO 1: Faturamento por filial ──────────────────────
def grafico_faturamento(vendas: pd.DataFrame) -> BytesIO:
    fat = vendas.groupby("nomeFilial")["valor"].sum().sort_values()
    fig, ax = plt.subplots(figsize=(7, 3.5))
    estilo_base(fig, ax)
    bars = ax.barh(fat.index, fat.values, color=[COR_VERDE, COR_AZUL], height=0.5)
    for bar, val in zip(bars, fat.values):
        ax.text(val + fat.values.max() * 0.01, bar.get_y() + bar.get_height() / 2,
                f"R$ {val:,.0f}", va="center", color=COR_TEXTO, fontsize=9, fontweight="bold")
    ax.set_title("Faturamento por Unidade", fontsize=12, fontweight="bold", pad=12)
    ax.set_xlabel("R$")
    ax.xaxis.set_major_formatter(plt.FuncFormatter(lambda x, _: f"R$ {x:,.0f}"))
    fig.tight_layout()
    return fig_para_bytes(fig)

# ─── GRÁFICO 2: Mix de pagamento ────────────────────────────
def grafico_pagamentos(vendas: pd.DataFrame) -> BytesIO:
    mix = vendas.groupby("FormaRecebimento")["valor"].count()
    cores = [COR_VERDE, COR_AZUL, COR_AMARELO][:len(mix)]
    fig, ax = plt.subplots(figsize=(5, 5))
    fig.patch.set_facecolor(COR_BG)
    wedges, texts, autotexts = ax.pie(
        mix.values, labels=mix.index, autopct="%1.1f%%",
        colors=cores, startangle=90,
        wedgeprops=dict(edgecolor=COR_BG, linewidth=2),
        textprops=dict(color=COR_TEXTO, fontsize=10)
    )
    for at in autotexts:
        at.set_color(COR_BG)
        at.set_fontweight("bold")
    ax.set_title("Mix de Pagamento", fontsize=12, fontweight="bold", color=COR_TEXTO, pad=14)
    fig.tight_layout()
    return fig_para_bytes(fig)

# ─── GRÁFICO 3: Receita por categoria ───────────────────────
def grafico_categorias(produtos: pd.DataFrame) -> BytesIO:
    grupos = produtos.groupby("grupo")["valor"].sum().sort_values(ascending=False).head(8)
    fig, ax = plt.subplots(figsize=(8, 4.5))
    estilo_base(fig, ax)
    cores = CORES_BARRAS[:len(grupos)]
    bars = ax.bar(range(len(grupos)), grupos.values, color=cores, width=0.6)
    ax.set_xticks(range(len(grupos)))
    ax.set_xticklabels(
        [g.replace(" E ", "\ne ").replace("/", "/\n").title() for g in grupos.index],
        rotation=0, ha="center", fontsize=8
    )
    total = grupos.sum()
    for bar, val in zip(bars, grupos.values):
        ax.text(bar.get_x() + bar.get_width() / 2, bar.get_height() + total * 0.005,
                f"{val/total*100:.0f}%", ha="center", color=COR_TEXTO, fontsize=8, fontweight="bold")
    ax.set_title("Receita por Categoria", fontsize=12, fontweight="bold", pad=12)
    ax.yaxis.set_major_formatter(plt.FuncFormatter(lambda x, _: f"R$ {x:,.0f}"))
    fig.tight_layout()
    return fig_para_bytes(fig)

# ─── GRÁFICO 4: Top produtos por filial ─────────────────────
def grafico_top_produtos(produtos: pd.DataFrame) -> BytesIO:
    filiais = produtos["nomeloja"].unique()
    fig, axes = plt.subplots(1, len(filiais), figsize=(7 * len(filiais), 5))
    if len(filiais) == 1:
        axes = [axes]
    for ax, filial in zip(axes, filiais):
        estilo_base(fig, ax)
        top = (produtos[produtos["nomeloja"] == filial]
               .sort_values("quantidade", ascending=False)
               .head(6))
        nomes = [p[:22] + "…" if len(p) > 22 else p for p in top["produto"]]
        bars = ax.barh(range(len(top)), top["quantidade"].values,
                       color=CORES_BARRAS[:len(top)], height=0.6)
        ax.set_yticks(range(len(top)))
        ax.set_yticklabels(nomes, fontsize=9)
        ax.invert_yaxis()
        for bar, val in zip(bars, top["quantidade"].values):
            ax.text(val + 0.3, bar.get_y() + bar.get_height() / 2,
                    f"{val} un", va="center", color=COR_TEXTO, fontsize=8)
        nome_curto = filial.split()[-1].title()
        ax.set_title(f"Top Produtos — {nome_curto}", fontsize=11, fontweight="bold", pad=10)
        ax.set_xlabel("Unidades vendidas")
    fig.tight_layout()
    return fig_para_bytes(fig)

# ─── GRÁFICO 5: Evolução semanal ────────────────────────────
def grafico_semanal(vendas: pd.DataFrame) -> BytesIO:
    v = vendas.copy()
    v["DataAbertura"] = pd.to_datetime(v["DataAbertura"], dayfirst=True)
    v["semana"] = v["DataAbertura"].dt.isocalendar().week.astype(str).apply(lambda x: f"S{x}")
    sem = v.groupby(["semana", "nomeFilial"])["valor"].sum().unstack(fill_value=0)
    fig, ax = plt.subplots(figsize=(8, 4))
    estilo_base(fig, ax)
    cores_linhas = [COR_VERDE, COR_AZUL]
    for i, col in enumerate(sem.columns):
        ax.plot(sem.index, sem[col], marker="o", color=cores_linhas[i],
                linewidth=2, markersize=7, label=col.split()[-1].title())
        for x, y in zip(range(len(sem)), sem[col]):
            ax.text(x, y + sem.values.max() * 0.02, f"R${y:,.0f}",
                    ha="center", color=cores_linhas[i], fontsize=8)
    ax.set_xticks(range(len(sem)))
    ax.set_xticklabels(sem.index)
    ax.set_title("Evolução Semanal por Unidade", fontsize=12, fontweight="bold", pad=12)
    ax.yaxis.set_major_formatter(plt.FuncFormatter(lambda x, _: f"R$ {x:,.0f}"))
    ax.legend(facecolor=COR_SURFACE, edgecolor=COR_BORDA,
              labelcolor=COR_TEXTO, fontsize=9)
    fig.tight_layout()
    return fig_para_bytes(fig)

# ─── GRÁFICO 6: Horários de pico ────────────────────────────
def grafico_pico(vendas: pd.DataFrame) -> BytesIO:
    v = vendas.copy()
    v["hora"] = pd.to_datetime(v["HoraAbertura"], format="%H:%M:%S").dt.hour
    pico = v.groupby(["hora", "nomeFilial"])["valor"].count().unstack(fill_value=0)
    fig, ax = plt.subplots(figsize=(9, 4))
    estilo_base(fig, ax)
    x = range(len(pico))
    w = 0.35
    cores_linhas = [COR_VERDE, COR_AZUL]
    for i, col in enumerate(pico.columns):
        offset = (i - 0.5) * w
        bars = ax.bar([xi + offset for xi in x], pico[col],
                      width=w, color=cores_linhas[i], label=col.split()[-1].title(), alpha=0.9)
    ax.set_xticks(list(x))
    ax.set_xticklabels([f"{h}h" for h in pico.index], fontsize=8)
    ax.set_title("Vendas por Horário", fontsize=12, fontweight="bold", pad=12)
    ax.set_ylabel("Nº de vendas")
    ax.legend(facecolor=COR_SURFACE, edgecolor=COR_BORDA,
              labelcolor=COR_TEXTO, fontsize=9)
    fig.tight_layout()
    return fig_para_bytes(fig)

# ─── HELPERS ────────────────────────────────────────────────
LIMITE = 4000

def dividir_mensagem(texto: str) -> list:
    if len(texto) <= LIMITE:
        return [texto]
    partes = []
    while len(texto) > LIMITE:
        corte = texto.rfind("\n\n", 0, LIMITE)
        if corte == -1:
            corte = texto.rfind("\n", 0, LIMITE)
        if corte == -1:
            corte = LIMITE
        partes.append(texto[:corte].strip())
        texto = texto[corte:].strip()
    if texto:
        partes.append(texto)
    return partes

def resumo_dados(chat_id: int) -> str:
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
        partes.append(f"RESUMO DE VENDAS\nTotal: {n_vendas} transações\n"
                      f"Faturamento: R$ {total:.2f}\nTicket médio: R$ {ticket:.2f}\n"
                      f"Cancelamentos: R$ {cancelados:.2f}\n\nPOR FILIAL:\n{filiais.to_string()}")

        v2 = vendas.copy()
        v2["hora"] = pd.to_datetime(v2["HoraAbertura"], format="%H:%M:%S").dt.hour
        pico = v2.groupby("hora")["valor"].count().sort_values(ascending=False).head(4)
        partes.append(f"HORÁRIOS DE PICO:\n{pico.to_string()}")

        mix = vendas.groupby("FormaRecebimento")["valor"].agg(["count","sum"])
        mix["pct"] = (mix["count"] / mix["count"].sum() * 100).round(1)
        partes.append(f"MIX DE PAGAMENTO:\n{mix.to_string()}")

    if produtos is not None:
        top = (produtos.sort_values("quantidade", ascending=False)
                       .groupby("nomeloja").head(5))
        partes.append(f"TOP PRODUTOS:\n{top[['nomeloja','produto','quantidade','valor']].to_string()}")

        grupos = produtos.groupby("grupo")["valor"].sum().sort_values(ascending=False)
        total_p = grupos.sum()
        grupos_str = "\n".join([f"{g}: R$ {v:.2f} ({v/total_p*100:.1f}%)" for g, v in grupos.items()])
        partes.append(f"RECEITA POR CATEGORIA (COMPLETA):\n{grupos_str}")

    return "\n\n".join(partes) if partes else "Nenhum dado carregado ainda."

async def perguntar_ia(contexto: str, pergunta: str) -> str:
    client = anthropic.Anthropic(api_key=ANTHROPIC_KEY)
    prompt = f"""Você é o MercadoBot, assistente de inteligência para operadores de mercadinhos autônomos em condomínios brasileiros.

Dados do operador:
{contexto}

REGRAS DE FORMATAÇÃO:
• Português brasileiro, linguagem direta e natural
• Use emojis para organizar seções, com moderação
• Separe seções com linha em branco
• NUNCA use traços, hífens ou linhas como ——— ou --- para separar seções
• NUNCA use asteriscos duplos para negrito
• Listas de itens: use bullet • simples
• Alertas começam com 🚨 ou ⚠️
• Oportunidades começam com 💡
• Máximo 3 parágrafos por seção
• Inclua TODOS os dados relevantes, não resuma nem corte categorias

Pergunta do operador: {pergunta}"""

    msg = client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=1500,
        messages=[{"role": "user", "content": prompt}]
    )
    return msg.content[0].text

# ─── MENU ───────────────────────────────────────────────────
async def configurar_menu(app):
    comandos = [
        BotCommand("start",      "Início e instruções"),
        BotCommand("briefing",   "📊 Briefing completo"),
        BotCommand("produtos",   "📦 Top produtos e oportunidades"),
        BotCommand("categorias", "🗂 Receita por categoria"),
        BotCommand("pagamentos", "💳 Mix PIX Débito Crédito"),
        BotCommand("semana",     "📅 Evolução semanal"),
        BotCommand("pico",       "🕐 Horários de pico"),
        BotCommand("alertas",    "⚠️ Alertas e atenções"),
        BotCommand("menu",       "🔄 Ver menu"),
    ]
    await app.bot.set_my_commands(comandos)
    await app.bot.set_chat_menu_button(menu_button=MenuButtonCommands())

def menu_principal():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("📊 Briefing",    callback_data="briefing"),
         InlineKeyboardButton("⚠️ Alertas",     callback_data="alertas")],
        [InlineKeyboardButton("📦 Produtos",    callback_data="produtos"),
         InlineKeyboardButton("🗂 Categorias",  callback_data="categorias")],
        [InlineKeyboardButton("💳 Pagamentos",  callback_data="pagamentos"),
         InlineKeyboardButton("🕐 Pico",        callback_data="pico")],
        [InlineKeyboardButton("📅 Semanal",     callback_data="semana")],
    ])

async def enviar_menu(destino, texto="O que deseja ver agora?"):
    await destino.reply_text(texto, reply_markup=menu_principal())

# ─── HANDLERS ───────────────────────────────────────────────

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "👋 Bem-vindo ao MercadoBot!\n\n"
        "Sou seu assistente de inteligência para mercadinhos autônomos.\n\n"
        "Como começar:\n"
        "1. Exporte o Resumo Geral de Vendas do PDV Legal em Excel\n"
        "2. Exporte os Produtos Mais Vendidos em Excel\n"
        "3. Envie os dois arquivos aqui\n\n"
        "Depois escolha uma opção abaixo 👇",
        reply_markup=menu_principal()
    )

async def comando_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Escolha uma opção:", reply_markup=menu_principal())

async def receber_arquivo(update: Update, context: ContextTypes.DEFAULT_TYPE):
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
                f"✅ Resumo de Vendas carregado\n📊 {len(df)} transações encontradas.\n\n"
                "Envie agora o arquivo de Produtos Mais Vendidos ou use o menu abaixo.",
                reply_markup=menu_principal()
            )
        elif "produto" in colunas and "quantidade" in colunas:
            dados_usuario[chat_id]["produtos"] = df
            await update.message.reply_text(
                f"✅ Produtos carregados\n📦 {df['produto'].nunique()} SKUs encontrados.\n\n"
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

# ─── COMANDO: BRIEFING ──────────────────────────────────────
async def comando_briefing(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.message
    chat_id = msg.chat_id
    d = dados_usuario.get(chat_id, {})
    if not d:
        await msg.reply_text("📎 Primeiro envie seus arquivos Excel.\nUse /start para ver as instruções.")
        return

    ctx = resumo_dados(chat_id)
    vendas   = d.get("vendas")
    produtos = d.get("produtos")

    # Seção 1 — Faturamento (texto)
    await msg.reply_text("⏳ Gerando briefing completo...")
    fat_texto = await perguntar_ia(ctx,
        "Resuma o faturamento geral: total consolidado, comparativo entre filiais, "
        "ticket médio, melhor dia da semana. Seja direto e use os números reais.")
    for p in dividir_mensagem(fat_texto):
        await msg.reply_text(p)

    # Gráfico faturamento
    if vendas is not None:
        await msg.reply_photo(photo=grafico_faturamento(vendas), caption="Faturamento por unidade")

    # Seção 2 — Categorias (texto + gráfico)
    cat_texto = await perguntar_ia(ctx,
        "Liste a receita de TODAS as categorias de produtos, do maior para o menor, "
        "com valor em reais e percentual. Não omita nenhuma categoria.")
    for p in dividir_mensagem(cat_texto):
        await msg.reply_text(p)
    if produtos is not None:
        await msg.reply_photo(photo=grafico_categorias(produtos), caption="Receita por categoria")

    # Seção 3 — Pagamentos (texto + gráfico)
    pag_texto = await perguntar_ia(ctx,
        "Analise o mix de pagamentos PIX, Débito e Crédito. "
        "Destaque oportunidades de incentivar PIX para reduzir taxas.")
    for p in dividir_mensagem(pag_texto):
        await msg.reply_text(p)
    if vendas is not None:
        await msg.reply_photo(photo=grafico_pagamentos(vendas), caption="Mix de pagamento")

    # Seção 4 — Alertas
    alertas_texto = await perguntar_ia(ctx,
        "Liste todos os alertas e pontos de atenção: cancelamentos, quedas, "
        "anomalias e oportunidades de melhoria. Seja específico com os números.")
    for p in dividir_mensagem(alertas_texto):
        await msg.reply_text(p)

    await enviar_menu(msg)

# ─── COMANDO: PRODUTOS ──────────────────────────────────────
async def comando_produtos(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.message
    chat_id = msg.chat_id
    d = dados_usuario.get(chat_id, {})
    ctx = resumo_dados(chat_id)
    await msg.reply_text("⏳ Analisando produtos...")
    texto = await perguntar_ia(ctx,
        "Analise os top produtos por filial, oportunidades de mix entre unidades "
        "e produtos que deveriam estar em ambas as lojas mas só estão em uma.")
    for p in dividir_mensagem(texto):
        await msg.reply_text(p)
    if d.get("produtos") is not None:
        await msg.reply_photo(photo=grafico_top_produtos(d["produtos"]), caption="Top produtos por unidade")
    await enviar_menu(msg)

# ─── COMANDO: CATEGORIAS ────────────────────────────────────
async def comando_categorias(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.message
    chat_id = msg.chat_id
    d = dados_usuario.get(chat_id, {})
    ctx = resumo_dados(chat_id)
    await msg.reply_text("⏳ Analisando categorias...")
    texto = await perguntar_ia(ctx,
        "Liste a receita de TODAS as categorias de produtos do maior para o menor, "
        "com valor em reais e percentual do total. Não omita nenhuma.")
    for p in dividir_mensagem(texto):
        await msg.reply_text(p)
    if d.get("produtos") is not None:
        await msg.reply_photo(photo=grafico_categorias(d["produtos"]), caption="Receita por categoria")
    await enviar_menu(msg)

# ─── COMANDO: PAGAMENTOS ────────────────────────────────────
async def comando_pagamentos(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.message
    chat_id = msg.chat_id
    d = dados_usuario.get(chat_id, {})
    ctx = resumo_dados(chat_id)
    await msg.reply_text("⏳ Analisando pagamentos...")
    texto = await perguntar_ia(ctx,
        "Analise o mix de pagamentos PIX, Débito e Crédito por filial. "
        "Destaque oportunidades de incentivar PIX e qualquer anomalia.")
    for p in dividir_mensagem(texto):
        await msg.reply_text(p)
    if d.get("vendas") is not None:
        await msg.reply_photo(photo=grafico_pagamentos(d["vendas"]), caption="Mix de pagamento")
    await enviar_menu(msg)

# ─── COMANDO: SEMANA ────────────────────────────────────────
async def comando_semana(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.message
    chat_id = msg.chat_id
    d = dados_usuario.get(chat_id, {})
    ctx = resumo_dados(chat_id)
    await msg.reply_text("⏳ Calculando semanas...")
    texto = await perguntar_ia(ctx,
        "Analise a evolução semanal do faturamento por filial com os números reais. "
        "Identifique semanas de queda e crescimento.")
    for p in dividir_mensagem(texto):
        await msg.reply_text(p)
    if d.get("vendas") is not None:
        await msg.reply_photo(photo=grafico_semanal(d["vendas"]), caption="Evolução semanal por unidade")
    await enviar_menu(msg)

# ─── COMANDO: PICO ──────────────────────────────────────────
async def comando_pico(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.message
    chat_id = msg.chat_id
    d = dados_usuario.get(chat_id, {})
    ctx = resumo_dados(chat_id)
    await msg.reply_text("⏳ Analisando horários...")
    texto = await perguntar_ia(ctx,
        "Analise os horários de pico de vendas por filial. "
        "Destaque os melhores horários e sugira ações para horários de baixo movimento.")
    for p in dividir_mensagem(texto):
        await msg.reply_text(p)
    if d.get("vendas") is not None:
        await msg.reply_photo(photo=grafico_pico(d["vendas"]), caption="Vendas por horário")
    await enviar_menu(msg)

# ─── COMANDO: ALERTAS ───────────────────────────────────────
async def comando_alertas(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.message
    chat_id = msg.chat_id
    ctx = resumo_dados(chat_id)
    await msg.reply_text("⏳ Verificando alertas...")
    texto = await perguntar_ia(ctx,
        "Liste todos os alertas: cancelamentos acima do normal, queda de faturamento, "
        "produtos com baixo giro, horários sem venda. Seja específico com os números.")
    for p in dividir_mensagem(texto):
        await msg.reply_text(p)
    await enviar_menu(msg)

# ─── MENSAGEM LIVRE ─────────────────────────────────────────
async def mensagem_livre(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    pergunta = update.message.text
    ctx = resumo_dados(chat_id)
    await update.message.reply_text("⏳ Pensando...")
    resposta = await perguntar_ia(ctx, pergunta)
    for p in dividir_mensagem(resposta):
        await update.message.reply_text(p)
    await enviar_menu(update.message)

# ─── BOTÕES INLINE ──────────────────────────────────────────
async def callback_botoes(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    chat_id = query.message.chat_id
    d = dados_usuario.get(chat_id, {})
    ctx = resumo_dados(chat_id)

    acao = query.data
    msg = query.message

    perguntas = {
        "briefing":   None,  # briefing usa fluxo próprio
        "produtos":   "Analise os top produtos por filial e oportunidades de mix.",
        "categorias": "Liste a receita de TODAS as categorias com valor e percentual. Não omita nenhuma.",
        "pagamentos": "Analise o mix de pagamentos e oportunidades de incentivar PIX.",
        "semana":     "Analise a evolução semanal do faturamento por filial com números reais.",
        "pico":       "Analise os horários de pico e sugira ações para horários fracos.",
        "alertas":    "Liste todos os alertas críticos com números específicos.",
    }

    graficos = {
        "produtos":   lambda: grafico_top_produtos(d["produtos"]) if d.get("produtos") is not None else None,
        "categorias": lambda: grafico_categorias(d["produtos"])   if d.get("produtos") is not None else None,
        "pagamentos": lambda: grafico_pagamentos(d["vendas"])     if d.get("vendas")   is not None else None,
        "semana":     lambda: grafico_semanal(d["vendas"])        if d.get("vendas")   is not None else None,
        "pico":       lambda: grafico_pico(d["vendas"])           if d.get("vendas")   is not None else None,
    }

    legenda = {
        "produtos":   "Top produtos por unidade",
        "categorias": "Receita por categoria",
        "pagamentos": "Mix de pagamento",
        "semana":     "Evolução semanal por unidade",
        "pico":       "Vendas por horário",
    }

    if acao == "briefing":
        # Redireciona para o fluxo completo do briefing
        update_fake = type("U", (), {"message": msg})()
        await comando_briefing(update_fake, context)
        return

    await msg.reply_text("⏳ Processando...")
    texto = await perguntar_ia(ctx, perguntas[acao])
    for p in dividir_mensagem(texto):
        await msg.reply_text(p)

    if acao in graficos:
        grafico_buf = graficos[acao]()
        if grafico_buf:
            await msg.reply_photo(photo=grafico_buf, caption=legenda.get(acao, ""))

    await enviar_menu(msg)

# ─── MAIN ────────────────────────────────────────────────────
def main():
    app = ApplicationBuilder().token(TELEGRAM_TOKEN).post_init(configurar_menu).build()

    app.add_handler(CommandHandler("start",       start))
    app.add_handler(CommandHandler("menu",        comando_menu))
    app.add_handler(CommandHandler("briefing",    comando_briefing))
    app.add_handler(CommandHandler("produtos",    comando_produtos))
    app.add_handler(CommandHandler("categorias",  comando_categorias))
    app.add_handler(CommandHandler("pagamentos",  comando_pagamentos))
    app.add_handler(CommandHandler("semana",      comando_semana))
    app.add_handler(CommandHandler("pico",        comando_pico))
    app.add_handler(CommandHandler("alertas",     comando_alertas))
    app.add_handler(MessageHandler(filters.Document.ALL, receber_arquivo))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, mensagem_livre))
    app.add_handler(CallbackQueryHandler(callback_botoes))

    print("🤖 MercadoBot rodando...")
    app.run_polling()

if __name__ == "__main__":
    main()