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


async def buscar_usuario_por_asaas_id(asaas_cliente_id: str):
    """Fallback: busca usuário pelo customer ID do Asaas quando externalReference não veio no webhook."""
    if not asaas_cliente_id:
        return None
    from database import get_pool
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT * FROM usuarios WHERE asaas_id = $1", asaas_cliente_id
        )
        return dict(row) if row else None


async def handle_webhook(request: web.Request) -> web.Response:
    """Processa eventos do Asaas."""
    try:
        payload = await request.json()
        logger.info(f"Webhook recebido: {payload.get('event')}")

        resultado = processar_webhook(payload)
        chat_id   = resultado["chat_id"]
        customer  = resultado.get("customer")
        evento    = resultado["evento"]

        usuario = None
        if chat_id:
            usuario = await buscar_usuario(chat_id)

        # Fallback: se não achou pelo externalReference, tenta pelo customer (asaas_id)
        if not usuario and customer:
            usuario = await buscar_usuario_por_asaas_id(customer)
            if usuario:
                chat_id = usuario["chat_id"]
                logger.info(f"Webhook: resolvido via customer={customer} -> chat_id={chat_id}")

        if not usuario:
            logger.warning(
                f"Webhook: não foi possível identificar usuário "
                f"(externalReference={resultado['chat_id']}, customer={customer}, evento={evento})"
            )
            return web.Response(text="ok")

        if evento == "cartao_validado":
            # SUBSCRIPTION_CREATED — o cartão foi validado no Checkout, sem cobrança.
            # A cobrança real só ocorrerá no nextDueDate (fim do trial).
            # Aqui apenas liberamos o trial de 7 dias.
            agora     = datetime.now(BRASILIA)
            trial_fim = (agora + timedelta(days=7)).isoformat()
            asaas_subscription_id = resultado.get("asaas_id")

            campos_update = dict(status="trial", trial_fim=trial_fim, trial_usado=True)
            if asaas_subscription_id and asaas_subscription_id.startswith("sub_"):
                campos_update["assinatura_asaas_id"] = asaas_subscription_id

            await atualizar_usuario(chat_id, **campos_update)
            logger.info(f"Usuário {chat_id} — cartão validado, trial de 7 dias liberado até {trial_fim}.")

            if _bot:
                await _bot.send_message(
                    chat_id=chat_id,
                    text=(
                        f"✅ <b>Cartão validado! Seu trial de 7 dias começou agora.</b>\n\n"
                        f"Você não será cobrado até o fim do trial. "
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

        elif evento == "pagamento_confirmado":
            agora = datetime.now(BRASILIA)

            # Qualquer pagamento confirmado significa que a pessoa já é assinante —
            # nunca deixa o status como "trial" depois de um pagamento real.
            novo_status    = "ativo"
            assinatura_fim = (agora + timedelta(days=31)).isoformat()
            asaas_subscription_id = resultado.get("asaas_id")

            campos_update = dict(
                status=novo_status,
                assinatura_fim=assinatura_fim,
                trial_usado=True,
            )
            # Atualiza a referência da subscription real, caso ainda não tivéssemos
            # (acontece no primeiro pagamento confirmado vindo de um Checkout)
            if asaas_subscription_id and asaas_subscription_id.startswith("sub_"):
                campos_update["assinatura_asaas_id"] = asaas_subscription_id

            await atualizar_usuario(chat_id, **campos_update)

            # Se o usuário já estava em trial, esta é a primeira cobrança real (fim do trial).
            # Se já estava "ativo", é uma renovação mensal — mensagem mais simples, sem spam.
            era_trial = usuario.get("status") == "trial"

            if _bot:
                if era_trial:
                    texto = (
                        f"✅ <b>Pagamento confirmado! Seu plano mensal está ativo.</b>\n\n"
                        f"Seu trial terminou e sua assinatura de R$ 29,90/mês começou agora.\n\n"
                        f"Use o menu para continuar de onde parou. 👇"
                    )
                else:
                    texto = (
                        f"✅ <b>Pagamento da assinatura confirmado.</b>\n\n"
                        f"Seu acesso ao MercadoBot continua ativo por mais 30 dias."
                    )
                await _bot.send_message(chat_id=chat_id, text=texto, parse_mode="HTML")
            logger.info(f"Usuário {chat_id} reativado — status={novo_status}, assinatura_fim={assinatura_fim}.")

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
