"""
pagamento.py — Integração com Asaas
Cria clientes, assinaturas com trial e processa webhooks.
"""
import os
import logging
import httpx
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

logger  = logging.getLogger(__name__)
BRASILIA = ZoneInfo("America/Sao_Paulo")

ASAAS_KEY = os.environ.get("ASAAS_KEY")
ASAAS_URL = os.environ.get("ASAAS_URL", "https://sandbox.asaas.com/api/v3")
# Em produção: ASAAS_URL = https://api.asaas.com/v3

PRECO_MENSAL = 29.90
TRIAL_DIAS   = 7


def _headers():
    return {
        "access_token": ASAAS_KEY,
        "Content-Type": "application/json",
    }


async def buscar_cliente_por_email(email: str) -> dict | None:
    """Busca cliente no Asaas pelo email."""
    async with httpx.AsyncClient() as client:
        resp = await client.get(
            f"{ASAAS_URL}/customers",
            params={"email": email},
            headers=_headers()
        )
        data = resp.json()
        clientes = data.get("data", [])
        return clientes[0] if clientes else None


async def criar_cliente_asaas(nome: str, email: str, cpf: str = None, empresa: str = None) -> dict:
    """Cria ou busca cliente no Asaas, atualizando CPF e empresa se necessário."""
    async with httpx.AsyncClient() as client:
        resp = await client.get(
            f"{ASAAS_URL}/customers",
            params={"email": email},
            headers=_headers()
        )
        data = resp.json()
        if data.get("data"):
            cliente = data["data"][0]
            logger.info(f"Cliente já existe no Asaas: {email}")
            # Atualiza campos faltantes
            update_payload = {}
            if cpf and not cliente.get("cpfCnpj"):
                update_payload["cpfCnpj"] = cpf
            if empresa and not cliente.get("company"):
                update_payload["company"] = empresa
            if update_payload:
                await client.put(
                    f"{ASAAS_URL}/customers/{cliente['id']}",
                    json=update_payload,
                    headers=_headers()
                )
                logger.info(f"Cliente atualizado: {update_payload}")
            return cliente

        payload = {"name": nome, "email": email}
        if cpf:
            payload["cpfCnpj"] = cpf
        if empresa:
            payload["company"] = empresa

        resp = await client.post(
            f"{ASAAS_URL}/customers",
            json=payload,
            headers=_headers()
        )
        cliente = resp.json()
        logger.info(f"Cliente criado no Asaas: {cliente.get('id')}")
        return cliente


async def criar_assinatura_com_trial(
    asaas_cliente_id: str,
    chat_id: int
) -> dict:
    """
    Cria assinatura mensal com 7 dias de trial.
    Primeira cobrança no 8º dia.
    """
    primeiro_vencimento = (
        datetime.now(BRASILIA) + timedelta(days=TRIAL_DIAS + 1)
    ).strftime("%Y-%m-%d")

    async with httpx.AsyncClient() as client:
        payload = {
            "customer":        asaas_cliente_id,
            "billingType":     "CREDIT_CARD",  # ou PIX
            "value":           PRECO_MENSAL,
            "nextDueDate":     primeiro_vencimento,
            "cycle":           "MONTHLY",
            "description":     "MercadoBot — Inteligência para seu mercadinho",
            "externalReference": str(chat_id),
        }
        resp = await client.post(
            f"{ASAAS_URL}/subscriptions",
            json=payload,
            headers=_headers()
        )
        assinatura = resp.json()
        logger.info(f"Assinatura criada: {assinatura.get('id')}")
        return assinatura


