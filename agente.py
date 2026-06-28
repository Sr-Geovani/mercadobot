"""
agente.py — Agente conversacional com tool-use (function calling).

Arquitetura: o usuário manda uma mensagem livre (texto, e futuramente
áudio/foto). O Claude recebe a mensagem + uma lista de "ferramentas"
(funções Python já existentes no bot, reaproveitadas, não reescritas) e
decide sozinho se/quais ferramentas chamar para responder.

Este módulo NÃO substitui os botões existentes — é uma camada nova e
paralela, acionada só quando o usuário escreve uma pergunta livre.

Fluxo de uma chamada:
  1. Usuário pergunta: "quanto vendi ontem?"
  2. Claude analisa a pergunta + as tools disponíveis
  3. Claude decide chamar a tool "buscar_faturamento" com data_ini/data_fim
  4. Nós executamos essa função Python de verdade (scraper real)
  5. Devolvemos o resultado pro Claude
  6. Claude formula a resposta final em linguagem natural
"""
import logging
import os
import json
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import anthropic

logger   = logging.getLogger(__name__)
BRASILIA = ZoneInfo("America/Sao_Paulo")
ANTHROPIC_KEY = os.environ.get("ANTHROPIC_KEY")

MODEL = "claude-sonnet-4-5"

# ─── DEFINIÇÃO DAS FERRAMENTAS (formato esperado pela API da Anthropic) ────
# Cada tool tem um nome, descrição (que ajuda o Claude a decidir quando usá-la)
# e um schema dos parâmetros que ela aceita.
TOOLS = [
    {
        "name": "buscar_faturamento",
        "description": (
            "Busca o faturamento e vendas de um mercadinho autônomo em um período "
            "específico, conectando diretamente ao PDV Legal (dados reais e atualizados, "
            "não estimativas). Use esta ferramenta sempre que o usuário perguntar sobre "
            "vendas, faturamento, quanto vendeu, receita, ticket médio, número de "
            "transações ou cancelamentos em um período (hoje, ontem, esta semana, "
            "este mês, ou um intervalo de datas específico)."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "data_ini": {
                    "type": "string",
                    "description": "Data inicial do período no formato DD/MM/AAAA.",
                },
                "data_fim": {
                    "type": "string",
                    "description": "Data final do período no formato DD/MM/AAAA.",
                },
                "descricao_periodo": {
                    "type": "string",
                    "description": "Descrição curta e amigável do período em português, ex: 'hoje', 'ontem', 'esta semana'. Usada só para exibição.",
                },
            },
            "required": ["data_ini", "data_fim", "descricao_periodo"],
        },
    },
    {
        "name": "analisar_operacao",
        "description": (
            "Executa uma análise específica e detalhada sobre a operação do mercadinho "
            "em um período. Use esta ferramenta quando o usuário pedir algo mais "
            "específico do que faturamento simples, como: score de saúde da operação, "
            "comparativo entre filiais/unidades, categorias de produtos mais vendidas, "
            "mix de formas de pagamento (pix/cartão/dinheiro), top produtos mais "
            "vendidos, produtos com baixo giro (que precisam de atenção no estoque), "
            "horários de pico de movimento, projeção de faturamento do mês, produto "
            "destaque do mês, evolução semanal de vendas, ou lista de reposição de "
            "produtos para comprar."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "tipo_analise": {
                    "type": "string",
                    "enum": [
                        "score", "comparativo", "categorias", "pagamentos",
                        "top_produtos", "giro_produtos", "pico_horario",
                        "projecao_mes", "produto_destaque_mes", "evolucao_semanal",
                        "reposicao",
                    ],
                    "description": (
                        "Tipo de análise: 'score' (saúde geral 0-100), 'comparativo' "
                        "(performance entre filiais), 'categorias' (vendas por categoria "
                        "de produto), 'pagamentos' (mix pix/cartão/dinheiro), "
                        "'top_produtos' (mais vendidos), 'giro_produtos' (produtos "
                        "parados/baixo giro), 'pico_horario' (horários de mais movimento), "
                        "'projecao_mes' (projeção de faturamento do mês), "
                        "'produto_destaque_mes' (produto que mais se destacou), "
                        "'evolucao_semanal' (tendência de vendas por semana), "
                        "'reposicao' (lista de produtos para comprar/repor)."
                    ),
                },
                "data_ini": {
                    "type": "string",
                    "description": "Data inicial do período no formato DD/MM/AAAA.",
                },
                "data_fim": {
                    "type": "string",
                    "description": "Data final do período no formato DD/MM/AAAA.",
                },
                "descricao_periodo": {
                    "type": "string",
                    "description": "Descrição curta e amigável do período em português, ex: 'hoje', 'este mês'.",
                },
            },
            "required": ["tipo_analise", "data_ini", "data_fim", "descricao_periodo"],
        },
    },
]


