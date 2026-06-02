import logging
import os
import asyncio
import subprocess
import sys
import pandas as pd
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from io import BytesIO

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, BotCommand, MenuButtonCommands
from telegram.ext import (
    ApplicationBuilder, CommandHandler, MessageHandler,
    CallbackQueryHandler, ContextTypes, filters
)
import anthropic

# ─── CONFIGURAÇÃO ────────────────────────────────────────────
TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN")
ANTHROPIC_KEY  = os.environ.get("ANTHROPIC_KEY")

logging.basicConfig(format="%(asctime)s - %(levelname)s - %(message)s", level=logging.INFO)

dados_usuario = {}
aguardando_dias = {}

# ─── INSTALA PLAYWRIGHT BROWSER SE NECESSÁRIO ────────────────
def garantir_browser():
    """Instala o Chromium do Playwright se ainda não estiver disponível."""
    try:
        from playwright.sync_api import sync_playwright
        with sync_playwright() as p:
            # Tenta apenas verificar se o browser existe
            browser = p.chromium.launch(headless=True)
            browser.close()
        logging.info("✅ Playwright Chromium já instalado.")
    except Exception:
        logging.info("⏳ Instalando Playwright Chromium...")
        subprocess.run(
            [sys.executable, "-m", "playwright", "install", "chromium", "--with-deps"],
            check=True
        )
        logging.info("✅ Playwright Chromium instalado com sucesso.")

# ─── PALETA ──────────────────────────────────────────────────
COR_BG      = "#0e0f11"
COR_SURFACE = "#1e2027"
COR_BORDA   = "#2a2d35"
COR_VERDE   = "#00e676"
COR_AZUL    = "#40c4ff"
COR_AMARELO = "#ffd740"
COR_ROXO    = "#ce93d8"
COR_TEXTO   = "#e8eaf0"
CORES       = [COR_VERDE, COR_AZUL, COR_AMARELO, COR_ROXO, "#ff8a65", "#80cbc4", "#fff176", "#ef9a9a"]

# ─── ESTILO GRÁFICO ──────────────────────────────────────────
def estilo(fig, ax):
    fig.patch.set_facecolor(COR_BG)
    ax.set_facecolor(COR_SURFACE)
    ax.tick_params(colors=COR_TEXTO, labelsize=9)
    ax.xaxis.label.set_color(COR_TEXTO)
    ax.yaxis.label.set_color(COR_TEXTO)
    ax.title.set_color(COR_TEXTO)
    for sp in ax.spines.values():
        sp.set_edgecolor(COR_BORDA)
    ax.grid(axis="y", color=COR_BORDA, linewidth=0.5, linestyle="--")
    ax.set_axisbelow(True)

def salvar(fig) -> BytesIO:
    buf = BytesIO()
    fig.savefig(buf, format="png", bbox_inches="tight", facecolor=fig.get_facecolor(), dpi=150)
    plt.close(fig)
    buf.seek(0)
    return buf

# ─── GRÁFICOS ────────────────────────────────────────────────
def g_faturamento(vendas):
    fat = vendas.groupby("nomeFilial")["valor"].sum().sort_values()
    fig, ax = plt.subplots(figsize=(7, 3))
    estilo(fig, ax)
    bars = ax.barh(fat.index, fat.values, color=[COR_VERDE, COR_AZUL], height=0.45)
    for bar, val in zip(bars, fat.values):
        ax.text(val + fat.max()*0.01, bar.get_y()+bar.get_height()/2,
                f"R$ {val:,.0f}", va="center", color=COR_TEXTO, fontsize=9, fontweight="bold")
    ax.set_title("Faturamento por Unidade", fontsize=11, fontweight="bold", pad=10)
    ax.xaxis.set_major_formatter(plt.FuncFormatter(lambda x,_: f"R$ {x:,.0f}"))
    fig.tight_layout()
    return salvar(fig)

def g_pagamentos(vendas):
    mix = vendas.groupby("FormaRecebimento")["valor"].count()
    fig, ax = plt.subplots(figsize=(5, 4.5))
    fig.patch.set_facecolor(COR_BG)
    wedges, texts, autotexts = ax.pie(
        mix.values, labels=mix.index, autopct="%1.1f%%",
        colors=CORES[:len(mix)], startangle=90,
        wedgeprops=dict(edgecolor=COR_BG, linewidth=2),
        textprops=dict(color=COR_TEXTO, fontsize=10)
    )
    for at in autotexts:
        at.set_color(COR_BG)
        at.set_fontweight("bold")
    ax.set_title("Mix de Pagamento", fontsize=11, fontweight="bold", color=COR_TEXTO, pad=12)
    fig.tight_layout()
    return salvar(fig)

