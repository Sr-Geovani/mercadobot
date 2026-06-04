"""
admin.py — Painel administrativo via Telegram
Só acessível pelo chat_id do dono definido em ADMIN_CHAT_ID.
"""
import os
import logging
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from telegram import Update, Bot, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes

logger   = logging.getLogger(__name__)
BRASILIA = ZoneInfo("America/Sao_Paulo")

ADMIN_CHAT_ID = int(os.environ.get("ADMIN_CHAT_ID", "0"))

CUSTO_BRIEFING = 0.015
CUSTO_CONSULTA = 0.003
CUSTO_SCRAPER  = 0.002
USD_BRL        = 5.80

def b(t): return f"<b>{t}</b>"
def i(t): return f"<i>{t}</i>"
def c(t): return f"<code>{t}</code>"

def is_admin(chat_id: int) -> bool:
    return chat_id == ADMIN_CHAT_ID

def kb_admin():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("👥 Usuários",          callback_data="admin_usuarios"),
         InlineKeyboardButton("💰 Receita",           callback_data="admin_receita")],
        [InlineKeyboardButton("💸 Custos",            callback_data="admin_custos"),
         InlineKeyboardButton("⚠️ Churn Risk",        callback_data="admin_churn")],
        [InlineKeyboardButton("❌ Cancelados",         callback_data="admin_cancelados"),
         InlineKeyboardButton("🆕 Novos",             callback_data="admin_novos")],
        [InlineKeyboardButton("📣 Enviar notificação", callback_data="admin_notificar")],
        [InlineKeyboardButton("📊 Visão Geral",       callback_data="admin_overview")],
    ])


async def cmd_admin(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    if not is_admin(chat_id):
        return

    from database import get_pool
    pool = await get_pool()

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
        f"⏳ Pendentes: {b(str(pendentes))}\n\n"
        f"💰 MRR estimado: {b(f'R$ {mrr:,.2f}')}\n\n"
        f"Escolha uma opção:",
        parse_mode="HTML",
        reply_markup=kb_admin()
    )


async def admin_usuarios(msg, pool):
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT chat_id, nome, nome_mercadinho, pdv_email, status, trial_fim, assinatura_fim, criado_em "
            "FROM usuarios ORDER BY criado_em DESC LIMIT 20"
        )

    if not rows:
        await msg.reply_text("Nenhum usuário cadastrado ainda.")
        return

    agora = datetime.now(BRASILIA)
    status_emoji = {"ativo": "✅", "trial": "🎁", "pendente": "⏳", "cancelado": "❌", "bloqueado": "🔒"}
    linhas = [f"👥 {b('USUÁRIOS CADASTRADOS')}\n"]

    for r in rows:
        emoji = status_emoji.get(r["status"], "❓")
        nome  = r["nome_mercadinho"] or r["nome"] or "—"
        email = r["pdv_email"] or "—"
        dias_info = ""
        if r["status"] == "trial" and r["trial_fim"]:
            dias = (datetime.fromisoformat(r["trial_fim"]) - agora).days
            dias_info = f" · {dias}d restantes"
        elif r["status"] == "ativo" and r["assinatura_fim"]:
            dias = (datetime.fromisoformat(r["assinatura_fim"]) - agora).days
            dias_info = f" · renova em {dias}d"
        linhas.append(f"{emoji} {b(nome)}{dias_info}")
        linhas.append(f"   {c(email)} | ID: {r['chat_id']}\n")

    await msg.reply_text("\n".join(linhas), parse_mode="HTML")


async def admin_cancelados(msg, pool):
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT chat_id, nome, nome_mercadinho, pdv_email, status, atualizado_em "
            "FROM usuarios WHERE status IN ('cancelado', 'bloqueado') "
            "ORDER BY atualizado_em DESC LIMIT 20"
        )

    if not rows:
        await msg.reply_text("Nenhum usuário cancelado.")
        return

    linhas = [f"❌ {b('CANCELADOS / BLOQUEADOS')}\n"]
    for r in rows:
        nome   = r["nome_mercadinho"] or r["nome"] or "—"
        email  = r["pdv_email"] or "—"
        status = "❌ Cancelado" if r["status"] == "cancelado" else "🔒 Bloqueado"
        when   = r["atualizado_em"][:10] if r["atualizado_em"] else "—"
        linhas.append(f"{status} — {b(nome)}")
        linhas.append(f"   {c(email)} | ID: {r['chat_id']} | em: {when}\n")

    await msg.reply_text("\n".join(linhas), parse_mode="HTML")


