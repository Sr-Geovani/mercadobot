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


async def atualizar_cliente_asaas(asaas_cliente_id: str, telefone: str = None,
                                   cep: str = None, endereco_numero: str = None) -> dict:
    """
    Atualiza diretamente um cliente já existente no Asaas (PUT por ID),
    sem precisar buscar por email. Usado na reativação, quando já temos o
    asaas_id salvo e só precisamos sincronizar campos novos (telefone/CEP/número)
    que foram coletados depois da criação original do cliente.
    """
    if not asaas_cliente_id:
        return {}

    payload = {}
    if telefone:
        payload["mobilePhone"] = telefone
        payload["phone"] = telefone
    if cep:
        payload["postalCode"] = cep
    if endereco_numero:
        payload["addressNumber"] = endereco_numero

    if not payload:
        return {}

    async with httpx.AsyncClient() as client:
        resp = await client.put(
            f"{ASAAS_URL}/customers/{asaas_cliente_id}",
            json=payload,
            headers=_headers()
        )
        data = resp.json()
        if data.get("errors"):
            logger.error(f"Erro ao atualizar cliente {asaas_cliente_id} no Asaas: {data.get('errors')}")
        else:
            logger.info(f"Cliente {asaas_cliente_id} atualizado no Asaas: {payload}")
        return data


async def criar_cliente_asaas(nome: str, email: str, cpf: str = None, empresa: str = None,
                               telefone: str = None, cep: str = None,
                               endereco_numero: str = None) -> dict:
    """
    Cria ou busca cliente no Asaas, atualizando CPF, empresa, telefone e endereço.

    telefone, cep e endereco_numero são necessários para o Checkout de cartão de
    crédito recorrente — sem eles o Asaas rejeita a criação do checkout com
    invalid_object (phone, cpfCnpj, address, addressNumber, postalCode, etc).

    Quando postalCode é informado, o Asaas preenche automaticamente province e
    city com base no CEP, então não precisamos coletar tudo manualmente.
    """
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
            if telefone and not cliente.get("mobilePhone"):
                update_payload["mobilePhone"] = telefone
                update_payload["phone"] = telefone
            if cep and not cliente.get("postalCode"):
                update_payload["postalCode"] = cep
            if endereco_numero and not cliente.get("addressNumber"):
                update_payload["addressNumber"] = endereco_numero
            if update_payload:
                await client.put(
                    f"{ASAAS_URL}/customers/{cliente['id']}",
                    json=update_payload,
                    headers=_headers()
                )
                logger.info(f"Cliente atualizado: {update_payload}")
                cliente.update(update_payload)
            return cliente

        payload = {"name": nome, "email": email}
        if cpf:
            payload["cpfCnpj"] = cpf
        if empresa:
            payload["company"] = empresa
        if telefone:
            payload["mobilePhone"] = telefone
            payload["phone"] = telefone
        if cep:
            payload["postalCode"] = cep
        if endereco_numero:
            payload["addressNumber"] = endereco_numero

        resp = await client.post(
            f"{ASAAS_URL}/customers",
            json=payload,
            headers=_headers()
        )
        cliente = resp.json()
        if cliente.get("errors"):
            logger.error(f"Erro ao criar cliente no Asaas: {cliente.get('errors')}")
        else:
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