def g_categorias(produtos):
    grupos = produtos.groupby("grupo")["valor"].sum().sort_values(ascending=False).head(8)
    fig, ax = plt.subplots(figsize=(8, 4))
    estilo(fig, ax)
    bars = ax.bar(range(len(grupos)), grupos.values, color=CORES[:len(grupos)], width=0.6)
    ax.set_xticks(range(len(grupos)))
    ax.set_xticklabels(
        [g[:13]+"…" if len(g)>13 else g for g in grupos.index],
        rotation=20, ha="right", fontsize=8
    )
    total = grupos.sum()
    for bar, val in zip(bars, grupos.values):
        ax.text(bar.get_x()+bar.get_width()/2, bar.get_height()+total*0.005,
                f"{val/total*100:.0f}%", ha="center", color=COR_TEXTO, fontsize=8, fontweight="bold")
    ax.set_title("Receita por Categoria", fontsize=11, fontweight="bold", pad=10)
    ax.yaxis.set_major_formatter(plt.FuncFormatter(lambda x,_: f"R$ {x:,.0f}"))
    fig.tight_layout()
    return salvar(fig)

def g_top_produtos(produtos):
    filiais = produtos["nomeloja"].unique()
    fig, axes = plt.subplots(1, len(filiais), figsize=(7*len(filiais), 4.5))
    if len(filiais) == 1:
        axes = [axes]
    for ax, filial in zip(axes, filiais):
        estilo(fig, ax)
        top = produtos[produtos["nomeloja"]==filial].sort_values("quantidade", ascending=False).head(6)
        nomes = [p[:20]+"…" if len(p)>20 else p for p in top["produto"]]
        bars = ax.barh(range(len(top)), top["quantidade"].values, color=CORES[:len(top)], height=0.55)
        ax.set_yticks(range(len(top)))
        ax.set_yticklabels(nomes, fontsize=9)
        ax.invert_yaxis()
        for bar, val in zip(bars, top["quantidade"].values):
            ax.text(val+0.2, bar.get_y()+bar.get_height()/2, f"{val} un", va="center", color=COR_TEXTO, fontsize=8)
        ax.set_title(f"Top Produtos — {filial.split()[-1].title()}", fontsize=11, fontweight="bold", pad=10)
        ax.set_xlabel("Unidades vendidas")
    fig.tight_layout()
    return salvar(fig)

def g_semanal(vendas):
    v = vendas.copy()
    v["DataAbertura"] = pd.to_datetime(v["DataAbertura"], dayfirst=True)
    v["semana"] = v["DataAbertura"].dt.isocalendar().week.astype(str).apply(lambda x: f"S{x}")
    sem = v.groupby(["semana","nomeFilial"])["valor"].sum().unstack(fill_value=0)
    fig, ax = plt.subplots(figsize=(8, 4))
    estilo(fig, ax)
    for i, col in enumerate([COR_VERDE, COR_AZUL][:len(sem.columns)]):
        label = sem.columns[i].split()[-1].title()
        ax.plot(range(len(sem)), sem.iloc[:,i], marker="o", color=col, linewidth=2.5, markersize=8, label=label)
        for x, y in enumerate(sem.iloc[:,i]):
            ax.text(x, y+sem.values.max()*0.03, f"R${y:,.0f}", ha="center", color=col, fontsize=8)
    ax.set_xticks(range(len(sem)))
    ax.set_xticklabels(sem.index)
    ax.set_title("Evolução Semanal", fontsize=11, fontweight="bold", pad=10)
    ax.yaxis.set_major_formatter(plt.FuncFormatter(lambda x,_: f"R$ {x:,.0f}"))
    ax.legend(facecolor=COR_SURFACE, edgecolor=COR_BORDA, labelcolor=COR_TEXTO, fontsize=9)
    fig.tight_layout()
    return salvar(fig)

def g_pico(vendas):
    v = vendas.copy()
    v["hora"] = pd.to_datetime(v["HoraAbertura"], format="%H:%M:%S").dt.hour
    pico = v.groupby(["hora","nomeFilial"])["valor"].count().unstack(fill_value=0)
    fig, ax = plt.subplots(figsize=(10, 4))
    estilo(fig, ax)
    w = 0.35
    for i, col in enumerate([COR_VERDE, COR_AZUL][:len(pico.columns)]):
        label = pico.columns[i].split()[-1].title()
        offset = (i - 0.5) * w
        ax.bar([x+offset for x in range(len(pico))], pico.iloc[:,i],
               width=w, color=col, label=label, alpha=0.9)
    ax.set_xticks(range(len(pico)))
    ax.set_xticklabels([f"{h}h" for h in pico.index], fontsize=8)
    ax.set_title("Vendas por Horário", fontsize=11, fontweight="bold", pad=10)
    ax.set_ylabel("Nº de vendas")
    ax.legend(facecolor=COR_SURFACE, edgecolor=COR_BORDA, labelcolor=COR_TEXTO, fontsize=9)
    fig.tight_layout()
    return salvar(fig)