def _resolver_periodo_relativo(texto_periodo: str) -> tuple[str, str] | None:
    """
    Resolve expressões relativas comuns ('hoje', 'ontem', 'esta semana', 'este mês')
    para datas DD/MM/AAAA. Retorna None se não reconhecer — nesse caso o próprio
    Claude já deve ter calculado e enviado data_ini/data_fim diretamente.
    """
    agora = datetime.now(BRASILIA)
    texto = texto_periodo.lower().strip()

    if texto in ("hoje",):
        d = agora.strftime("%d/%m/%Y")
        return d, d
    if texto in ("ontem",):
        d = (agora - timedelta(days=1)).strftime("%d/%m/%Y")
        return d, d
    if "esta semana" in texto or "essa semana" in texto:
        seg = agora - timedelta(days=agora.weekday())
        return seg.strftime("%d/%m/%Y"), agora.strftime("%d/%m/%Y")
    if "este mês" in texto or "esse mês" in texto or "mes atual" in texto:
        primeiro = agora.replace(day=1)
        return primeiro.strftime("%d/%m/%Y"), agora.strftime("%d/%m/%Y")
    return None


async def garantir_dados_periodo(chat_id: int, data_ini: str, data_fim: str,
                                  descricao_periodo: str) -> dict:
    """
    Busca vendas E produtos do PDV Legal para o período, reaproveitando o
    mesmo caminho real usado pelos botões existentes. Salva em dados_usuario
    para sincronizar com o menu, e retorna os DataFrames + metadados.

    Centraliza a busca para evitar baixar os dados duas vezes quando o agente
    precisa encadear múltiplas ferramentas (ex: faturamento + score) sobre o
    mesmo período.
    """
    import asyncio
    import pandas as pd
    from scraper import baixar_relatorios_periodo
    from database import buscar_usuario
    from bot import normalizar_vendas, normalizar_produtos, dados_usuario

    usuario = await buscar_usuario(chat_id)
    if not usuario or not usuario.get("pdv_email"):
        return {"erro": "Usuário sem credenciais do PDV Legal cadastradas."}

    pdv_email = usuario.get("pdv_email")
    pdv_senha = usuario.get("pdv_senha")

    # Reaproveita dados já carregados se o período pedido for exatamente o
    # mesmo já carregado em dados_usuario — evita scrape duplicado.
    d_atual = dados_usuario.get(chat_id, {})
    if d_atual.get("data_ini") == data_ini and d_atual.get("data_fim") == data_fim \
       and d_atual.get("vendas") is not None:
        return {
            "vendas": d_atual["vendas"],
            "produtos": d_atual.get("produtos"),
            "total_cancel": d_atual.get("total_cancel", {}),
        }

    try:
        loop = asyncio.get_event_loop()
        path_vendas, path_produtos, total_cancel = await loop.run_in_executor(
            None, baixar_relatorios_periodo, data_ini, data_fim, pdv_email, pdv_senha
        )
        vendas   = normalizar_vendas(pd.read_excel(path_vendas))
        produtos = normalizar_produtos(pd.read_excel(path_produtos))
    except Exception as e:
        logger.error(f"Agente: erro ao buscar dados para {chat_id}: {e}")
        return {"erro": f"Não consegui buscar os dados no PDV Legal agora: {e}"}

    if chat_id not in dados_usuario:
        dados_usuario[chat_id] = {}
    dados_usuario[chat_id]["vendas"]        = vendas
    dados_usuario[chat_id]["produtos"]      = produtos
    dados_usuario[chat_id]["periodo_label"] = descricao_periodo
    dados_usuario[chat_id]["total_cancel"]  = total_cancel
    dados_usuario[chat_id]["data_ini"]      = data_ini
    dados_usuario[chat_id]["data_fim"]      = data_fim

    return {"vendas": vendas, "produtos": produtos, "total_cancel": total_cancel}


