"""
scheduler.py — Agendador do briefing diário
Roda às 7h da manhã, baixa os relatórios e envia o briefing automático.
"""
import asyncio
import logging
import os
from datetime import datetime
from pathlib import Path

import pandas as pd
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from telegram import Bot

logger = logging.getLogger(__name__)

TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN")

# IDs dos chats que recebem o briefing automático
# Separados por vírgula na variável de ambiente CHAT_IDS
# Ex: CHAT_IDS=123456789,987654321
CHAT_IDS = [
    int(cid.strip())
    for cid in os.environ.get("CHAT_IDS", "").split(",")
    if cid.strip()
]

HORARIO_HORA   = int(os.environ.get("BRIEFING_HORA", "7"))
HORARIO_MINUTO = int(os.environ.get("BRIEFING_MINUTO", "0"))


async def enviar_briefing_automatico():
    """Baixa os relatórios e envia o briefing para todos os chats cadastrados."""
    if not CHAT_IDS:
        logger.warning("Nenhum CHAT_ID configurado. Defina a variável CHAT_IDS no Railway.")
        return

    logger.info(f"Iniciando briefing automático — {datetime.now():%d/%m/%Y %H:%M}")

    bot = Bot(token=TELEGRAM_TOKEN)

    # Notifica que está processando
    for chat_id in CHAT_IDS:
        await bot.send_message(
            chat_id=chat_id,
            text="⏳ Gerando seu briefing automático do dia anterior..."
        )

    try:
        # Importa aqui para evitar circular import com bot.py
        from scraper import baixar_relatorios
        from processador import gerar_briefing_completo

        path_vendas, path_produtos = baixar_relatorios()

        vendas   = pd.read_excel(path_vendas)
        produtos = pd.read_excel(path_produtos)

        mensagens = gerar_briefing_completo(vendas, produtos)

        for chat_id in CHAT_IDS:
            for tipo, conteudo in mensagens:
                if tipo == "texto":
                    await bot.send_message(
                        chat_id=chat_id,
                        text=conteudo,
                        parse_mode="HTML"
                    )
                elif tipo == "foto":
                    await bot.send_photo(chat_id=chat_id, photo=conteudo)
            # Menu após o briefing
            await bot.send_message(
                chat_id=chat_id,
                text="📋 Use o menu para explorar os dados detalhadamente.",
            )

        logger.info("Briefing automático enviado com sucesso.")

    except Exception as e:
        logger.error(f"Erro no briefing automático: {e}")
        for chat_id in CHAT_IDS:
            await bot.send_message(
                chat_id=chat_id,
                text=f"❌ Erro ao gerar briefing automático: {str(e)}\n\nVocê pode importar os arquivos manualmente."
            )


def iniciar_scheduler():
    """Inicia o agendador que dispara o briefing todo dia no horário configurado."""
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
    logger.info(f"Briefing agendado para {HORARIO_HORA:02d}:{HORARIO_MINUTO:02d} (horário de Brasília)")
    return scheduler