# ─── FORMATAÇÃO HTML (cores no texto) ────────────────────────
def b(t): return f"<b>{t}</b>"          # negrito
def c(t): return f"<code>{t}</code>"    # destaque monoespaçado (cinza)
def i(t): return f"<i>{t}</i>"          # itálico

def bloco_faturamento(vendas: pd.DataFrame) -> str:
    total = vendas["valor"].sum()
    ticket = vendas["valor"].mean()
    n = len(vendas)
    cancel = vendas["ValorItensCancelados"].sum()
    pct_cancel = (cancel / total * 100) if total else 0

    filiais = vendas.groupby("nomeFilial").agg(
        fat=("valor","sum"), qtd=("valor","count"), tk=("valor","mean"),
        canc=("ValorItensCancelados","sum")
    )

    linhas = [f"📊 {b('FATURAMENTO DO PERÍODO')}\n"]
    linhas.append(f"💰 Total consolidado: {b(f'R$ {total:,.2f}')}")
    linhas.append(f"🛒 Transações: {b(str(n))}")
    linhas.append(f"🎯 Ticket médio: {b(f'R$ {ticket:.2f}')}")
    linhas.append(f"⚠️ Cancelamentos: {c(f'R$ {cancel:,.2f}')} {i(f'({pct_cancel:.1f}% do faturamento)')}\n")

    for filial, row in filiais.iterrows():
        nome = filial.split()[-1].title()
        linhas.append(f"📍 {b(nome)}")
        linhas.append(f"   Faturamento: {b(f'R$ {row.fat:,.2f}')}")
        linhas.append(f"   Vendas: {row.qtd} | Ticket: R$ {row.tk:.2f}")
        linhas.append(f"   Cancelamentos: {c(f'R$ {row.canc:,.2f}')}\n")

    return "\n".join(linhas)

def bloco_pagamentos(vendas: pd.DataFrame) -> str:
    mix = vendas.groupby("FormaRecebimento").agg(qtd=("valor","count"), total=("valor","sum"))
    mix["pct"] = (mix["qtd"] / mix["qtd"].sum() * 100)
    total_geral = mix["total"].sum()

    linhas = [f"💳 {b('MIX DE PAGAMENTO')}\n"]
    icones = {"PIX": "🟢", "DEBITO": "🔵", "CREDITO": "🟡"}
    for forma, row in mix.sort_values("qtd", ascending=False).iterrows():
        ic = icones.get(forma.upper(), "⚪")
        linhas.append(f"{ic} {b(forma)}: {row.qtd} transações ({b(f'{row.pct:.1f}%')})")
        linhas.append(f"   Volume: R$ {row.total:,.2f}")

    pix_pct = mix.loc[mix.index.str.upper()=="PIX","pct"].values
    if len(pix_pct) and pix_pct[0] < 30:
        linhas.append(f"\n💡 {i('PIX abaixo de 30% — incentivar uso pode reduzir taxas de maquininha.')}")

    return "\n".join(linhas)

def bloco_categorias(produtos: pd.DataFrame) -> str:
    grupos = produtos.groupby("grupo")["valor"].sum().sort_values(ascending=False)
    total = grupos.sum()

    linhas = [f"🗂 {b('RECEITA POR CATEGORIA')}\n"]
    medalhas = ["🥇","🥈","🥉"]
    for idx, (grupo, val) in enumerate(grupos.items()):
        pct = val / total * 100
        med = medalhas[idx] if idx < 3 else "  •"
        linhas.append(f"{med} {grupo}: {b(f'R$ {val:,.2f}')} {i(f'({pct:.1f}%)')}")

    return "\n".join(linhas)

def bloco_top_produtos(produtos: pd.DataFrame) -> str:
    linhas = [f"📦 {b('TOP PRODUTOS POR UNIDADE')}\n"]
    for filial in produtos["nomeloja"].unique():
        nome = filial.split()[-1].title()
        top = produtos[produtos["nomeloja"]==filial].sort_values("quantidade", ascending=False).head(5)
        linhas.append(f"📍 {b(nome)}")
        for pos, (_, row) in enumerate(top.iterrows(), 1):
            linhas.append(f"   {pos}. {row['produto']} — {b(f'{int(row.quantidade)} un')} / R$ {row.valor:,.2f}")
        linhas.append("")
    return "\n".join(linhas)

