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


async def criar_cliente_asaas(nome: str, email: str, cpf: str = None) -> dict:
    """Cria ou busca cliente no Asaas, atualizando CPF se necessário."""
    async with httpx.AsyncClient() as client:
        # Verifica se já existe
        resp = await client.get(
            f"{ASAAS_URL}/customers",
            params={"email": email},
            headers=_headers()
        )
        data = resp.json()
        if data.get("data"):
            cliente = data["data"][0]
            logger.info(f"Cliente já existe no Asaas: {email}")
            # Atualiza CPF se ainda não tinha
            if cpf and not cliente.get("cpfCnpj"):
                await client.post(
                    f"{ASAAS_URL}/customers/{cliente['id']}",
                    json={"cpfCnpj": cpf},
                    headers=_headers()
                )
                logger.info(f"CPF atualizado para cliente {cliente['id']}")
            return cliente

        # Cria novo
        payload = {"name": nome, "email": email}
        if cpf:
            payload["cpfCnpj"] = cpf

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


async def gerar_link_pagamento(asaas_cliente_id: str, chat_id: int) -> tuple:
    """
    Cria assinatura mensal recorrente com trial de 7 dias.
    Retorna (link, assinatura_id).
    """
    async with httpx.AsyncClient() as client:
        primeiro_vencimento = (
            datetime.now(BRASILIA) + timedelta(days=TRIAL_DIAS + 1)
        ).strftime("%Y-%m-%d")

        payload = {
            "customer":          asaas_cliente_id,
            "billingType":       "CREDIT_CARD",
            "value":             PRECO_MENSAL,
            "nextDueDate":       primeiro_vencimento,
            "cycle":             "MONTHLY",
            "description":       "MercadoBot — Inteligência para seu mercadinho autônomo",
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

        # Busca o link da primeira cobrança
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
    Retorna dict com {evento, chat_id, asaas_id}.
    """
    evento = payload.get("event", "")
    dados  = payload.get("payment") or payload.get("subscription") or {}

    chat_id  = dados.get("externalReference")
    asaas_id = dados.get("id") or dados.get("subscription")

    mapa = {
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
        "raw":      evento,
    }
