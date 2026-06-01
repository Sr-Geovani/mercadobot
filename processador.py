"""
processador.py — Gera os blocos de mensagem e gráficos do briefing.
Usado tanto pelo bot.py (on-demand) quanto pelo scheduler.py (automático).
"""
import pandas as pd
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from io import BytesIO

# ─── PALETA ──────────────────────────────────────────────────
COR_BG      = "#0e0f11"
COR_SURFACE = "#1e2027"
COR_BORDA   = "#2a2d35"
COR_VERDE   = "#00e676"
COR_AZUL    = "#40c4ff"
COR_AMARELO = "#ffd740"
COR_ROXO    = "#ce93d8"
COR_TEXTO   = "#e8eaf0"
CORES       = [COR_VERDE, COR_AZUL, COR_AMARELO, COR_ROXO,
               "#ff8a65", "#80cbc4", "#fff176", "#ef9a9a"]


def b(t): return f"<b>{t}</b>"
def i(t): return f"<i>{t}</i>"
def c(t): return f"<code>{t}</code>"


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
    fig.savefig(buf, format="png", bbox_inches="tight",
                facecolor=fig.get_facecolor(), dpi=150)
    plt.close(fig)
    buf.seek(0)
    return buf


def g_faturamento(vendas: pd.DataFrame) -> BytesIO:
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


def g_categorias(produtos: pd.DataFrame) -> BytesIO:
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


def g_pagamentos(vendas: pd.DataFrame) -> BytesIO:
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


def bloco_faturamento(vendas: pd.DataFrame) -> str:
    total  = vendas["valor"].sum()
    ticket = vendas["valor"].mean()
    n      = len(vendas)
    cancel = vendas["ValorItensCancelados"].sum()
    pct_c  = (cancel/total*100) if total else 0
    filiais = vendas.groupby("nomeFilial").agg(
        fat=("valor","sum"), qtd=("valor","count"),
        tk=("valor","mean"), canc=("ValorItensCancelados","sum")
    )
    linhas = [f"📊 {b('FATURAMENTO DO PERÍODO')}\n",
              f"💰 Total: {b(f'R$ {total:,.2f}')}",
              f"🛒 Transações: {b(str(n))}",
              f"🎯 Ticket médio: {b(f'R$ {ticket:.2f}')}",
              f"⚠️ Cancelamentos: {c(f'R$ {cancel:,.2f}')} {i(f'({pct_c:.1f}%)')}\n"]
    for filial, row in filiais.iterrows():
        nome = filial.split()[-1].title()
        linhas += [f"📍 {b(nome)}",
                   f"   Faturamento: {b(f'R$ {row.fat:,.2f}')}",
                   f"   Vendas: {row.qtd} | Ticket: R$ {row.tk:.2f}",
                   f"   Cancelamentos: {c(f'R$ {row.canc:,.2f}')}\n"]
    return "\n".join(linhas)


def bloco_categorias(produtos: pd.DataFrame) -> str:
    grupos = produtos.groupby("grupo")["valor"].sum().sort_values(ascending=False)
    total  = grupos.sum()
    medalhas = ["🥇","🥈","🥉"]
    linhas = [f"🗂 {b('RECEITA POR CATEGORIA')}\n"]
    for idx, (grupo, val) in enumerate(grupos.items()):
        med = medalhas[idx] if idx < 3 else "•"
        linhas.append(f"{med} {grupo}: {b(f'R$ {val:,.2f}')} {i(f'({val/total*100:.1f}%)')}")
    return "\n".join(linhas)


def bloco_pagamentos(vendas: pd.DataFrame) -> str:
    mix = vendas.groupby("FormaRecebimento").agg(qtd=("valor","count"), total=("valor","sum"))
    mix["pct"] = (mix["qtd"]/mix["qtd"].sum()*100)
    icones = {"PIX":"🟢","DEBITO":"🔵","CREDITO":"🟡"}
    linhas = [f"💳 {b('MIX DE PAGAMENTO')}\n"]
    for forma, row in mix.sort_values("qtd", ascending=False).iterrows():
        ic = icones.get(forma.upper(),"⚪")
        linhas += [f"{ic} {b(forma)}: {row.qtd} transações ({b(f'{row.pct:.1f}%')})",
                   f"   Volume: R$ {row.total:,.2f}"]
    pix = mix.loc[mix.index.str.upper()=="PIX","pct"].values
    if len(pix) and pix[0] < 30:
        linhas.append(f"\n💡 {i('PIX abaixo de 30% — incentivar uso reduz taxas de maquininha.')}")
    return "\n".join(linhas)


def gerar_briefing_completo(vendas: pd.DataFrame,
                             produtos: pd.DataFrame) -> list:
    """
    Retorna lista de tuplas (tipo, conteudo):
    tipo = 'texto' | 'foto'
    Usado pelo scheduler para envio automático.
    """
    msgs = []
    msgs.append(("texto", bloco_faturamento(vendas)))
    msgs.append(("foto",  g_faturamento(vendas)))
    msgs.append(("texto", bloco_categorias(produtos)))
    msgs.append(("foto",  g_categorias(produtos)))
    msgs.append(("texto", bloco_pagamentos(vendas)))
    msgs.append(("foto",  g_pagamentos(vendas)))
    return msgs