def bloco_semanal(vendas: pd.DataFrame) -> str:
    v = vendas.copy()
    v["DataAbertura"] = pd.to_datetime(v["DataAbertura"], dayfirst=True)
    v["semana"] = v["DataAbertura"].dt.isocalendar().week.astype(str).apply(lambda x: f"S{x}")
    sem = v.groupby(["semana","nomeFilial"])["valor"].sum().unstack(fill_value=0)

    linhas = [f"📅 {b('EVOLUÇÃO SEMANAL')}\n"]
    for semana, row in sem.iterrows():
        linhas.append(f"📌 {b(semana)}")
        for filial, val in row.items():
            nome = filial.split()[-1].title()
            linhas.append(f"   {nome}: {b(f'R$ {val:,.2f}')}")
        linhas.append("")

    # Variação última semana
    if len(sem) >= 2:
        linhas.append(f"📉 {b('Variação última semana:')}")
        ultima = sem.iloc[-1]
        penultima = sem.iloc[-2]
        for filial in sem.columns:
            nome = filial.split()[-1].title()
            var = ultima[filial] - penultima[filial]
            sinal = "▲" if var >= 0 else "▼"
            cor_tag = "" if var >= 0 else i(f"{sinal} R$ {abs(var):,.2f}")
            if var >= 0:
                linhas.append(f"   {nome}: {b(f'▲ R$ {var:,.2f}')}")
            else:
                linhas.append(f"   {nome}: {i(f'▼ R$ {abs(var):,.2f}')}")

    return "\n".join(linhas)

def bloco_pico(vendas: pd.DataFrame) -> str:
    v = vendas.copy()
    v["hora"] = pd.to_datetime(v["HoraAbertura"], format="%H:%M:%S").dt.hour
    pico = v.groupby(["hora","nomeFilial"])["valor"].count().unstack(fill_value=0)

    linhas = [f"🕐 {b('HORÁRIOS DE PICO')}\n"]
    for filial in pico.columns:
        nome = filial.split()[-1].title()
        top3 = pico[filial].sort_values(ascending=False).head(3)
        linhas.append(f"📍 {b(nome)}")
        for hora, qtd in top3.items():
            linhas.append(f"   {hora}h — {b(f'{int(qtd)} vendas')}")
        linhas.append("")

    return "\n".join(linhas)

def bloco_reposicao(produtos: pd.DataFrame, modo: str) -> list:
    """
    Gera lista de reposição por filial.
    modo: 'exato' = repor exatamente o que saiu
          'estoque' = repor o que saiu + 30% de margem de segurança
    """
    v = produtos.copy()
    fator = 1.3 if modo == "estoque" else 1.0
    label_modo = "Reposição + 30% de estoque de segurança" if modo == "estoque" else "Reposição exata do que saiu"

    blocos = []
    for filial in v["nomeloja"].unique():
        nome = filial.split()[-1].title()
        df = v[v["nomeloja"] == filial].sort_values("quantidade", ascending=False)

        linhas = [
            f"🛒 {b(f'LISTA DE REPOSIÇÃO — {nome.upper()}')}",
            f"{i(label_modo)}\n"
        ]

        total_itens = 0
        for _, row in df.iterrows():
            repor = max(1, round(row["quantidade"] * fator))
            total_itens += repor
            sufixo = f"+30% = {repor}" if modo == "estoque" else f"{repor}"
            linhas.append(f"• {row['produto']}")
            linhas.append(f"  {b(f'{sufixo} un')}  {i(f'vendido: {int(row.quantidade)} un')}")

        linhas.append(f"\n📦 {b(f'Total: {total_itens} unidades')}")
        blocos.append("\n".join(linhas))

    return blocos

# ─── INSIGHT IA (curto) ───────────────────────────────────────
async def insight_ia(contexto: str, tema: str) -> str:
    client = anthropic.Anthropic(api_key=ANTHROPIC_KEY)
    prompt = f"""Você é o MercadoBot, assistente para mercadinhos autônomos em condomínios.

Dados:
{contexto}

Gere exatamente 2 insights curtos sobre: {tema}
Cada insight em 1 linha. Use • no início. Sem introdução, sem conclusão.
Foque em ação prática ou alerta específico com número real dos dados.
Não use traços, asteriscos ou markdown."""

    msg = client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=300,
        messages=[{"role": "user", "content": prompt}]
    )
    return msg.content[0].text.strip()

