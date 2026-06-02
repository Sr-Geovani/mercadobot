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


async def criar_cliente_asaas(nome: str, email: str, cpf: str = None) -> dict:
    """Cria ou busca cliente no Asaas."""
    async with httpx.AsyncClient() as client:
        # Verifica se já existe
        resp = await client.get(
            f"{ASAAS_URL}/customers",
            params={"email": email},
            headers=_headers()
        )
        data = resp.json()
        if data.get("data"):
            logger.info(f"Cliente já existe no Asaas: {email}")
            return data["data"][0]

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


async def gerar_link_pagamento(asaas_cliente_id: str, chat_id: int) -> str:
    """Gera cobrança com link de pagamento via Asaas."""
    async with httpx.AsyncClient() as client:
        primeiro_vencimento = (
            datetime.now(BRASILIA) + timedelta(days=TRIAL_DIAS + 1)
        ).strftime("%Y-%m-%d")

        payload = {
            "customer":          asaas_cliente_id,
            "billingType":       "UNDEFINED",
            "value":             PRECO_MENSAL,
            "dueDate":           primeiro_vencimento,
            "description":       f"MercadoBot — 7 dias grátis, depois R$ {PRECO_MENSAL}/mês",
            "externalReference": str(chat_id),
        }
        resp = await client.post(
            f"{ASAAS_URL}/payments",
            json=payload,
            headers=_headers()
        )
        data = resp.json()
        logger.info(f"Payment criado: {data}")

        # Retorna o link de fatura gerado pelo Asaas
        return data.get("invoiceUrl") or data.get("bankSlipUrl") or ""


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