async def executar_tool_buscar_faturamento(chat_id: int, data_ini: str, data_fim: str,
                                            descricao_periodo: str) -> dict:
    """
    Executa de fato a busca de faturamento — reaproveita o MESMO caminho que
    o botão 'Briefing'/'Atualizar' já usa: scraper real do PDV Legal +
    normalização de dados. Não há lógica duplicada nem dado simulado.
    """
    dados = await garantir_dados_periodo(chat_id, data_ini, data_fim, descricao_periodo)
    if "erro" in dados:
        return dados

    vendas       = dados["vendas"]
    total_cancel = dados.get("total_cancel", {})

    if len(vendas) == 0:
        return {
            "periodo": descricao_periodo,
            "data_ini": data_ini,
            "data_fim": data_fim,
            "total_vendas": 0,
            "faturamento_total": 0.0,
            "ticket_medio": 0.0,
            "mensagem": "Nenhuma venda registrada nesse período.",
        }

    total      = float(vendas["valor"].sum())
    n          = int(len(vendas))
    ticket     = float(vendas["valor"].mean()) if n else 0.0
    cancel_val = total_cancel.get("_total", 0.0) if isinstance(total_cancel, dict) else float(total_cancel or 0)

    por_filial = {}
    if "nomeFilial" in vendas.columns:
        por_filial = vendas.groupby("nomeFilial")["valor"].sum().round(2).to_dict()

    return {
        "periodo": descricao_periodo,
        "data_ini": data_ini,
        "data_fim": data_fim,
        "total_vendas": n,
        "faturamento_total": round(total, 2),
        "ticket_medio": round(ticket, 2),
        "cancelamentos_total": round(cancel_val, 2),
        "faturamento_por_filial": por_filial,
    }


async def executar_tool_analise(chat_id: int, tipo_analise: str, data_ini: str,
                                 data_fim: str, descricao_periodo: str) -> dict:
    """
    Executa uma das análises já existentes no bot (score, comparativo,
    categorias, pagamentos, top produtos, giro, pico de horário, projeção,
    produto destaque do mês), reaproveitando as funções bloco_* originais
    sem duplicar nenhuma lógica de cálculo.
    """
    from bot import (
        bloco_score, bloco_comparativo, bloco_categorias, bloco_pagamentos,
        bloco_top_produtos, bloco_giro_produtos, bloco_pico, bloco_projecao_mes,
        bloco_produto_mes, bloco_semanal, bloco_reposicao,
    )

    dados = await garantir_dados_periodo(chat_id, data_ini, data_fim, descricao_periodo)
    if "erro" in dados:
        return dados

    vendas   = dados["vendas"]
    produtos = dados.get("produtos")

    if vendas is None or len(vendas) == 0:
        return {"erro": "Nenhum dado de vendas disponível para esse período."}

    mapa_blocos_vendas = {
        "score": bloco_score,
        "comparativo": bloco_comparativo,
        "pagamentos": bloco_pagamentos,
        "pico_horario": bloco_pico,
        "projecao_mes": bloco_projecao_mes,
        "evolucao_semanal": bloco_semanal,
    }
    mapa_blocos_produtos = {
        "categorias": bloco_categorias,
        "top_produtos": bloco_top_produtos,
        "giro_produtos": bloco_giro_produtos,
        "produto_destaque_mes": bloco_produto_mes,
    }

    if tipo_analise == "reposicao":
        if produtos is None or len(produtos) == 0:
            return {"erro": "Nenhum dado de produtos disponível para esse período."}
        blocos = bloco_reposicao(produtos, modo="exato")
        return {"periodo": descricao_periodo, "analise": tipo_analise, "resultado_texto": "\n\n".join(blocos)}

    if tipo_analise in mapa_blocos_vendas:
        texto = mapa_blocos_vendas[tipo_analise](vendas)
        return {"periodo": descricao_periodo, "analise": tipo_analise, "resultado_texto": texto}

    if tipo_analise in mapa_blocos_produtos:
        if produtos is None or len(produtos) == 0:
            return {"erro": "Nenhum dado de produtos disponível para esse período."}
        texto = mapa_blocos_produtos[tipo_analise](produtos)
        return {"periodo": descricao_periodo, "analise": tipo_analise, "resultado_texto": texto}

    return {"erro": f"Tipo de análise '{tipo_analise}' não reconhecido."}