def resumo_dados(chat_id: int) -> str:
    d = dados_usuario.get(chat_id, {})
    vendas   = d.get("vendas")
    produtos = d.get("produtos")
    partes = []

    if vendas is not None:
        total   = vendas["valor"].sum()
        ticket  = vendas["valor"].mean()
        n       = len(vendas)
        cancel  = vendas["ValorItensCancelados"].sum()
        filiais = vendas.groupby("nomeFilial")["valor"].agg(["sum","count","mean"])
        partes.append(f"VENDAS: {n} transações, R$ {total:.2f} total, ticket R$ {ticket:.2f}, cancelamentos R$ {cancel:.2f}")
        partes.append("POR FILIAL:\n" + filiais.to_string())
        v2 = vendas.copy()
        v2["hora"] = pd.to_datetime(v2["HoraAbertura"], format="%H:%M:%S").dt.hour
        pico = v2.groupby("hora")["valor"].count().sort_values(ascending=False).head(5)
        partes.append("PICO:\n" + pico.to_string())
        mix = vendas.groupby("FormaRecebimento")["valor"].agg(["count","sum"])
        mix["pct"] = (mix["count"]/mix["count"].sum()*100).round(1)
        partes.append("PAGAMENTOS:\n" + mix.to_string())

    if produtos is not None:
        top = produtos.sort_values("quantidade", ascending=False).groupby("nomeloja").head(5)
        partes.append("TOP PRODUTOS:\n" + top[["nomeloja","produto","quantidade","valor"]].to_string())
        grupos = produtos.groupby("grupo")["valor"].sum().sort_values(ascending=False)
        total_p = grupos.sum()
        gs = "\n".join([f"{g}: R$ {v:.2f} ({v/total_p*100:.1f}%)" for g,v in grupos.items()])
        partes.append("CATEGORIAS:\n" + gs)

    return "\n\n".join(partes) if partes else "Sem dados."

# ─── MENU ────────────────────────────────────────────────────
async def configurar_menu(app):
    cmds = [
        BotCommand("start",      "Início e instruções"),
        BotCommand("briefing",   "📊 Briefing completo"),
        BotCommand("produtos",   "📦 Top produtos"),
        BotCommand("categorias", "🗂 Receita por categoria"),
        BotCommand("pagamentos", "💳 Mix de pagamento"),
        BotCommand("semana",     "📅 Evolução semanal"),
        BotCommand("pico",       "🕐 Horários de pico"),
        BotCommand("alertas",    "⚠️ Alertas"),
        BotCommand("reposicao",  "🛒 Lista de reposição"),
        BotCommand("atualizar",  "🔄 Buscar dados agora"),
        BotCommand("menu",       "🔄 Menu"),
    ]
    await app.bot.set_my_commands(cmds)
    await app.bot.set_chat_menu_button(menu_button=MenuButtonCommands())

def kb_menu():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("📊 Briefing",    callback_data="briefing"),
         InlineKeyboardButton("⚠️ Alertas",     callback_data="alertas")],
        [InlineKeyboardButton("📦 Produtos",    callback_data="produtos"),
         InlineKeyboardButton("🗂 Categorias",  callback_data="categorias")],
        [InlineKeyboardButton("💳 Pagamentos",  callback_data="pagamentos"),
         InlineKeyboardButton("🕐 Pico",        callback_data="pico")],
        [InlineKeyboardButton("📅 Semanal",     callback_data="semana")],
        [InlineKeyboardButton("🛒 Lista de Reposição", callback_data="reposicao")],
        [InlineKeyboardButton("🔄 Atualizar dados agora", callback_data="atualizar_menu")],
    ])

async def abrir_menu(msg):
    await msg.reply_text("O que deseja analisar agora?", reply_markup=kb_menu())

# ─── ENVIO HTML ──────────────────────────────────────────────
async def enviar(msg, texto: str):
    """Envia texto com parse_mode HTML, dividindo se necessário."""
    LIMITE = 4000
    while len(texto) > LIMITE:
        corte = texto.rfind("\n", 0, LIMITE)
        if corte == -1:
            corte = LIMITE
        await msg.reply_text(texto[:corte], parse_mode="HTML")
        texto = texto[corte:].strip()
    if texto:
        await msg.reply_text(texto, parse_mode="HTML")

# ─── HANDLERS ────────────────────────────────────────────────
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        f"👋 {b('Bem-vindo ao MercadoBot!')}\n\n"
        "Seu assistente de inteligência para mercadinhos autônomos.\n\n"
        f"{b('Como começar:')}\n"
        "1. Exporte o <i>Resumo Geral de Vendas</i> do PDV Legal em Excel\n"
        "2. Exporte os <i>Produtos Mais Vendidos</i> em Excel\n"
        "3. Envie os dois arquivos aqui\n\n"
        "Depois escolha uma opção abaixo 👇",
        parse_mode="HTML",
        reply_markup=kb_menu()
    )

async def comando_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Escolha uma opção:", reply_markup=kb_menu())

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
                f"✅ {b('Resumo de Vendas carregado')}\n📊 {len(df)} transações encontradas.\n\n"
                "Envie agora os <i>Produtos Mais Vendidos</i> ou use o menu abaixo.",
                parse_mode="HTML", reply_markup=kb_menu()
            )
        elif "produto" in colunas and "quantidade" in colunas:
            dados_usuario[chat_id]["produtos"] = df
            await update.message.reply_text(
                f"✅ {b('Produtos carregados')}\n📦 {df['produto'].nunique()} SKUs encontrados.\n\n"
                "Tudo pronto! Escolha uma análise:",
                parse_mode="HTML", reply_markup=kb_menu()
            )
        else:
            await update.message.reply_text(
                "⚠️ Formato não reconhecido.\nEnvie o <i>Resumo Geral de Vendas</i> ou os <i>Produtos Mais Vendidos</i>.",
                parse_mode="HTML"
            )
    except Exception as e:
        await update.message.reply_text(f"❌ Erro: {str(e)}")