async def cancelar_checkout(checkout_id: str) -> bool:
    """
    Cancela um checkout ainda ativo no Asaas. Usado antes de gerar um novo
    link de pagamento, para evitar que existam múltiplos checkouts "vivos"
    simultaneamente para o mesmo usuário — o que gera confusão quando os
    antigos expiram depois e disparam notificações desnecessárias.
    """
    if not checkout_id:
        return False
    try:
        async with httpx.AsyncClient() as client:
            resp = await client.post(
                f"{ASAAS_URL}/checkouts/{checkout_id}/cancel",
                headers=_headers()
            )
            if resp.status_code in (200, 201):
                logger.info(f"Checkout {checkout_id} cancelado com sucesso.")
                return True
            # Pode já estar expirado/pago/inexistente — não é um erro grave
            logger.info(f"Checkout {checkout_id} não pôde ser cancelado (status {resp.status_code}): {resp.text[:200]}")
            return False
    except Exception as e:
        logger.warning(f"Erro ao tentar cancelar checkout {checkout_id}: {e}")
        return False


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
        "minutesToExpire": 60,  # 1h para preencher — valores altos (ex: dias em minutos) são rejeitados pela API
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
        logger.info(
            f"[TESTE-CHECKOUT] Checkout criado para chat_id={chat_id} | "
            f"nextDueDate configurado={primeiro_vencimento} | "
            f"valor da assinatura=R${PRECO_MENSAL} | "
            f"resposta_completa={data}"
        )

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

    Busca tanto status CONFIRMED quanto RECEIVED — pagamentos via cartão de
    crédito recorrente costumam vir como RECEIVED, não CONFIRMED.
    """
    if not asaas_cliente_id:
        return False

    from datetime import datetime, timedelta
    from zoneinfo import ZoneInfo
    brasilia   = ZoneInfo("America/Sao_Paulo")
    data_corte = (datetime.now(brasilia) - timedelta(days=35)).strftime("%Y-%m-%d")

    async with httpx.AsyncClient() as client:
        for status in ("CONFIRMED", "RECEIVED"):
            resp = await client.get(
                f"{ASAAS_URL}/payments",
                params={
                    "customer":        asaas_cliente_id,
                    "status":          status,
                    "dateCreated[ge]": data_corte,
                },
                headers=_headers()
            )
            data = resp.json()
            pagamentos = data.get("data", [])
            if pagamentos:
                logger.info(f"Pagamento recente encontrado (status={status}): {pagamentos[0].get('id')}")
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


async def cancelar_cobrancas_futuras(asaas_cliente_id: str, dias_uso_ciclo_atual: int = None) -> int:
    """
    Cancela cobranças pendentes (futuras, ainda não pagas) e estorna
    PROPORCIONALMENTE a cobrança do ciclo atual já em andamento, se houver uma
    CONFIRMED/RECEIVED recente.

    dias_uso_ciclo_atual: quantos dias do ciclo de 30 dias já foram usados.
    Se não informado, tenta calcular a partir da data de pagamento da cobrança
    encontrada. O estorno é proporcional aos dias NÃO utilizados, garantindo
    que cobramos pelo período em que o serviço foi efetivamente prestado.
    """
    if not asaas_cliente_id:
        return 0

    from datetime import datetime, timedelta
    from zoneinfo import ZoneInfo
    brasilia = ZoneInfo("America/Sao_Paulo")
    hoje     = datetime.now(brasilia)
    amanha   = (hoje + timedelta(days=1)).strftime("%Y-%m-%d")

    canceladas = 0
    async with httpx.AsyncClient() as client:

        # 1. Cobranças PENDING com vencimento futuro — cancelamento total via DELETE.
        #    Essas ainda não foram pagas, então não há nada a estornar.
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
                    logger.info(f"Cobrança PENDING futura cancelada: {pid}")

        # 2. Cobrança do CICLO ATUAL — pode estar CONFIRMED ou RECEIVED, com
        #    vencimento já passado ou próximo (não no futuro, pois é a do mês
        #    vigente). Buscamos as últimas cobranças pagas, sem filtro de data,
        #    e identificamos a mais recente como sendo o ciclo em andamento.
        for status in ("CONFIRMED", "RECEIVED"):
            resp = await client.get(
                f"{ASAAS_URL}/payments",
                params={"customer": asaas_cliente_id, "status": status, "limit": 1, "order": "desc", "sort": "dueDate"},
                headers=_headers()
            )
            pagamentos = resp.json().get("data", [])
            if not pagamentos:
                continue

            pagamento = pagamentos[0]
            pid          = pagamento.get("id")
            valor_total  = float(pagamento.get("value", 0))
            data_pgto    = pagamento.get("paymentDate") or pagamento.get("clientPaymentDate") or pagamento.get("dueDate")

            if not pid or valor_total <= 0:
                continue

            # Calcula dias de uso desde o pagamento até hoje, dentro do ciclo de 30 dias
            dias_usados = dias_uso_ciclo_atual
            if dias_usados is None and data_pgto:
                try:
                    data_pgto_dt = datetime.strptime(data_pgto[:10], "%Y-%m-%d").replace(tzinfo=brasilia)
                    dias_usados  = max(0, (hoje - data_pgto_dt).days)
                except Exception:
                    dias_usados = 0
            dias_usados = dias_usados or 0
            dias_usados = min(dias_usados, 30)  # não ultrapassa o ciclo

            dias_restantes  = 30 - dias_usados
            valor_proporcional_restante = round(valor_total * (dias_restantes / 30), 2)

            if valor_proporcional_restante <= 0:
                logger.info(
                    f"Cancelamento: ciclo já totalmente consumido ({dias_usados} dias) — "
                    f"sem estorno devido para {pid}."
                )
                continue

            # Estorna apenas a fração correspondente aos dias não utilizados
            r2 = await client.post(
                f"{ASAAS_URL}/payments/{pid}/refund",
                json={
                    "value": valor_proporcional_restante,
                    "description": (
                        f"Cancelamento — estorno proporcional: "
                        f"{dias_usados} dia(s) usado(s) de 30, "
                        f"{dias_restantes} dia(s) não utilizado(s)."
                    ),
                },
                headers=_headers()
            )
            if r2.status_code in (200, 201):
                canceladas += 1
                logger.info(
                    f"Cobrança {pid} estornada PROPORCIONALMENTE: "
                    f"R${valor_proporcional_restante:.2f} de R${valor_total:.2f} "
                    f"({dias_usados}d usados / {dias_restantes}d restantes)"
                )
            else:
                logger.warning(f"Falha ao estornar proporcionalmente {pid}: {r2.status_code} {r2.text}")

            break  # só processa a cobrança mais recente do ciclo atual

    return canceladas


async def cancelar_assinatura(asaas_id: str) -> bool:
    """Cancela assinatura no Asaas."""
    async with httpx.AsyncClient() as client:
        resp = await client.delete(
            f"{ASAAS_URL}/subscriptions/{asaas_id}",
            headers=_headers()
        )
        return resp.status_code == 200


async def cancelar_cobranca_pendente(asaas_id: str) -> bool:
    """
    Cancela a cobrança PENDENTE agendada para o primeiro ciclo (dia 8 do trial).
    Usada quando usuário cancela DURANTE o trial.
    """
    try:
        async with httpx.AsyncClient() as client:
            # Busca payments pendentes da assinatura
            resp = await client.get(
                f"{ASAAS_URL}/payments",
                headers=_headers(),
                params={"subscription": asaas_id, "status": "PENDING"}
            )
            
            if resp.status_code != 200:
                logger.warning(f"Erro ao buscar payments pendentes: {resp.status_code}")
                return False
            
            data = resp.json()
            pagamentos = data.get("data", [])
            
            if not pagamentos:
                logger.info(f"Nenhum payment pendente encontrado para {asaas_id}")
                return True
            
            # Deleta cada payment pendente
            for payment in pagamentos:
                payment_id = payment.get("id")
                if payment_id:
                    logger.info(f"Deletando payment pendente: {payment_id}")
                    delete_resp = await client.delete(
                        f"{ASAAS_URL}/payments/{payment_id}",
                        headers=_headers()
                    )
                    if delete_resp.status_code != 200:
                        logger.error(f"Erro ao deletar payment {payment_id}: {delete_resp.status_code}")
                        return False
            
            logger.info(f"Cobranças pendentes canceladas para {asaas_id}")
            return True
            
    except Exception as e:
        logger.error(f"Erro em cancelar_cobranca_pendente: {e}")
        return False


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
    # Em eventos de checkout, o externalReference que configuramos fica dentro
    # do sub-objeto subscription, não no nível raiz do checkout.
    if not chat_id and isinstance(dados.get("subscription"), dict):
        chat_id = dados["subscription"].get("externalReference")

    asaas_id = dados.get("id") or dados.get("subscription")
    if isinstance(asaas_id, dict):
        asaas_id = asaas_id.get("id")
    customer = dados.get("customer")  # ID do cliente Asaas — usado como fallback

    mapa = {
        "SUBSCRIPTION_CREATED":      "cartao_validado",
        "CHECKOUT_PAID":             "cartao_validado",
        "CHECKOUT_CANCELED":         "checkout_cancelado",
        "CHECKOUT_EXPIRED":          "checkout_expirado",
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
