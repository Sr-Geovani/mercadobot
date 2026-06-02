"""
scheduler.py — Briefing diário multi-usuário
Roda às 7h, busca todos os usuários ativos e envia briefing com suas credenciais.
"""
import asyncio
import logging
import os
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
from pathlib import Path

import pandas as pd
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from telegram import Bot, InlineKeyboardMarkup, InlineKeyboardButton

logger         = logging.getLogger(__name__)
TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN")
BRASILIA       = ZoneInfo("America/Sao_Paulo")
HORARIO_HORA   = int(os.environ.get("BRIEFING_HORA",   "7"))
HORARIO_MINUTO = int(os.environ.get("BRIEFING_MINUTO", "0"))


def b(t): return f"<b>{t}</b>"
def i(t): return f"<i>{t}</i>"


async def briefing_usuario(bot: Bot, usuario: dict):
    """Executa o briefing completo para um usuário específico."""
    chat_id   = usuario["chat_id"]
    pdv_email = usuario.get("pdv_email")
    pdv_senha = usuario.get("pdv_senha")
    nome      = usuario.get("nome", "Operador")

    if not pdv_email or not pdv_senha:
        logger.warning(f"Usuário {chat_id} sem credenciais PDV — pulando.")
        return

    logger.info(f"Iniciando briefing para {chat_id} ({pdv_email})")

    try:
        await bot.send_message(
            chat_id=chat_id,
            text=f"☀️ {b('Bom dia, ' + nome + '!')} Gerando seu briefing de ontem...",
            parse_mode="HTML"
        )

        # Importa o scraper e processa com as credenciais do usuário
        import os as _os
        _os.environ["PDV_EMAIL"] = pdv_email
        _os.environ["PDV_SENHA"] = pdv_senha

        from scraper import baixar_relatorios
        loop = asyncio.get_event_loop()
        path_vendas, path_produtos = await loop.run_in_executor(
            None, baixar_relatorios
        )

        vendas   = pd.read_excel(path_vendas)
        produtos = pd.read_excel(path_produtos)

        # Normaliza dados
        from bot import normalizar_vendas, normalizar_produtos, bloco_faturamento, \
            bloco_categorias, bloco_pagamentos, bloco_semanal, \
            g_faturamento, g_categorias, g_pagamentos, g_semanal, \
            insight_ia, resumo_dados, kb_menu

        vendas   = normalizar_vendas(vendas)
        produtos = normalizar_produtos(produtos)

        if len(vendas) == 0:
            await bot.send_message(
                chat_id=chat_id,
                text=(
                    f"⚠️ Não encontrei vendas para ontem.\n\n"
                    f"Se houve vendas, use {b('🔄 Atualizar dados agora')} para buscar manualmente."
                ),
                parse_mode="HTML"
            )
            return

        # Envia blocos separados
        await bot.send_message(chat_id=chat_id, text=bloco_faturamento(vendas), parse_mode="HTML")
        await bot.send_photo(chat_id=chat_id, photo=g_faturamento(vendas))

        await bot.send_message(chat_id=chat_id, text=bloco_categorias(produtos), parse_mode="HTML")
        await bot.send_photo(chat_id=chat_id, photo=g_categorias(produtos))

        await bot.send_message(chat_id=chat_id, text=bloco_pagamentos(vendas), parse_mode="HTML")
        await bot.send_photo(chat_id=chat_id, photo=g_pagamentos(vendas))

        await bot.send_message(chat_id=chat_id, text=bloco_semanal(vendas), parse_mode="HTML")
        await bot.send_photo(chat_id=chat_id, photo=g_semanal(vendas))

        # Insight IA aleatório
        from bot import resumo_dados as _resumo
        ctx = (
            f"VENDAS: {len(vendas)} transações, R$ {vendas['valor'].sum():.2f} total\n"
            f"TICKET MÉDIO: R$ {vendas['valor'].mean():.2f}\n"
            f"CANCELAMENTOS: R$ {vendas['ValorItensCancelados'].sum():.2f}"
        )
        insight = await insight_ia(ctx)
        await bot.send_message(
            chat_id=chat_id,
            text=f"💡 {b('INSIGHT DO DIA')}\n\n{insight}",
            parse_mode="HTML"
        )

        # Menu
        await bot.send_message(
            chat_id=chat_id,
            text="O que deseja analisar agora?",
            reply_markup=kb_menu()
        )

        logger.info(f"Briefing enviado com sucesso para {chat_id}")

    except Exception as e:
        logger.error(f"Erro no briefing do usuário {chat_id}: {e}")
        await bot.send_message(
            chat_id=chat_id,
            text=(
                f"⚠️ Não consegui gerar o briefing automático hoje.\n\n"
                f"Possíveis causas:\n"
                f"• PDV Legal fora do ar\n"
                f"• Instabilidade na conexão\n\n"
                f"Use {b('🔄 Atualizar dados agora')} para tentar manualmente."
            ),
            parse_mode="HTML"
        )


async def enviar_briefing_automatico():
    """Busca todos os usuários ativos e envia o briefing para cada um."""
    from database import listar_usuarios_ativos

    agora    = datetime.now(BRASILIA)
    usuarios = await listar_usuarios_ativos()

    if not usuarios:
        logger.info("Nenhum usuário ativo para o briefing.")
        return

    logger.info(f"Briefing automático — {agora:%d/%m/%Y %H:%M} — {len(usuarios)} usuário(s)")
    bot = Bot(token=TELEGRAM_TOKEN)

    for usuario in usuarios:
        try:
            await briefing_usuario(bot, usuario)
            # Pequena pausa entre usuários para não sobrecarregar o PDV Legal
            await asyncio.sleep(10)
        except Exception as e:
            logger.error(f"Erro no briefing do usuário {usuario['chat_id']}: {e}")


def iniciar_scheduler():
    """Inicia o agendador diário."""
    scheduler = AsyncIOScheduler(timezone="America/Sao_Paulo")
    scheduler.add_job(
        enviar_briefing_automatico,
        trigger="cron",
        hour=HORARIO_HORA,
        minute=HORARIO_MINUTO,
        id="briefing_diario",
        replace_existing=True,
    )
    scheduler.start()
    logger.info(f"Briefing agendado para {HORARIO_HORA:02d}:{HORARIO_MINUTO:02d} (Brasília)")
    return scheduler
