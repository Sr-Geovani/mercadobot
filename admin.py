"""
admin.py — Painel administrativo via Telegram
Só acessível pelo chat_id do dono definido em ADMIN_CHAT_ID.
"""
import os
import logging
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes, CommandHandler, CallbackQueryHandler

logger   = logging.getLogger(__name__)
BRASILIA = ZoneInfo("America/Sao_Paulo")

# Seu chat_id pessoal — defina no Railway como variável ADMIN_CHAT_ID
ADMIN_CHAT_ID = int(os.environ.get("ADMIN_CHAT_ID", "0"))

def b(t): return f"<b>{t}</b>"
def i(t): return f"<i>{t}</i>"
def c(t): return f"<code>{t}</code>"

# Custo estimado por operação (USD)
CUSTO_BRIEFING    = 0.015   # briefing completo (5 chamadas IA)
CUSTO_CONSULTA    = 0.003   # análise avulsa (1 chamada IA)
CUSTO_SCRAPER     = 0.002   # playwright por execução (energia Railway)
USD_BRL           = 5.80    # taxa aproximada


def is_admin(chat_id: int) -> bool:
    return chat_id == ADMIN_CHAT_ID


def kb_admin():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("👥 Usuários",         callback_data="admin_usuarios"),
         InlineKeyboardButton("💰 Receita",          callback_data="admin_receita")],
        [InlineKeyboardButton("💸 Custos",           callback_data="admin_custos"),
         InlineKeyboardButton("⚠️ Churn Risk",       callback_data="admin_churn")],
        [InlineKeyboardButton("📊 Visão Geral",      callback_data="admin_overview")],
    ])


async def cmd_admin(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    if not is_admin(chat_id):
        return  # Silêncio total para não-admins

    from database import listar_usuarios_ativos, get_pool
    pool = await get_pool()

    # Totais rápidos
    async with pool.acquire() as conn:
        total      = await conn.fetchval("SELECT COUNT(*) FROM usuarios")
        ativos     = await conn.fetchval("SELECT COUNT(*) FROM usuarios WHERE status = 'ativo'")
        trial      = await conn.fetchval("SELECT COUNT(*) FROM usuarios WHERE status = 'trial'")
        cancelados = await conn.fetchval("SELECT COUNT(*) FROM usuarios WHERE status = 'cancelado'")
        pendentes  = await conn.fetchval("SELECT COUNT(*) FROM usuarios WHERE status = 'pendente'")

    mrr = ativos * 29.90

    await update.message.reply_text(
        f"🛡 {b('PAINEL ADMIN — MercadoBot')}\n\n"
        f"👥 Total cadastros: {b(str(total))}\n"
        f"✅ Ativos: {b(str(ativos))}\n"
        f"🎁 Trial: {b(str(trial))}\n"
        f"❌ Cancelados: {b(str(cancelados))}\n"
        f"⏳ Pendentes (sem pagar): {b(str(pendentes))}\n\n"
        f"💰 MRR estimado: {b(f'R$ {mrr:,.2f}')}\n\n"
        f"Escolha uma opção:",
        parse_mode="HTML",
        reply_markup=kb_admin()
    )


async def admin_usuarios(msg, pool):
    """Lista todos os usuários com status e data."""
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT chat_id, nome, nome_mercadinho, email, pdv_email, status, trial_fim, assinatura_fim, criado_em "
            "FROM usuarios ORDER BY criado_em DESC LIMIT 20"
        )

    if not rows:
        await msg.reply_text("Nenhum usuário cadastrado ainda.")
        return

    agora  = datetime.now(BRASILIA)
    linhas = [f"👥 {b('USUÁRIOS CADASTRADOS')}\n"]

    status_emoji = {
        "ativo": "✅", "trial": "🎁", "pendente": "⏳",
        "cancelado": "❌", "bloqueado": "🔒"
    }

    for r in rows:
        emoji = status_emoji.get(r["status"], "❓")
        nome  = r["nome_mercadinho"] or r["nome"] or "—"
        email = r["pdv_email"] or r["email"] or "—"

        # Dias restantes
        dias_info = ""
        if r["status"] == "trial" and r["trial_fim"]:
            fim  = datetime.fromisoformat(r["trial_fim"])
            dias = (fim - agora).days
            dias_info = f" · {dias}d restantes"
        elif r["status"] == "ativo" and r["assinatura_fim"]:
            fim  = datetime.fromisoformat(r["assinatura_fim"])
            dias = (fim - agora).days
            dias_info = f" · renova em {dias}d"

        linhas.append(f"{emoji} {b(nome)}{dias_info}")
        linhas.append(f"   {c(email)}")
        linhas.append(f"   ID: {r['chat_id']}\n")

    await msg.reply_text("\n".join(linhas), parse_mode="HTML")