async def admin_novos(msg, pool):
    agora      = datetime.now(BRASILIA)
    data_corte = (agora - timedelta(days=7)).isoformat()

    async with pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT chat_id, nome, nome_mercadinho, pdv_email, status, criado_em "
            "FROM usuarios WHERE criado_em > $1 ORDER BY criado_em DESC",
            data_corte
        )

    if not rows:
        await msg.reply_text("Nenhum novo cadastro nos últimos 7 dias.")
        return

    status_emoji = {"ativo": "✅", "trial": "🎁", "pendente": "⏳", "cancelado": "❌", "bloqueado": "🔒"}
    linhas = [f"🆕 {b('NOVOS CADASTROS — últimos 7 dias')}\n"]

    for r in rows:
        nome  = r["nome_mercadinho"] or r["nome"] or "—"
        email = r["pdv_email"] or "—"
        emoji = status_emoji.get(r["status"], "❓")
        when  = r["criado_em"][:10] if r["criado_em"] else "—"
        linhas.append(f"{emoji} {b(nome)} — {r['status']}")
        linhas.append(f"   {c(email)} | ID: {r['chat_id']} | em: {when}\n")

    await msg.reply_text("\n".join(linhas), parse_mode="HTML")


async def admin_receita(msg, pool):
    async with pool.acquire() as conn:
        ativos    = await conn.fetchval("SELECT COUNT(*) FROM usuarios WHERE status = 'ativo'")
        trial     = await conn.fetchval("SELECT COUNT(*) FROM usuarios WHERE status = 'trial'")
        total     = await conn.fetchval("SELECT COUNT(*) FROM usuarios")
        data_30d  = (datetime.now(BRASILIA) - timedelta(days=30)).isoformat()
        novos_30d = await conn.fetchval("SELECT COUNT(*) FROM usuarios WHERE criado_em > $1", data_30d)

    mrr       = ativos * 29.90
    arr       = mrr * 12
    potencial = (ativos + trial) * 29.90

    linhas = [f"💰 {b('VISÃO FINANCEIRA')}\n"]
    linhas.append(f"📊 Clientes ativos: {b(str(ativos))}")
    linhas.append(f"🎁 Em trial: {b(str(trial))}")
    linhas.append(f"💵 MRR atual: {b(f'R$ {mrr:,.2f}')}")
    linhas.append(f"📈 ARR projetado: {b(f'R$ {arr:,.2f}')}")
    linhas.append(f"🎯 MRR potencial (conv. trial): {b(f'R$ {potencial:,.2f}')}")
    linhas.append(f"🆕 Novos cadastros (30d): {b(str(novos_30d))}")
    await msg.reply_text("\n".join(linhas), parse_mode="HTML")


async def admin_custos(msg, pool):
    async with pool.acquire() as conn:
        ativos = await conn.fetchval(
            "SELECT COUNT(*) FROM usuarios WHERE status IN ('ativo', 'trial')"
        )

    briefings_mes = ativos * 30 * CUSTO_BRIEFING
    consultas_mes = ativos * 20 * CUSTO_CONSULTA
    scrapers_mes  = ativos * 50 * CUSTO_SCRAPER
    railway_mes   = 10.00
    total_usd     = briefings_mes + consultas_mes + scrapers_mes + railway_mes
    total_brl     = total_usd * USD_BRL
    custo_user    = (total_brl / ativos) if ativos else 0
    receita_mes   = ativos * 29.90
    margem        = ((receita_mes - total_brl) / receita_mes * 100) if receita_mes else 0

    linhas = [f"💸 {b('ESTIMATIVA DE CUSTOS')}\n"]
    linhas.append(f"👥 Usuários ativos/trial: {b(str(ativos))}\n")
    linhas.append(f"🤖 IA (briefings): {i(f'US$ {briefings_mes:.2f}/mês')}")
    linhas.append(f"🤖 IA (consultas): {i(f'US$ {consultas_mes:.2f}/mês')}")
    linhas.append(f"🌐 Scraper: {i(f'US$ {scrapers_mes:.2f}/mês')}")
    linhas.append(f"🖥 Railway: {i(f'US$ {railway_mes:.2f}/mês')}")
    linhas.append(f"\n💵 Total: {b(f'US$ {total_usd:.2f}')} = {b(f'R$ {total_brl:.2f}')}")
    linhas.append(f"👤 Custo/usuário: {b(f'R$ {custo_user:.2f}')}")
    linhas.append(f"💰 Receita: {b(f'R$ {receita_mes:.2f}')}")
    linhas.append(f"📈 Margem bruta: {b(f'{margem:.0f}%')}")
    await msg.reply_text("\n".join(linhas), parse_mode="HTML")