# ─── FLUXO BRIEFING ──────────────────────────────────────────
async def fluxo_briefing(msg, chat_id: int):
    d = dados_usuario.get(chat_id, {})
    if not d:
        await msg.reply_text("📎 Envie seus arquivos Excel primeiro. Use /start.")
        return
    vendas   = d.get("vendas")
    produtos = d.get("produtos")
    ctx = resumo_dados(chat_id)

    # Bloco 1 — Faturamento
    await enviar(msg, bloco_faturamento(vendas))
    if vendas is not None:
        await msg.reply_photo(photo=g_faturamento(vendas))

    # Bloco 2 — Categorias
    if produtos is not None:
        await enviar(msg, bloco_categorias(produtos))
        await msg.reply_photo(photo=g_categorias(produtos))

    # Bloco 3 — Pagamentos
    if vendas is not None:
        await enviar(msg, bloco_pagamentos(vendas))
        await msg.reply_photo(photo=g_pagamentos(vendas))

    # Bloco 4 — Semanal
    if vendas is not None:
        await enviar(msg, bloco_semanal(vendas))
        await msg.reply_photo(photo=g_semanal(vendas))

    # Bloco 5 — Insights IA (curtos)
    insight = await insight_ia(ctx, "cancelamentos, quedas de faturamento e oportunidades de produto")
    await enviar(msg, f"💡 {b('INSIGHTS')}\n\n{insight}")

    await abrir_menu(msg)