async def admin_receita(msg, pool):
    """Visão financeira — MRR, ARR, projeções."""
    async with pool.acquire() as conn:
        ativos = await conn.fetchval("SELECT COUNT(*) FROM usuarios WHERE status = 'ativo'")
        trial  = await conn.fetchval("SELECT COUNT(*) FROM usuarios WHERE status = 'trial'")
        total  = await conn.fetchval("SELECT COUNT(*) FROM usuarios")

        # Cadastros nos últimos 30 dias
        data_corte = (datetime.now(BRASILIA) - timedelta(days=30)).isoformat()
        novos_30d  = await conn.fetchval(
            "SELECT COUNT(*) FROM usuarios WHERE criado_em > $1", data_corte
        )

    mrr        = ativos * 29.90
    arr        = mrr * 12
    potencial  = (ativos + trial) * 29.90
    ticket     = 29.90

    linhas = [f"💰 {b('VISÃO FINANCEIRA')}\n"]
    linhas.append(f"📊 Clientes ativos: {b(str(ativos))}")
    linhas.append(f"🎁 Em trial: {b(str(trial))}")
    linhas.append(f"💵 MRR atual: {b(f'R$ {mrr:,.2f}')}")
    linhas.append(f"📈 ARR projetado: {b(f'R$ {arr:,.2f}')}")
    linhas.append(f"🎯 MRR potencial (conv. trial): {b(f'R$ {potencial:,.2f}')}")
    linhas.append(f"🆕 Novos cadastros (30d): {b(str(novos_30d))}")
    linhas.append(f"\n{i('Ticket médio: R$ 29,90/mês')}")

    await msg.reply_text("\n".join(linhas), parse_mode="HTML")


async def admin_custos(msg, pool):
    """Estimativa de custos por usuário e total."""
    async with pool.acquire() as conn:
        ativos = await conn.fetchval(
            "SELECT COUNT(*) FROM usuarios WHERE status IN ('ativo', 'trial')"
        )

    # Cálculo mensal estimado
    briefings_mes    = ativos * 30 * CUSTO_BRIEFING
    consultas_mes    = ativos * 20 * CUSTO_CONSULTA
    scrapers_mes     = ativos * 50 * CUSTO_SCRAPER   # briefing + consultas + alertas
    railway_mes      = 10.00                          # USD fixo Railway
    total_usd        = briefings_mes + consultas_mes + scrapers_mes + railway_mes
    total_brl        = total_usd * USD_BRL

    custo_por_user   = (total_brl / ativos) if ativos else 0
    receita_mes      = ativos * 29.90
    margem           = ((receita_mes - total_brl) / receita_mes * 100) if receita_mes else 0

    linhas = [f"💸 {b('ESTIMATIVA DE CUSTOS')}\n"]
    linhas.append(f"👥 Usuários ativos/trial: {b(str(ativos))}\n")
    linhas.append(f"🤖 IA (briefings): {i(f'US$ {briefings_mes:.2f}/mês')}")
    linhas.append(f"🤖 IA (consultas): {i(f'US$ {consultas_mes:.2f}/mês')}")
    linhas.append(f"🌐 Scraper: {i(f'US$ {scrapers_mes:.2f}/mês')}")
    linhas.append(f"🖥 Railway: {i(f'US$ {railway_mes:.2f}/mês')}")
    linhas.append(f"\n💵 Total: {b(f'US$ {total_usd:.2f}')} = {b(f'R$ {total_brl:.2f}')}")
    linhas.append(f"👤 Custo/usuário: {b(f'R$ {custo_por_user:.2f}')}")
    linhas.append(f"💰 Receita: {b(f'R$ {receita_mes:.2f}')}")
    linhas.append(f"📈 Margem bruta: {b(f'{margem:.0f}%')}")

    if margem < 70:
        linhas.append(f"\n⚠️ {i('Margem abaixo de 70% — considere ajustar o preço.')}")
    else:
        linhas.append(f"\n✅ {i('Margem saudável.')}")

    await msg.reply_text("\n".join(linhas), parse_mode="HTML")