# Mapa de nome de tool -> função Python que efetivamente a executa
EXECUTORES = {
    "buscar_faturamento": executar_tool_buscar_faturamento,
    "analisar_operacao": executar_tool_analise,
}


SYSTEM_PROMPT = (
    "Você é o assistente do MercadoBot, um SaaS de inteligência para operadores de "
    "mercadinhos autônomos em condomínios no Brasil. Você conversa direto com o "
    "operador do mercadinho via Telegram.\n\n"
    "Contexto importante sobre o negócio: nesses mercados não há operador/caixa "
    "presente — o cliente final escaneia e paga sozinho. Por isso cancelamentos "
    "(erro de operação, item não reconhecido, desistência) são NORMAIS e ESPERADOS "
    "nesse modelo. Só é motivo de alerta quando o cancelamento passa de 25% do "
    "faturamento bruto. Abaixo disso, não trate como problema.\n\n"
    "Você tem ferramentas reais para buscar dados atualizados direto do sistema "
    "PDV Legal do usuário. Use-as sempre que a pergunta envolver números, vendas, "
    "faturamento ou qualquer dado concreto — nunca invente ou estime valores.\n\n"
    "Quando o usuário mencionar um período relativo (hoje, ontem, esta semana, "
    "este mês), calcule você mesmo as datas exatas em formato DD/MM/AAAA antes de "
    "chamar a ferramenta — hoje é " + datetime.now(BRASILIA).strftime("%d/%m/%Y") + ".\n\n"
    "Responda em português do Brasil, direto ao ponto, sem rodeios. Pode usar "
    "negrito (**texto**) e emojis com moderação. Evite respostas longas — "
    "operadores de mercadinho leem isso rápido, no celular, entre uma tarefa e outra."
)


async def identificar_produto_imagem(imagem_base64: str, media_type: str,
                                      pergunta_usuario: str = "") -> str:
    """
    Usa a visão nativa do Claude (suportada de fato pela API, ao contrário de
    áudio) para identificar um produto a partir de uma foto, e responder à
    pergunta do usuário sobre ele (ex: "isso vende bem?", "tenho estoque
    disso?"). Roda como uma chamada simples (sem tool-use), pois aqui a
    entrada principal é a imagem, não uma pergunta que precise de dados do
    PDV Legal — ainda. Se o usuário quiser dados reais sobre o produto
    identificado, deve perguntar em seguida em texto, e cai no fluxo normal
    do agente com tool-use.
    """
    client = anthropic.AsyncAnthropic(api_key=ANTHROPIC_KEY)

    texto_pedido = pergunta_usuario.strip() or (
        "Identifique este produto (nome, marca, categoria). Depois, dê uma dica "
        "rápida e prática para um operador de mercadinho autônomo sobre esse "
        "produto — por exemplo, se costuma ter giro alto, onde costuma ficar "
        "bem posicionado, ou produtos complementares para vender junto."
    )

    try:
        resp = await client.messages.create(
            model=MODEL,
            max_tokens=500,
            system=SYSTEM_PROMPT,
            messages=[{
                "role": "user",
                "content": [
                    {
                        "type": "image",
                        "source": {
                            "type": "base64",
                            "media_type": media_type,
                            "data": imagem_base64,
                        },
                    },
                    {"type": "text", "text": texto_pedido},
                ],
            }],
        )
        texto = "".join(block.text for block in resp.content if block.type == "text")
        return texto.strip() or "Não consegui identificar o produto nessa imagem."
    except Exception as e:
        logger.error(f"Erro ao identificar produto por imagem: {e}")
        return "⚠️ Não consegui analisar a imagem agora. Tente de novo em alguns instantes."