async def comando_briefing(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("⏳ Gerando briefing completo...")
    await fluxo_briefing(update.message, update.effective_chat.id)

async def comando_produtos(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    d = dados_usuario.get(chat_id, {})
    produtos = d.get("produtos")
    if produtos is None:
        await update.message.reply_text("📎 Envie o arquivo de Produtos Mais Vendidos primeiro.")
        return
    await enviar(update.message, bloco_top_produtos(produtos))
    await update.message.reply_photo(photo=g_top_produtos(produtos))
    ctx = resumo_dados(chat_id)
    insight = await insight_ia(ctx, "oportunidades de mix de produtos entre as unidades")
    await enviar(update.message, f"💡 {b('INSIGHTS')}\n\n{insight}")
    await abrir_menu(update.message)

async def comando_categorias(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    d = dados_usuario.get(chat_id, {})
    produtos = d.get("produtos")
    if produtos is None:
        await update.message.reply_text("📎 Envie o arquivo de Produtos Mais Vendidos primeiro.")
        return
    await enviar(update.message, bloco_categorias(produtos))
    await update.message.reply_photo(photo=g_categorias(produtos))
    ctx = resumo_dados(chat_id)
    insight = await insight_ia(ctx, "categorias com melhor e pior desempenho")
    await enviar(update.message, f"💡 {b('INSIGHTS')}\n\n{insight}")
    await abrir_menu(update.message)

async def comando_pagamentos(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    d = dados_usuario.get(chat_id, {})
    vendas = d.get("vendas")
    if vendas is None:
        await update.message.reply_text("📎 Envie o arquivo de Vendas primeiro.")
        return
    await enviar(update.message, bloco_pagamentos(vendas))
    await update.message.reply_photo(photo=g_pagamentos(vendas))
    ctx = resumo_dados(chat_id)
    insight = await insight_ia(ctx, "mix de pagamento e oportunidade de incentivar PIX")
    await enviar(update.message, f"💡 {b('INSIGHTS')}\n\n{insight}")
    await abrir_menu(update.message)

async def comando_semana(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    d = dados_usuario.get(chat_id, {})
    vendas = d.get("vendas")
    if vendas is None:
        await update.message.reply_text("📎 Envie o arquivo de Vendas primeiro.")
        return
    await enviar(update.message, bloco_semanal(vendas))
    await update.message.reply_photo(photo=g_semanal(vendas))
    ctx = resumo_dados(chat_id)
    insight = await insight_ia(ctx, "variação semanal de faturamento entre as unidades")
    await enviar(update.message, f"💡 {b('INSIGHTS')}\n\n{insight}")
    await abrir_menu(update.message)

async def comando_pico(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    d = dados_usuario.get(chat_id, {})
    vendas = d.get("vendas")
    if vendas is None:
        await update.message.reply_text("📎 Envie o arquivo de Vendas primeiro.")
        return
    await enviar(update.message, bloco_pico(vendas))
    await update.message.reply_photo(photo=g_pico(vendas))
    ctx = resumo_dados(chat_id)
    insight = await insight_ia(ctx, "horários de pico e horários fracos para sugestão de ação")
    await enviar(update.message, f"💡 {b('INSIGHTS')}\n\n{insight}")
    await abrir_menu(update.message)

async def comando_alertas(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    ctx = resumo_dados(chat_id)
    d = dados_usuario.get(chat_id, {})
    vendas = d.get("vendas")
    if not d:
        await update.message.reply_text("📎 Envie seus arquivos Excel primeiro.")
        return

    linhas = [f"🚨 {b('ALERTAS E ATENÇÕES')}\n"]

    if vendas is not None:
        cancel = vendas["ValorItensCancelados"].sum()
        total  = vendas["valor"].sum()
        pct    = cancel/total*100
        if pct > 5:
            linhas.append(f"⚠️ Cancelamentos em {b(f'{pct:.1f}%')} do faturamento — acima do ideal (5%)")
            linhas.append(f"   Valor: {c(f'R$ {cancel:,.2f}')}\n")

        sem = vendas.copy()
        sem["DataAbertura"] = pd.to_datetime(sem["DataAbertura"], dayfirst=True)
        sem["semana"] = sem["DataAbertura"].dt.isocalendar().week
        fat_sem = sem.groupby(["semana","nomeFilial"])["valor"].sum().unstack(fill_value=0)
        if len(fat_sem) >= 2:
            for col in fat_sem.columns:
                var = fat_sem.iloc[-1][col] - fat_sem.iloc[-2][col]
                if var < -200:
                    nome = col.split()[-1].title()
                    linhas.append(f"📉 {b(nome)}: queda de {i(f'R$ {abs(var):,.2f}')} na última semana")

    insight = await insight_ia(ctx, "todos os alertas críticos e ações corretivas imediatas")
    linhas.append(f"\n{insight}")
    await enviar(update.message, "\n".join(linhas))
    await abrir_menu(update.message)

async def comando_reposicao(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    d = dados_usuario.get(chat_id, {})
    if d.get("produtos") is None:
        await update.message.reply_text(
            "📎 Envie o arquivo de <i>Produtos Mais Vendidos</i> primeiro.",
            parse_mode="HTML"
        )
        return
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("✅ Repor exatamente o que saiu", callback_data="rep_exato")],
        [InlineKeyboardButton("📦 Repor + estoque de segurança (30%)", callback_data="rep_estoque")],
    ])
    await update.message.reply_text(
        f"🛒 {b('LISTA DE REPOSIÇÃO')}\n\n"
        f"A lista é baseada em tudo que saiu da loja no período importado.\n\n"
        f"Como deseja repor?",
        parse_mode="HTML",
        reply_markup=kb
    )
    aguardando_dias[chat_id] = True

async def comando_atualizar(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Dispara o download e briefing na hora, para o período escolhido."""
    chat_id = update.effective_chat.id

    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("📅 Hoje",          callback_data="atualizar_hoje")],
        [InlineKeyboardButton("📅 Ontem",         callback_data="atualizar_ontem")],
        [InlineKeyboardButton("📅 Últimos 7 dias", callback_data="atualizar_7dias")],
        [InlineKeyboardButton("📅 Mês atual",     callback_data="atualizar_mes")],
    ])
    await update.message.reply_text(
        f"🔄 {b('ATUALIZAR DADOS')}\n\n"
        f"Qual período deseja buscar agora?",
        parse_mode="HTML",
        reply_markup=kb
    )

async def executar_atualizacao(msg, chat_id: int, data_ini: str, data_fim: str, label: str):
    """Executa o scraper para o período escolhido e envia o briefing."""
    await msg.reply_text(
        f"⏳ Buscando dados de {b(label)} no PDV Legal...\n"
        f"{i('Isso pode levar até 1 minuto.')}",
        parse_mode="HTML"
    )
    try:
        from scraper import baixar_relatorios_periodo
        import pandas as pd

        path_vendas, path_produtos = await asyncio.get_event_loop().run_in_executor(
            None, baixar_relatorios_periodo, data_ini, data_fim
        )

        vendas   = pd.read_excel(path_vendas)
        produtos = pd.read_excel(path_produtos)

        # Atualiza dados em memória do usuário
        if chat_id not in dados_usuario:
            dados_usuario[chat_id] = {}
        dados_usuario[chat_id]["vendas"]   = vendas
        dados_usuario[chat_id]["produtos"] = produtos

        await msg.reply_text(
            f"✅ {b('Dados atualizados!')} Gerando briefing de {b(label)}...",
            parse_mode="HTML"
        )
        await fluxo_briefing(msg, chat_id)

    except Exception as e:
        await msg.reply_text(
            f"❌ Erro ao buscar dados: {str(e)}\n\n"
            f"Você pode importar os arquivos manualmente enviando os Excel aqui."
        )
        await abrir_menu(msg)

async def mensagem_livre(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    ctx = resumo_dados(chat_id)
    await update.message.reply_text("⏳ Pensando...")
    insight = await insight_ia(ctx, update.message.text)
    await enviar(update.message, insight)
    await abrir_menu(update.message)

async def callback_botoes(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    chat_id = query.message.chat_id
    acao = query.data
    msg  = query.message

    # ─── Atualizar menu (deve vir ANTES do startswith) ──────
    if acao == "atualizar_menu":
        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton("📅 Hoje",           callback_data="atualizar_hoje")],
            [InlineKeyboardButton("📅 Ontem",          callback_data="atualizar_ontem")],
            [InlineKeyboardButton("📅 Últimos 7 dias", callback_data="atualizar_7dias")],
            [InlineKeyboardButton("📅 Mês atual",      callback_data="atualizar_mes")],
        ])
        await msg.reply_text(
            f"🔄 {b('ATUALIZAR DADOS')}\n\nQual período deseja buscar agora?",
            parse_mode="HTML", reply_markup=kb
        )
        return

    # ─── Atualizar: período escolhido ───────────────────────
    if acao.startswith("atualizar_"):
        from datetime import datetime, timedelta
        hoje   = datetime.now()
        ontem  = hoje - timedelta(days=1)
        fmt    = "%d/%m/%Y"

        periodos = {
            "atualizar_hoje":   (hoje.strftime(fmt),  hoje.strftime(fmt),  "hoje"),
            "atualizar_ontem":  (ontem.strftime(fmt), ontem.strftime(fmt), "ontem"),
            "atualizar_7dias":  ((hoje - timedelta(days=7)).strftime(fmt), hoje.strftime(fmt), "últimos 7 dias"),
            "atualizar_mes":    (hoje.strftime("01/%m/%Y"), hoje.strftime(fmt), f"mês de {hoje.strftime('%B')}"),
        }

        if acao in periodos:
            ini, fim, label = periodos[acao]
            await executar_atualizacao(msg, chat_id, ini, fim, label)
        return
        modo = acao.split("_")[1]  # 'exato' ou 'estoque'
        d = dados_usuario.get(chat_id, {})
        produtos = d.get("produtos")
        if produtos is None:
            await msg.reply_text("📎 Envie o arquivo de Produtos primeiro.")
            return
        label = "exata do que saiu" if modo == "exato" else "com estoque de segurança (+30%)"
        await msg.reply_text(f"⏳ Gerando lista de reposição {label}...")
        blocos = bloco_reposicao(produtos, modo)
        for bloco in blocos:
            await enviar(msg, bloco)
        aguardando_dias.pop(chat_id, None)
        await abrir_menu(msg)
        return

    cmds = {
        "briefing":   comando_briefing,
        "produtos":   comando_produtos,
        "categorias": comando_categorias,
        "pagamentos": comando_pagamentos,
        "semana":     comando_semana,
        "pico":       comando_pico,
        "alertas":    comando_alertas,
        "reposicao":  comando_reposicao,
    }

    if acao == "briefing":
        await msg.reply_text("⏳ Gerando briefing completo...")
        await fluxo_briefing(msg, chat_id)
        return

    fake = type("U", (), {"message": msg, "effective_chat": type("C", (), {"id": chat_id})()})()
    if acao in cmds:
        await cmds[acao](fake, None)

# ─── MAIN — ver ao final do arquivo ─────────────────────────


# ─── INICIALIZAÇÃO COM SCHEDULER ─────────────────────────────
# Substitui o main() original para incluir o agendador automático

import asyncio as _asyncio

def main():
    garantir_browser()
    from scheduler import iniciar_scheduler

    app = ApplicationBuilder().token(TELEGRAM_TOKEN).post_init(configurar_menu).build()
    app.add_handler(CommandHandler("start",      start))
    app.add_handler(CommandHandler("menu",       comando_menu))
    app.add_handler(CommandHandler("briefing",   comando_briefing))
    app.add_handler(CommandHandler("produtos",   comando_produtos))
    app.add_handler(CommandHandler("categorias", comando_categorias))
    app.add_handler(CommandHandler("pagamentos", comando_pagamentos))
    app.add_handler(CommandHandler("semana",     comando_semana))
    app.add_handler(CommandHandler("pico",       comando_pico))
    app.add_handler(CommandHandler("alertas",    comando_alertas))
    app.add_handler(CommandHandler("reposicao",  comando_reposicao))
    app.add_handler(CommandHandler("atualizar",  comando_atualizar))
    app.add_handler(MessageHandler(filters.Document.ALL, receber_arquivo))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, mensagem_livre))
    app.add_handler(CallbackQueryHandler(callback_botoes))

    # Inicia o agendador junto com o bot
    iniciar_scheduler()

    print("🤖 MercadoBot rodando com briefing automático às 07:00...")
    app.run_polling()

if __name__ == "__main__":
    main()
