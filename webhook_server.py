"""
webhook_server.py — Servidor HTTP para receber webhooks do Asaas.
Roda junto com o bot usando aiohttp.
"""
import logging
import os
from aiohttp import web
from database import atualizar_usuario, buscar_usuario
from pagamento import processar_webhook
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

logger   = logging.getLogger(__name__)
BRASILIA = ZoneInfo("America/Sao_Paulo")
WEBHOOK_SECRET = os.environ.get("WEBHOOK_SECRET", "mercadobot_webhook")

# Bot global — setado pelo main()
_bot = None

def set_bot(bot):
    global _bot
    _bot = bot


async def handle_webhook(request: web.Request) -> web.Response:
    """Processa eventos do Asaas."""
    try:
        payload = await request.json()
        logger.info(f"Webhook recebido: {payload.get('event')}")

        resultado = processar_webhook(payload)
        chat_id   = resultado["chat_id"]
        evento    = resultado["evento"]

        if not chat_id:
            return web.Response(text="ok")

        usuario = await buscar_usuario(chat_id)
        if not usuario:
            return web.Response(text="ok")

        if evento == "pagamento_confirmado":
            agora = datetime.now(BRASILIA)

            # Qualquer pagamento confirmado significa que a pessoa já é assinante —
            # nunca deixa o status como "trial" depois de um pagamento real.
            novo_status    = "ativo"
            assinatura_fim = (agora + timedelta(days=31)).isoformat()

            # Preserva trial_fim original só para referência histórica, se existir
            trial_fim = usuario.get("trial_fim")

            await atualizar_usuario(
                chat_id,
                status=novo_status,
                assinatura_fim=assinatura_fim,
                trial_usado=True
            )

            msg_trial = "Seu acesso está ativo.\n\n"
            if _bot:
                await _bot.send_message(
                    chat_id=chat_id,
                    text=(
                        f"✅ <b>Pagamento confirmado! Bem-vindo ao MercadoBot!</b>\n\n"
                        f"{msg_trial}"
                        f"Para começar, use /atualizar para buscar seus dados do PDV Legal.\n\n"
                        f"<b>Comandos disponíveis:</b>\n"
                        f"/briefing — resumo completo do período\n"
                        f"/atualizar — buscar dados por período\n"
                        f"/reposicao — lista de reposição inteligente\n"
                        f"/score — score de saúde da operação\n"
                        f"/projecao — projeção do mês\n"
                        f"/comparativo — comparativo entre unidades\n"
                        f"/alertas — alertas e pontos de atenção\n"
                        f"/configuracoes — atualizar credenciais\n"
                        f"/status — status da assinatura\n\n"
                        f"Ou use o /menu para ver todas as opções com um toque. 👇"
                    ),
                    parse_mode="HTML"
                )
            logger.info(f"Usuário {chat_id} reativado — status={novo_status}, trial_fim={trial_fim}.")

        elif evento == "pagamento_atrasado":
            if _bot:
                await _bot.send_message(
                    chat_id=chat_id,
                    text=(
                        "⚠️ <b>Pagamento em atraso</b>\n\n"
                        "Seu acesso ao MercadoBot será bloqueado em 3 dias.\n"
                        "Regularize para continuar usando."
                    ),
                    parse_mode="HTML"
                )

        elif evento in ("assinatura_cancelada", "pagamento_cancelado"):
            await atualizar_usuario(chat_id, status="cancelado")
            if _bot:
                await _bot.send_message(
                    chat_id=chat_id,
                    text=(
                        "😔 <b>Assinatura cancelada</b>\n\n"
                        "Seu acesso ao MercadoBot foi encerrado.\n"
                        "Use /assinar para reativar quando quiser."
                    ),
                    parse_mode="HTML"
                )

        return web.Response(text="ok")

    except Exception as e:
        logger.error(f"Erro no webhook: {e}")
        return web.Response(text="ok", status=200)


async def iniciar_servidor_webhook():
    """Inicia o servidor HTTP na porta 8080."""
    app = web.Application()
    app.router.add_post("/webhook/asaas", handle_webhook)
    app.router.add_get("/health", lambda r: web.Response(text="ok"))

    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", 8080)
    await site.start()
    logger.info("Servidor webhook rodando na porta 8080.")
    return runner