async def gerar_link_pagamento(asaas_cliente_id: str, chat_id: int, reativacao: bool = False,
                                dias_trial_restantes: int = 0,
                                nome_cliente: str = "", email_cliente: str = "",
                                cpf_cliente: str = "") -> tuple:
    """
    Cria um Checkout do Asaas (chargeTypes=RECURRENT) que, ao validar o cartão,
    cria a assinatura SEM cobrar a primeira parcela imediatamente — respeita o
    nextDueDate informado. Isso evita o bug de cobrança no cadastro/trial.

    Documentação: ao usar checkout com chargeTypes RECURRENT, o cartão é validado
    mas a cobrança só ocorre no vencimento configurado em subscription.nextDueDate.

    Retorna (link_checkout, checkout_id).
    """
    if reativacao and dias_trial_restantes <= 0:
        # Trial esgotado — cobrança imediata (hoje)
        primeiro_vencimento = datetime.now(BRASILIA).strftime("%Y-%m-%d %H:%M:%S")
        descricao = "MercadoBot — Reativação de assinatura"
    elif reativacao and dias_trial_restantes > 0:
        primeiro_vencimento = (
            datetime.now(BRASILIA) + timedelta(days=dias_trial_restantes + 1)
        ).strftime("%Y-%m-%d %H:%M:%S")
        descricao = f"MercadoBot — Reativação ({dias_trial_restantes}d de trial restantes)"
    else:
        primeiro_vencimento = (
            datetime.now(BRASILIA) + timedelta(days=TRIAL_DIAS + 1)
        ).strftime("%Y-%m-%d %H:%M:%S")
        descricao = "MercadoBot — Inteligência para seu mercadinho autônomo"

    payload = {
        "billingTypes":   ["CREDIT_CARD"],
        "chargeTypes":    ["RECURRENT"],
        "minutesToExpire": 4320,  # 3 dias para o cliente preencher
        "callback": {
            "successUrl": "https://t.me/MercadoBotOficial",
            "cancelUrl":  "https://t.me/MercadoBotOficial",
            "expiredUrl": "https://t.me/MercadoBotOficial",
        },
        "items": [{
            "name":        "MercadoBot",
            "description": descricao,
            "quantity":    1,
            "value":       PRECO_MENSAL,
        }],
        "subscription": {
            "cycle":             "MONTHLY",
            "nextDueDate":       primeiro_vencimento,
            "externalReference": str(chat_id),
        },
    }

    # Se já temos o cliente cadastrado no Asaas, vincula — evita duplicar cadastro
    if asaas_cliente_id:
        payload["customer"] = asaas_cliente_id

    async with httpx.AsyncClient() as client:
        resp = await client.post(
            f"{ASAAS_URL}/checkouts",
            json=payload,
            headers=_headers()
        )
        data = resp.json()
        logger.info(f"Checkout criado: {data}")

        checkout_id = data.get("id", "")
        link        = data.get("link", "") or data.get("url", "")

        if not link:
            logger.error(f"Erro ao criar checkout: {data}")
            return "", ""

        return link, checkout_id