async def admin_churn(msg, pool):
    agora = datetime.now(BRASILIA)
    em_2d = (agora + timedelta(days=2)).isoformat()
    ontem = (agora - timedelta(days=1)).isoformat()

    async with pool.acquire() as conn:
        expirando = await conn.fetch(
            "SELECT nome, nome_mercadinho, pdv_email, trial_fim FROM usuarios "
            "WHERE status = 'trial' AND trial_fim <= $1", em_2d
        )
        pendentes = await conn.fetch(
            "SELECT nome, nome_mercadinho, pdv_email, criado_em FROM usuarios "
            "WHERE status = 'pendente' AND criado_em < $1", ontem
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
    agora = datetime.now(BRASILIA)
    async with pool.acquire() as conn:
        ativos     = await conn.fetchval("SELECT COUNT(*) FROM usuarios WHERE status = 'ativo'")
        trial      = await conn.fetchval("SELECT COUNT(*) FROM usuarios WHERE status = 'trial'")
        cancelados = await conn.fetchval("SELECT COUNT(*) FROM usuarios WHERE status = 'cancelado'")
        total      = await conn.fetchval("SELECT COUNT(*) FROM usuarios")
        data_7d    = (agora - timedelta(days=7)).isoformat()
        novos_7d   = await conn.fetchval("SELECT COUNT(*) FROM usuarios WHERE criado_em > $1", data_7d)

    mrr      = ativos * 29.90
    tx_conv  = (ativos / trial * 100) if trial else 0
    tx_churn = (cancelados / total * 100) if total else 0

    linhas = [
        f"📊 {b('VISÃO GERAL — ' + agora.strftime('%d/%m/%Y'))}\n",
        f"✅ Ativos: {b(str(ativos))}",
        f"🎁 Trial: {b(str(trial))}",
        f"❌ Cancelados: {b(str(cancelados))}",
        f"🆕 Novos (7d): {b(str(novos_7d))}",
        f"\n💰 MRR: {b(f'R$ {mrr:,.2f}')}",
        f"🔄 Conversão trial→pago: {b(f'{tx_conv:.0f}%')}",
        f"📉 Churn acumulado: {b(f'{tx_churn:.0f}%')}",
    ]
    await msg.reply_text("\n".join(linhas), parse_mode="HTML")


async def admin_notificar(msg, pool):
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("📣 Todos os ativos e trial",   callback_data="admin_notif_todos")],
        [InlineKeyboardButton("🎁 Somente trial",             callback_data="admin_notif_trial")],
        [InlineKeyboardButton("✅ Somente assinantes ativos",  callback_data="admin_notif_ativos")],
        [InlineKeyboardButton("❌ Cancelados / Bloqueados",    callback_data="admin_notif_cancelados")],
    ])
    await msg.reply_text(
        f"📣 {b('ENVIAR NOTIFICAÇÃO')}\n\nPara qual grupo deseja enviar?",
        parse_mode="HTML",
        reply_markup=kb
    )


async def admin_executar_notificacao(msg, pool, grupo: str, texto: str):
    filtros = {
        "todos":      "status IN ('trial', 'ativo')",
        "trial":      "status = 'trial'",
        "ativos":     "status = 'ativo'",
        "cancelados": "status IN ('cancelado', 'bloqueado')",
    }
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            f"SELECT chat_id FROM usuarios WHERE {filtros.get(grupo, 'status IN (\'trial\', \'ativo\')')}"
        )

    if not rows:
        await msg.reply_text("Nenhum usuário encontrado para esse grupo.")
        return

    bot      = Bot(token=os.environ.get("TELEGRAM_TOKEN"))
    enviadas = 0
    falhas   = 0

    await msg.reply_text(f"⏳ Enviando para {len(rows)} usuário(s)...")

    for r in rows:
        try:
            await bot.send_message(chat_id=r["chat_id"], text=texto, parse_mode="HTML")
            enviadas += 1
        except Exception as e:
            falhas += 1
            logger.warning(f"Falha ao notificar {r['chat_id']}: {e}")

    await msg.reply_text(
        f"✅ {b('Notificação enviada!')}\n\nEnviadas: {b(str(enviadas))}\nFalhas: {falhas}",
        parse_mode="HTML"
    )


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
        "admin_usuarios":   admin_usuarios,
        "admin_receita":    admin_receita,
        "admin_custos":     admin_custos,
        "admin_churn":      admin_churn,
        "admin_overview":   admin_overview,
        "admin_cancelados": admin_cancelados,
        "admin_novos":      admin_novos,
        "admin_notificar":  admin_notificar,
    }

    if acao in mapa:
        await mapa[acao](msg, pool)
        if acao != "admin_notificar":
            await msg.reply_text("🛡 Painel Admin:", reply_markup=kb_admin())
        return

    # Seleção do grupo para notificação
    if acao.startswith("admin_notif_"):
        grupo = acao.replace("admin_notif_", "")
        grupos_label = {
            "todos":      "Todos os ativos e trial",
            "trial":      "Somente trial",
            "ativos":     "Somente assinantes ativos",
            "cancelados": "Cancelados / Bloqueados",
        }
        context.user_data["admin_notif_grupo"]    = grupo
        context.user_data["admin_aguardando_notif"] = True
        await msg.reply_text(
            f"📣 Grupo: {b(grupos_label.get(grupo, grupo))}\n\n"
            f"Agora {b('envie a mensagem')} que deseja disparar.\n"
            f"Suporta HTML: {c('<b>negrito</b>')}, {c('<i>itálico</i>')}\n\n"
            f"{i('Digite a mensagem no chat:')}",
            parse_mode="HTML"
        )
        return