async def transcrever_audio(caminho_arquivo: str) -> str | None:
    """
    Transcreve áudio para texto usando a API Whisper da OpenAI.

    Importante: a API da Anthropic (Claude) NÃO possui endpoint de áudio
    documentado para desenvolvedores — o voice mode do Claude é exclusivo
    do produto consumidor (app/web), não está disponível via API. Por isso
    usamos Whisper para a transcrição e só então mandamos o texto resultante
    para o Claude, como uma mensagem de texto normal.

    Retorna None se a chave da OpenAI não estiver configurada ou se a
    transcrição falhar — o chamador deve tratar esse caso com uma mensagem
    clara ao usuário, nunca falhar em silêncio.
    """
    openai_key = os.environ.get("OPENAI_KEY") or os.environ.get("OPENAI_API_KEY")
    if not openai_key:
        logger.warning("Transcrição de áudio solicitada mas OPENAI_KEY não está configurada.")
        return None

    try:
        import httpx
        async with httpx.AsyncClient(timeout=60) as client:
            with open(caminho_arquivo, "rb") as f:
                resp = await client.post(
                    "https://api.openai.com/v1/audio/transcriptions",
                    headers={"Authorization": f"Bearer {openai_key}"},
                    files={"file": (os.path.basename(caminho_arquivo), f, "audio/ogg")},
                    data={"model": "whisper-1", "language": "pt"},
                )
            if resp.status_code != 200:
                logger.error(f"Erro na transcrição Whisper: {resp.status_code} {resp.text}")
                return None
            return resp.json().get("text", "").strip() or None
    except Exception as e:
        logger.error(f"Erro ao transcrever áudio: {e}")
        return None


async def processar_mensagem_agente(chat_id: int, texto_usuario: str,
                                     historico: list[dict] = None) -> str:
    """
    Ponto de entrada principal do agente. Recebe a mensagem do usuário,
    decide com o Claude se precisa chamar ferramentas, executa o que for
    necessário, e retorna a resposta final em texto.

    historico: lista opcional de mensagens anteriores [{"role": ..., "content": ...}]
    para dar contexto de conversas anteriores na mesma sessão.
    """
    client = anthropic.AsyncAnthropic(api_key=ANTHROPIC_KEY)

    messages = list(historico) if historico else []
    messages.append({"role": "user", "content": texto_usuario})

    try:
        # Loop de tool-use: o Claude pode pedir para chamar uma ou mais
        # ferramentas antes de dar a resposta final. Repetimos até ele
        # parar de pedir ferramentas (stop_reason != "tool_use").
        for _ in range(5):  # limite de segurança contra loop infinito
            resp = await client.messages.create(
                model=MODEL,
                max_tokens=1024,
                system=SYSTEM_PROMPT,
                tools=TOOLS,
                messages=messages,
            )

            if resp.stop_reason != "tool_use":
                # Claude decidiu responder direto, sem (mais) ferramentas
                texto_final = "".join(
                    block.text for block in resp.content if block.type == "text"
                )
                return texto_final.strip() or "Não consegui formular uma resposta. Tente reformular a pergunta."

            # Claude pediu para usar uma ou mais ferramentas — executamos cada uma
            messages.append({"role": "assistant", "content": resp.content})

            tool_results = []
            for block in resp.content:
                if block.type != "tool_use":
                    continue

                nome_tool = block.name
                args      = block.input
                logger.info(f"Agente chat_id={chat_id}: chamando tool '{nome_tool}' com {args}")

                executor = EXECUTORES.get(nome_tool)
                if not executor:
                    resultado = {"erro": f"Ferramenta '{nome_tool}' não implementada."}
                else:
                    resultado = await executor(chat_id, **args)

                tool_results.append({
                    "type": "tool_result",
                    "tool_use_id": block.id,
                    "content": json.dumps(resultado, ensure_ascii=False),
                })

            messages.append({"role": "user", "content": tool_results})

        return "Sua pergunta envolveu muitas etapas — tente perguntar de forma mais direta."

    except Exception as e:
        logger.error(f"Erro no agente para chat_id={chat_id}: {e}")
        return "⚠️ Não consegui processar sua pergunta agora. Tente de novo em alguns instantes."