async def gerar_link_pagamento_legado(asaas_cliente_id: str, chat_id: int, reativacao: bool = False, dias_trial_restantes: int = 0) -> tuple:
    """
    MÉTODO ANTIGO — mantido apenas como referência/fallback.
    PROBLEMA CONHECIDO: cria a subscription direto e usa o invoiceUrl da primeira
    cobrança. Quando o cliente preenche o cartão nesse link, o Asaas cobra
    IMEDIATAMENTE, ignorando o nextDueDate configurado — não respeita o trial.
    Use gerar_link_pagamento() (Checkout) em vez deste.
    """
    async with httpx.AsyncClient() as client:
        if reativacao and dias_trial_restantes <= 0:
            primeiro_vencimento = datetime.now(BRASILIA).strftime("%Y-%m-%d")
            descricao = "MercadoBot — Reativação de assinatura"
        elif reativacao and dias_trial_restantes > 0:
            primeiro_vencimento = (
                datetime.now(BRASILIA) + timedelta(days=dias_trial_restantes + 1)
            ).strftime("%Y-%m-%d")
            descricao = f"MercadoBot — Reativação ({dias_trial_restantes}d de trial restantes)"
        else:
            primeiro_vencimento = (
                datetime.now(BRASILIA) + timedelta(days=TRIAL_DIAS + 1)
            ).strftime("%Y-%m-%d")
            descricao = "MercadoBot — Inteligência para seu mercadinho autônomo"

        payload = {
            "customer":          asaas_cliente_id,
            "billingType":       "CREDIT_CARD",
            "value":             PRECO_MENSAL,
            "nextDueDate":       primeiro_vencimento,
            "cycle":             "MONTHLY",
            "description":       descricao,
            "externalReference": str(chat_id),
        }
        resp = await client.post(
            f"{ASAAS_URL}/subscriptions",
            json=payload,
            headers=_headers()
        )
        data = resp.json()
        logger.info(f"Assinatura criada: {data}")

        assinatura_id = data.get("id")
        if not assinatura_id:
            logger.error(f"Erro ao criar assinatura: {data}")
            return "", ""

        resp_pag = await client.get(
            f"{ASAAS_URL}/subscriptions/{assinatura_id}/payments",
            headers=_headers()
        )
        pagamentos = resp_pag.json()
        logger.info(f"Pagamentos da assinatura: {pagamentos}")

        link = ""
        if pagamentos.get("data"):
            primeira = pagamentos["data"][0]
            link = primeira.get("invoiceUrl") or primeira.get("bankSlipUrl") or ""

        return link, assinatura_id


async def verificar_pagamento_confirmado(asaas_cliente_id: str) -> bool:
    """
    Verifica se existe pagamento confirmado recente (últimos 35 dias).
    Pagamentos antigos não reativam o acesso.
    """
    if not asaas_cliente_id:
        return False

    from datetime import datetime, timedelta
    from zoneinfo import ZoneInfo
    brasilia   = ZoneInfo("America/Sao_Paulo")
    data_corte = (datetime.now(brasilia) - timedelta(days=35)).strftime("%Y-%m-%d")

    async with httpx.AsyncClient() as client:
        resp = await client.get(
            f"{ASAAS_URL}/payments",
            params={
                "customer":        asaas_cliente_id,
                "status":          "CONFIRMED",
                "dateCreated[ge]": data_corte,
            },
            headers=_headers()
        )
        data = resp.json()
        pagamentos = data.get("data", [])
        if pagamentos:
            logger.info(f"Pagamento recente encontrado: {pagamentos[0].get('id')}")
            return True
        return False


async def buscar_assinatura_ativa(asaas_cliente_id: str) -> str:
    """Busca assinatura ativa existente para o cliente. Retorna o ID ou vazio."""
    async with httpx.AsyncClient() as client:
        resp = await client.get(
            f"{ASAAS_URL}/subscriptions",
            params={"customer": asaas_cliente_id, "status": "ACTIVE"},
            headers=_headers()
        )
        data = resp.json()
        assinaturas = data.get("data", [])
        if assinaturas:
            logger.info(f"Assinatura ativa encontrada: {assinaturas[0]['id']}")
            return assinaturas[0]["id"]
        return ""


async def buscar_link_assinatura(assinatura_id: str) -> str:
    """Busca o link de pagamento da próxima cobrança de uma assinatura."""
    async with httpx.AsyncClient() as client:
        resp = await client.get(
            f"{ASAAS_URL}/subscriptions/{assinatura_id}/payments",
            params={"status": "PENDING"},
            headers=_headers()
        )
        data = resp.json()
        pagamentos = data.get("data", [])
        if pagamentos:
            return pagamentos[0].get("invoiceUrl") or pagamentos[0].get("bankSlipUrl") or ""
        return ""


