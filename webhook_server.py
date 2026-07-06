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
        evento_raw = payload.get("event")
        dados_raw  = payload.get("payment") or payload.get("subscription") or payload.get("checkout") or {}
        sub_raw    = dados_raw.get("subscription") if isinstance(dados_raw.get("subscription"), dict) else {}
        logger.info(
            f"[TESTE-WEBHOOK] Evento={evento_raw} | "
            f"value={dados_raw.get('value')} | "
            f"status={dados_raw.get('status')} | "
            f"customer={dados_raw.get('customer')} | "
            f"externalReference={dados_raw.get('externalReference') or sub_raw.get('externalReference')} | "
            f"id={dados_raw.get('id')}"
        )

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
            # SUBSCRIPTION_CREATED ou CHECKOUT_PAID — validou cartão
            # A cobrança real só ocorrerá no nextDueDate (fim do trial).
            agora     = datetime.now(BRASILIA)
            trial_fim = (agora + timedelta(days=7)).isoformat()
            asaas_subscription_id = resultado.get("asaas_id")
            asaas_customer_id = resultado.get("customer")  # ID do cliente Asaas

            campos_update = dict(status="trial", trial_fim=trial_fim, trial_usado=True)
            
            # Se vem com subscription ID, salva
            if asaas_subscription_id and asaas_subscription_id.startswith("sub_"):
                campos_update["assinatura_asaas_id"] = asaas_subscription_id
            # Se vem com customer ID, também salva (importante para checkout)
            elif asaas_customer_id and asaas_customer_id.startswith("cus_"):
                campos_update["asaas_id"] = asaas_customer_id
                logger.info(f"Salvando customer ID: {asaas_customer_id}")
            
            # Se vem subscription no objeto (em alguns webhooks), também salva
            if isinstance(resultado.get("subscription"), dict):
                sub_id = resultado["subscription"].get("id")
                if sub_id and sub_id.startswith("sub_"):
                    campos_update["assinatura_asaas_id"] = sub_id

            await atualizar_usuario(chat_id, **campos_update)
            logger.info(f"Usuário {chat_id} — cartão validado, trial de 7 dias liberado até {trial_fim}.")

            if _bot:
                try:
                    from bot import kb_menu
                    await _bot.send_message(
                        chat_id=chat_id,
                        text=(
                            f"🎉 <b>Bem-vindo ao MercadoBot!</b>\n\n"
                            f"Seu cartão foi validado. Você tem <b>7 dias grátis</b> para explorar tudo.\n\n"
                            f"<b>🔔 Importante — Informação de preço (uma única vez):</b>\n\n"
                            f"📅 <b>Dias 1–7:</b> Grátis (trial completo)\n"
                            f"💰 <b>A partir do dia 8:</b> R$ 29,90/mês (primeiros 3 meses)\n"
                            f"💰 <b>A partir do 4º mês:</b> R$ 49,90/mês\n\n"
                            f"Sem surpresas. Sem pegadinha. Cancela quando quiser.\n\n"
                            f"═════════════════════════════════════\n\n"
                            f"<b>O que você pode fazer agora:</b>\n\n"
                            f"💬 <b>Pergunte qualquer coisa</b> — \"quanto vendi?\", \"qual produto vende bem?\"\n"
                            f"📸 <b>Mande foto de produto</b> — a IA identifica e mostra o desempenho\n"
                            f"🎤 <b>Mande áudio</b> — fale em vez de digitar\n"
                            f"📊 <b>Relatórios automáticos</b> — todo dia às 7h\n"
                            f"🔔 <b>Alertas inteligentes</b> — 13h, 19h, 20h, 22h (só quando algo errado)\n"
                            f"🔍 <b>Investiga quedas</b> — \"por que caiu?\" e recebe análise completa\n"
                            f"🌐 <b>Benchmark de mercado</b> — compare com outras lojas autônomas\n\n"
                            f"Vamos começar? 👇"
                        ),
                        parse_mode="HTML",
                        reply_markup=kb_menu()
                    )
                except Exception as e_envio:
                    logger.error(f"FALHA AO NOTIFICAR chat_id={chat_id} sobre cartao_validado: {e_envio}")

        elif evento == "pagamento_confirmado":
            agora = datetime.now(BRASILIA)

            # Qualquer pagamento confirmado significa que a pessoa já é assinante —
            # nunca deixa o status como "trial" depois de um pagamento real.
            novo_status    = "ativo"
            
            # ⚠️ IMPORTANTE: Se o user já tinha assinatura_fim definida (agendamento),
            # RESPEITA ela. Só calcula novo se não tinha.
            assinatura_fim_existente = usuario.get("assinatura_fim")
            if assinatura_fim_existente:
                try:
                    # Tenta usar a data existente (agendamento respeitado)
                    datetime.fromisoformat(assinatura_fim_existente)
                    assinatura_fim = assinatura_fim_existente
                except:
                    # Se erro ao parsear, recalcula
                    assinatura_fim = (agora + timedelta(days=31)).isoformat()
            else:
                # Sem assinatura_fim anterior, calcula 31 dias
                assinatura_fim = (agora + timedelta(days=31)).isoformat()
            
            # Busca subscription ID de várias possíveis localizações no webhook
            asaas_subscription_id = None
            
            # Tenta: resultado.subscription.id (subscriptions normais)
            if resultado.get("subscription") and isinstance(resultado["subscription"], dict):
                asaas_subscription_id = resultado["subscription"].get("id")
            
            # Tenta: resultado.asaas_id (fallback, se houver)
            if not asaas_subscription_id:
                asaas_subscription_id = resultado.get("asaas_id")
            
            # Tenta: resultado.id se começar com "sub_" (edge case)
            if not asaas_subscription_id and resultado.get("id", "").startswith("sub_"):
                asaas_subscription_id = resultado.get("id")

            campos_update = dict(
                status=novo_status,
                assinatura_fim=assinatura_fim,
            )
            
            # Só marca trial_usado=True se o user era TRIAL antes
            # Se era cancelado_mas_ativo ou outro status, não marca
            if usuario.get("status") == "trial":
                campos_update["trial_usado"] = True
            
            # Atualiza a referência da subscription real, caso ainda não tivéssemos
            # (acontece no primeiro pagamento confirmado vindo de um Checkout)
            if asaas_subscription_id and asaas_subscription_id.startswith("sub_"):
                campos_update["assinatura_asaas_id"] = asaas_subscription_id
                logger.info(f"✅ Subscription salva: {asaas_subscription_id}")
            else:
                logger.warning(f"⚠️ Nenhum subscription ID encontrado no webhook. Resultado: {resultado}")

            await atualizar_usuario(chat_id, **campos_update)

            # Se o usuário já estava em trial, esta é a primeira cobrança real (fim do trial).
            # Se já estava "ativo" ou outro, é uma renovação/reativação — mensagem apropriada.
            era_trial = usuario.get("status") == "trial"

            if _bot:
                from bot import kb_menu
                if era_trial:
                    texto = (
                        f"✅ <b>Pagamento confirmado! Seu plano mensal está ativo.</b>\n\n"
                        f"Seu trial terminou e sua assinatura de R$ 29,90/mês começou agora.\n\n"
                        f"Use o menu abaixo para continuar de onde parou 👇"
                    )
                else:
                    texto = (
                        f"✅ <b>Pagamento da assinatura confirmado.</b>\n\n"
                        f"Seu acesso ao MercadoBot continua ativo por mais 30 dias."
                    )
                try:
                    await _bot.send_message(
                        chat_id=chat_id, text=texto, parse_mode="HTML",
                        reply_markup=kb_menu()
                    )
                except Exception as e_envio:
                    logger.error(
                        f"FALHA AO NOTIFICAR chat_id={chat_id} sobre pagamento_confirmado: {e_envio}. "
                        f"Usuário foi ativado no banco mas pode não ter recebido a mensagem."
                    )
            logger.info(f"Usuário {chat_id} reativado — status={novo_status}, assinatura_fim={assinatura_fim}, sub_id={asaas_subscription_id}.")

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
            usuario = await buscar_usuario(chat_id)
            
            # Lógica correta de cancelamento
            if usuario:
                status_antes = usuario.get("status")
                
                if status_antes == "trial":
                    # Trial cancelado = sem assinatura_fim
                    novo_status = "cancelado"
                    await atualizar_usuario(chat_id, status=novo_status)
                else:
                    # Ativo ou outro = mantém assinatura_fim mas marca como cancelado_mas_ativo
                    novo_status = "cancelado_mas_ativo"
                    await atualizar_usuario(chat_id, status=novo_status)
                
                logger.info(f"Cancelamento: {status_antes} → {novo_status}")
            else:
                await atualizar_usuario(chat_id, status="cancelado")
            
            if _bot:
                await _bot.send_message(
                    chat_id=chat_id,
                    text=(
                        "❌ <b>Assinatura cancelada</b>\n\n"
                        "Você deixará de receber cobranças.\n"
                        "Use /start para reativar quando quiser."
                    ),
                    parse_mode="HTML"
                )

        elif evento in ("checkout_expirado", "checkout_cancelado"):
            # Cliente não terminou de preencher o cartão a tempo, ou cancelou.
            # NÃO enviamos mais mensagem de "link expirou" porque:
            # 1. Spam se o checkout expirar varias vezes
            # 2. Confunde o usuário
            # 3. Ele pode tentar /reativar se quiser um novo link
            logger.info(f"Checkout {evento} para chat_id={chat_id}")

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