async def admin_churn(msg, pool):
    """Usuários em risco de churn — trial expirando, sem uso recente."""
    agora = datetime.now(BRASILIA)
    em_2d = (agora + timedelta(days=2)).isoformat()

    async with pool.acquire() as conn:
        # Trial expirando em 2 dias
        expirando = await conn.fetch(
            "SELECT nome, nome_mercadinho, pdv_email, trial_fim FROM usuarios "
            "WHERE status = 'trial' AND trial_fim <= $1",
            em_2d
        )
        # Pendentes há mais de 1 dia
        ontem = (agora - timedelta(days=1)).isoformat()
        pendentes = await conn.fetch(
            "SELECT nome, nome_mercadinho, pdv_email, criado_em FROM usuarios "
            "WHERE status = 'pendente' AND criado_em < $1",
            ontem
        )

    linhas = [f"⚠️ {b('CHURN RISK')}\n"]

    if expirando:
        linhas.append(f"⏰ {b('Trial expirando em 2 dias:')}")
        for r in expirando:
            nome = r["nome_mercadinho"] or r["nome"] or "—"
            linhas.append(f"   • {nome} — {r['pdv_email']}")
        linhas.append("")

    if pendentes:
        linhas.append(f"💳 {b('Cadastraram mas não pagaram:')}")
        for r in pendentes:
            nome = r["nome_mercadinho"] or r["nome"] or "—"
            linhas.append(f"   • {nome} — {r['pdv_email']}")
        linhas.append("")

    if not expirando and not pendentes:
        linhas.append("✅ Nenhum risco de churn identificado agora.")

    await msg.reply_text("\n".join(linhas), parse_mode="HTML")


async def admin_overview(msg, pool):
    """Visão executiva consolidada."""
    agora = datetime.now(BRASILIA)
    async with pool.acquire() as conn:
        ativos     = await conn.fetchval("SELECT COUNT(*) FROM usuarios WHERE status = 'ativo'")
        trial      = await conn.fetchval("SELECT COUNT(*) FROM usuarios WHERE status = 'trial'")
        cancelados = await conn.fetchval("SELECT COUNT(*) FROM usuarios WHERE status = 'cancelado'")
        total      = await conn.fetchval("SELECT COUNT(*) FROM usuarios")
        data_corte = (agora - timedelta(days=7)).isoformat()
        novos_7d   = await conn.fetchval(
            "SELECT COUNT(*) FROM usuarios WHERE criado_em > $1", data_corte
        )

    mrr          = ativos * 29.90
    tx_conv      = (ativos / trial * 100) if trial else 0
    tx_churn     = (cancelados / total * 100) if total else 0

    linhas = [
        f"📊 {b('VISÃO GERAL — ' + agora.strftime('%d/%m/%Y'))}\n",
        f"✅ Ativos: {b(str(ativos))}",
        f"🎁 Trial: {b(str(trial))}",
        f"❌ Cancelados: {b(str(cancelados))}",
        f"🆕 Novos (7d): {b(str(novos_7d))}",
        f"\n💰 MRR: {b(f'R$ {mrr:,.2f}')}",
        f"🔄 Taxa conversão trial→pago: {b(f'{tx_conv:.0f}%')}",
        f"📉 Taxa churn acumulada: {b(f'{tx_churn:.0f}%')}",
    ]
    await msg.reply_text("\n".join(linhas), parse_mode="HTML")


async def callback_admin(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    chat_id = query.message.chat_id

    if not is_admin(chat_id):
        return

    from database import get_pool
    pool = await get_pool()
    msg  = query.message
    acao = query.data

    mapa = {
        "admin_usuarios": admin_usuarios,
        "admin_receita":  admin_receita,
        "admin_custos":   admin_custos,
        "admin_churn":    admin_churn,
        "admin_overview": admin_overview,
    }

    if acao in mapa:
        await mapa[acao](msg, pool)
        await msg.reply_text("🛡 Painel Admin:", reply_markup=kb_admin())