async def cancelar_cobrancas_futuras(asaas_cliente_id: str) -> int:
    """
    Cancela cobranças pendentes e estorna confirmadas com vencimento futuro.
    """
    if not asaas_cliente_id:
        return 0

    from datetime import datetime, timedelta
    from zoneinfo import ZoneInfo
    brasilia = ZoneInfo("America/Sao_Paulo")
    amanha   = (datetime.now(brasilia) + timedelta(days=1)).strftime("%Y-%m-%d")

    canceladas = 0
    async with httpx.AsyncClient() as client:

        # PENDING — cancela com DELETE
        resp = await client.get(
            f"{ASAAS_URL}/payments",
            params={"customer": asaas_cliente_id, "status": "PENDING", "dueDateStart": amanha},
            headers=_headers()
        )
        for pagamento in resp.json().get("data", []):
            pid = pagamento.get("id")
            if pid:
                r = await client.delete(f"{ASAAS_URL}/payments/{pid}", headers=_headers())
                if r.status_code in (200, 204):
                    canceladas += 1
                    logger.info(f"Cobrança PENDING cancelada: {pid}")

        # CONFIRMED com vencimento futuro — estorna com refund
        resp = await client.get(
            f"{ASAAS_URL}/payments",
            params={"customer": asaas_cliente_id, "status": "CONFIRMED", "dueDateStart": amanha},
            headers=_headers()
        )
        for pagamento in resp.json().get("data", []):
            pid   = pagamento.get("id")
            valor = pagamento.get("value", 0)
            if pid:
                # Tenta DELETE primeiro (funciona se ainda não processado)
                r = await client.delete(f"{ASAAS_URL}/payments/{pid}", headers=_headers())
                if r.status_code in (200, 204):
                    canceladas += 1
                    logger.info(f"Cobrança CONFIRMED cancelada via DELETE: {pid}")
                else:
                    # Fallback: solicita reembolso total
                    r2 = await client.post(
                        f"{ASAAS_URL}/payments/{pid}/refund",
                        json={"value": valor, "description": "Cancelamento dentro do trial"},
                        headers=_headers()
                    )
                    if r2.status_code in (200, 201):
                        canceladas += 1
                        logger.info(f"Cobrança CONFIRMED estornada via refund: {pid}")
                    else:
                        logger.warning(f"Falha ao cancelar/estornar {pid}: {r2.status_code} {r2.text}")

    return canceladas


async def cancelar_assinatura(asaas_id: str) -> bool:
    """Cancela assinatura no Asaas."""
    async with httpx.AsyncClient() as client:
        resp = await client.delete(
            f"{ASAAS_URL}/subscriptions/{asaas_id}",
            headers=_headers()
        )
        return resp.status_code == 200


def processar_webhook(payload: dict) -> dict:
    """
    Interpreta o evento do webhook do Asaas.
    Retorna dict com {evento, chat_id, asaas_id, customer}.

    chat_id pode vir vazio se o evento for de um payment cujo externalReference
    não foi propagado da subscription (comum em fluxos via Checkout). Nesse caso,
    o chamador deve resolver o chat_id buscando o usuário pelo campo "customer"
    (asaas_id) salvo no banco.
    """
    evento = payload.get("event", "")
    dados  = payload.get("payment") or payload.get("subscription") or payload.get("checkout") or {}

    chat_id  = dados.get("externalReference")
    asaas_id = dados.get("id") or dados.get("subscription")
    customer = dados.get("customer")  # ID do cliente Asaas — usado como fallback

    mapa = {
        "SUBSCRIPTION_CREATED":      "cartao_validado",
        "PAYMENT_CONFIRMED":         "pagamento_confirmado",
        "PAYMENT_RECEIVED":          "pagamento_confirmado",
        "PAYMENT_OVERDUE":           "pagamento_atrasado",
        "PAYMENT_DELETED":           "pagamento_cancelado",
        "SUBSCRIPTION_INACTIVATED": "assinatura_cancelada",
        "SUBSCRIPTION_DELETED":     "assinatura_cancelada",
    }

    return {
        "evento":   mapa.get(evento, evento),
        "chat_id":  int(chat_id) if chat_id else None,
        "asaas_id": asaas_id,
        "customer": customer,
        "raw":      evento,
    }
