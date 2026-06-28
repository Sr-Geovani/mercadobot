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
    {
        "name": "buscar_produto_especifico",
        "description": (
            "Busca informações de venda de UM produto específico na base de produtos "
            "vendidos, com tolerância a nomes abreviados ou diferentes do que aparece "
            "na embalagem/foto (o sistema do mercadinho costuma abreviar nomes, ex: "
            "'DORIT QJ 275' para Doritos Queijo 275g). Use esta ferramenta quando o "
            "usuário perguntar sobre um produto em particular — por exemplo depois de "
            "enviar uma foto e perguntar 'isso vende bem?', 'quanto vendi disso?'. "
            "IMPORTANTE: passe apenas a palavra-chave PRINCIPAL do produto (marca ou "
            "primeira palavra do nome), NÃO o nome completo com gramatura/sabor — "
            "ex: para 'Doritos Queijo 275g' passe só 'doritos', não a frase completa. "
            "Isso aumenta a chance de encontrar o produto mesmo com nome abreviado."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "nome_produto": {
                    "type": "string",
                    "description": "Apenas a palavra-chave principal do produto (marca ou primeiro termo), ex: 'doritos', 'coca', 'redbull'. Evite frases completas ou gramatura.",
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
                    "description": "Descrição curta do período em português, ex: 'últimos 30 dias'.",
                },
            },
            "required": ["nome_produto", "data_ini", "data_fim", "descricao_periodo"],
        },
    },
    {
        "name": "detectar_padroes_operacao",
        "description": (
            "Analisa o histórico recente de vendas (últimos 30 dias) e identifica "
            "padrões reais — como um dia da semana consistentemente mais fraco ou "
            "mais forte, ou o produto campeão do período. Use esta ferramenta quando "
            "o usuário perguntar algo como 'tem algum padrão estranho?', 'que dia eu "
            "vendo menos?', 'qual produto é meu campeão?', 'tem alguma tendência que "
            "eu deveria saber?'. Requer pelo menos 14 dias de histórico para ter "
            "significância estatística — se não houver dados suficientes, a "
            "ferramenta avisa isso."
        ),
        "input_schema": {
            "type": "object",
            "properties": {},
        },
    },
    {
        "name": "comparar_com_outros_mercadinhos",
        "description": (
            "Compara o desempenho de um produto campeão do usuário com a média de "
            "outros mercadinhos autônomos que vendem esse mesmo produto (benchmark "
            "anonimizado entre clientes do MercadoBot). Use quando o usuário "
            "perguntar coisas como 'esse produto vende bem comparado a outros "
            "mercadinhos?', 'estou vendendo bem isso?', 'é normal vender X por mês "
            "desse produto?'. IMPORTANTE: só funciona bem para produtos universais "
            "(refrigerantes, salgadinhos, águas, energéticos) — produtos muito "
            "nichados ou regionais não têm amostra suficiente para comparação justa, "
            "e a ferramenta vai avisar isso quando for o caso."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "nome_produto": {
                    "type": "string",
                    "description": "Nome do produto a comparar, ex: 'coca cola', 'doritos'.",
                },
            },
            "required": ["nome_produto"],
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


def _normalizar_texto(texto: str) -> str:
    """Remove acentos, baixa caixa, remove pontuação simples — facilita comparação."""
    import unicodedata
    texto = unicodedata.normalize("NFKD", texto).encode("ascii", "ignore").decode("ascii")
    return texto.lower().strip()


def _tokens_significativos(texto: str) -> list[str]:
    """
    Quebra em palavras e descarta tokens pouco discriminantes (números
    isolados, unidades de medida) — o que sobra tende a ser marca/sabor,
    que é o que mais importa para encontrar o produto certo mesmo com
    nome abreviado ou gramatura diferente.
    """
    import re
    texto = _normalizar_texto(texto)
    tokens = re.findall(r"[a-z]+", texto)  # só letras — ignora números/gramas
    return [t for t in tokens if len(t) >= 3]


def _bate_prefixo(a: str, b: str, min_len: int = 5) -> bool:
    """
    Compara dois tokens bidirecionalmente por prefixo — cobre tanto o caso
    do termo buscado vir abreviado quanto o nome no PDV Legal estar
    abreviado (ex: buscar 'doritos' deve bater com 'DORIT' no sistema).
    Para tokens curtos (< min_len), exige igualdade exata para evitar
    falsos positivos (ex: 'sal' não deveria casar com qualquer coisa).
    """
    menor_len = min(len(a), len(b))
    if menor_len < min_len:
        return a == b
    return a[:min_len] == b[:min_len]


def _buscar_produto_em_base(produtos, nome_produto: str):
    """
    Busca tolerante a abreviação, em camadas progressivas — para da
    primeira camada que encontrar algo:
      1. Contains direto (com e sem espaços, cobre termo buscado colado
         como 'redbull' batendo com 'RED BULL')
      2. Palavra-chave principal contida no nome OU prefixo bidirecional
         por token (cobre abreviação em qualquer direção)
      3. Similaridade aproximada (difflib) como último recurso

    Retorna (DataFrame filtrado, nivel_confianca) para o Claude poder
    avisar o usuário quando o match foi só aproximado, não exato.
    """
    import difflib

    nomes_produtos    = produtos["produto"].astype(str)
    nomes_norm        = nomes_produtos.apply(_normalizar_texto)
    nomes_sem_espaco  = nomes_norm.str.replace(" ", "", regex=False)
    termo_norm        = _normalizar_texto(nome_produto)
    termo_sem_espaco  = termo_norm.replace(" ", "")

    # Camada 1 — contains direto, com e sem espaços
    mask = nomes_norm.str.contains(termo_norm, na=False, regex=False)
    mask = mask | nomes_sem_espaco.str.contains(termo_sem_espaco, na=False, regex=False)
    if mask.any():
        return produtos[mask], "exata"

    tokens_busca = _tokens_significativos(nome_produto)
    if not tokens_busca:
        return produtos.iloc[0:0], "nenhuma"
    palavra_chave = tokens_busca[0]

    # Camada 2 — palavra-chave contains OU prefixo bidirecional por token
    def _bate(nome_norm: str) -> bool:
        if palavra_chave in nome_norm:
            return True
        return any(_bate_prefixo(palavra_chave, tok) for tok in _tokens_significativos(nome_norm))

    mask = nomes_norm.apply(_bate)
    if mask.any():
        return produtos[mask], "palavra_chave_ou_prefixo"

    # Camada 3 — similaridade aproximada, último recurso
    nomes_unicos = nomes_norm.unique().tolist()
    proximos = difflib.get_close_matches(termo_norm, nomes_unicos, n=5, cutoff=0.55)
    if proximos:
        mask = nomes_norm.isin(proximos)
        return produtos[mask], "aproximada"

    return produtos.iloc[0:0], "nenhuma"


async def executar_tool_buscar_produto(chat_id: int, nome_produto: str, data_ini: str,
                                        data_fim: str, descricao_periodo: str) -> dict:
    """
    Busca um produto específico na base de produtos vendidos do período,
    com tolerância a nomes abreviados ou diferentes do PDV Legal (ex: buscar
    'doritos' encontra 'DORIT QUEIJO 275' mesmo com gramatura ou abreviação
    diferentes). Retorna quantidade, valor e em quais filiais aparece.
    Usado principalmente após identificação de produto por foto.
    """
    dados = await garantir_dados_periodo(chat_id, data_ini, data_fim, descricao_periodo)
    if "erro" in dados:
        return dados

    produtos = dados.get("produtos")
    if produtos is None or len(produtos) == 0:
        return {"erro": "Nenhum dado de produtos disponível para esse período."}

    encontrados, confianca = _buscar_produto_em_base(produtos, nome_produto)

    if len(encontrados) == 0:
        return {
            "produto_buscado": nome_produto,
            "periodo": descricao_periodo,
            "encontrado": False,
            "mensagem": (
                f"Não encontrei nenhum produto parecido com '{nome_produto}' vendido "
                f"nesse período — pode não ter vendido, ou o nome no sistema é bem "
                f"diferente do esperado."
            ),
        }

    por_filial = (
        encontrados.groupby("nomeloja")
        .agg(quantidade=("quantidade", "sum"), valor=("valor", "sum"))
        .round(2)
        .to_dict(orient="index")
    )
    nomes_exatos = encontrados["produto"].unique().tolist()

    resultado = {
        "produto_buscado": nome_produto,
        "periodo": descricao_periodo,
        "encontrado": True,
        "nivel_confianca_match": confianca,  # "exata"/"palavra_chave"/"prefixo_abreviado"/"aproximada"
        "nomes_exatos_encontrados": nomes_exatos,
        "quantidade_total": int(encontrados["quantidade"].sum()),
        "valor_total": round(float(encontrados["valor"].sum()), 2),
        "por_filial": por_filial,
    }

    if confianca == "aproximada":
        resultado["aviso"] = (
            "O nome encontrado é diferente do buscado — confirme com o usuário "
            "se este é o produto certo antes de afirmar com certeza."
        )

    return resultado


async def executar_tool_detectar_padroes(chat_id: int) -> dict:
    """
    Busca os últimos 30 dias e roda a detecção de padrões via IA, retornando
    os padrões já filtrados pelo controle anti-spam (chamada sob demanda
    não tem restrição de janela, só o job automático tem).
    """
    from padroes import detectar_padroes_vendas

    agora = datetime.now(BRASILIA)
    d30   = (agora - timedelta(days=30)).strftime("%d/%m/%Y")
    hoje  = agora.strftime("%d/%m/%Y")

    dados = await garantir_dados_periodo(chat_id, d30, hoje, "últimos 30 dias")
    if "erro" in dados:
        return dados

    padroes = await detectar_padroes_vendas(chat_id, dados.get("vendas"), dados.get("produtos"))
    if not padroes:
        return {
            "padroes_encontrados": 0,
            "mensagem": "Não identifiquei nenhum padrão estatisticamente relevante nos últimos 30 dias — ou os dados ainda são insuficientes (mínimo de 14 dias de histórico).",
        }

    return {"padroes_encontrados": len(padroes), "padroes": padroes}


async def executar_tool_comparar_benchmark(chat_id: int, nome_produto: str) -> dict:
    """
    Compara um produto do usuário com o benchmark agregado de outros
    clientes. Avisa explicitamente quando a amostra é pequena demais para
    uma comparação confiável.
    """
    from database import buscar_benchmark_produto

    benchmark = await buscar_benchmark_produto(nome_produto, chat_id_excluir=chat_id)

    if benchmark.get("amostras", 0) == 0:
        return {
            "produto": nome_produto,
            "comparavel": False,
            "mensagem": (
                "Ainda não temos dados de outros mercadinhos vendendo esse produto "
                "para comparar — a base de comparação está crescendo conforme mais "
                "operadores usam o MercadoBot."
            ),
        }

    if benchmark.get("outros_clientes", 0) < 3:
        return {
            "produto": nome_produto,
            "comparavel": False,
            "amostra_pequena": True,
            "outros_clientes": benchmark["outros_clientes"],
            "mensagem": (
                f"Encontrei dados de apenas {benchmark['outros_clientes']} outro(s) "
                f"mercadinho(s) vendendo esse produto — amostra pequena demais para "
                f"uma comparação confiável ainda. Trate com cautela."
            ),
        }

    return {
        "produto": nome_produto,
        "comparavel": True,
        "outros_clientes": benchmark["outros_clientes"],
        "quantidade_media_outros": benchmark["quantidade_media"],
        "quantidade_max_outros": benchmark["quantidade_max"],
        "quantidade_min_outros": benchmark["quantidade_min"],
    }


# Mapa de nome de tool -> função Python que efetivamente a executa
EXECUTORES = {
    "buscar_faturamento": executar_tool_buscar_faturamento,
    "analisar_operacao": executar_tool_analise,
    "buscar_produto_especifico": executar_tool_buscar_produto,
    "detectar_padroes_operacao": executar_tool_detectar_padroes,
    "comparar_com_outros_mercadinhos": executar_tool_comparar_benchmark,
}


def _construir_system_prompt() -> str:
    """
    Monta o prompt do sistema com datas JÁ CALCULADAS para os períodos mais
    comuns, em vez de pedir para o Claude calcular sozinho. Isso elimina
    ambiguidade de interpretação (ex: "este mês" sendo interpretado de forma
    diferente em conversas diferentes) — a fonte da verdade do calendário é
    sempre o Python, nunca o modelo.
    """
    agora = datetime.now(BRASILIA)
    hoje        = agora.strftime("%d/%m/%Y")
    ontem       = (agora - timedelta(days=1)).strftime("%d/%m/%Y")
    seg_semana  = (agora - timedelta(days=agora.weekday())).strftime("%d/%m/%Y")
    primeiro_mes_atual = agora.replace(day=1).strftime("%d/%m/%Y")
    ultimo_dia_mes_anterior = (agora.replace(day=1) - timedelta(days=1))
    primeiro_mes_anterior   = ultimo_dia_mes_anterior.replace(day=1).strftime("%d/%m/%Y")
    ultimo_mes_anterior     = ultimo_dia_mes_anterior.strftime("%d/%m/%Y")
    d30 = (agora - timedelta(days=30)).strftime("%d/%m/%Y")

    return (
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
        "Você tem memória das mensagens anteriores trocadas HOJE com este usuário "
        "(a sessão expira à meia-noite). Pode fazer referência a perguntas anteriores "
        "do mesmo dia sem precisar que o usuário repita o contexto.\n\n"
        f"IMPORTANTE — datas EXATAS para usar nas ferramentas (não calcule por conta "
        f"própria, use estas):\n"
        f"  • 'hoje' = data_ini: {hoje}, data_fim: {hoje}\n"
        f"  • 'ontem' = data_ini: {ontem}, data_fim: {ontem}\n"
        f"  • 'esta semana' = data_ini: {seg_semana}, data_fim: {hoje}\n"
        f"  • 'este mês' / 'mês atual' = data_ini: {primeiro_mes_atual}, data_fim: {hoje} "
        f"(SEMPRE do dia 1 do mês atual até hoje — nunca um período menor)\n"
        f"  • 'mês passado' / 'mês anterior' = data_ini: {primeiro_mes_anterior}, "
        f"data_fim: {ultimo_mes_anterior} (o mês civil COMPLETO anterior, do dia 1 ao último dia)\n"
        f"  • 'últimos 30 dias' = data_ini: {d30}, data_fim: {hoje}\n"
        f"  • Para 'produtos mais vendidos no mês', 'top produtos do mês', 'lista de "
        f"reposição do mês': use SEMPRE o período de 'este mês' acima (dia 1 até hoje), "
        f"nunca um período menor como 'hoje' ou 'últimos 7 dias' — o usuário quer o mês "
        f"completo a menos que diga explicitamente outro período.\n"
        f"  • Se o usuário pedir um período customizado (ex: 'de 10 a 20 de junho'), "
        f"calcule manualmente, mas para os períodos padrão acima sempre use os valores "
        f"já calculados nesta lista.\n\n"
        "Quando o usuário enviar uma FOTO de um produto: identifique o produto pela "
        "imagem e, IMEDIATAMENTE e sem perguntar permissão, use a ferramenta "
        "'buscar_produto_especifico' com APENAS a palavra-chave principal (marca/primeira "
        "palavra, sem gramatura) para verificar se ele aparece na base de vendas dos "
        "últimos 30 dias. Tente também variações da palavra-chave se a primeira tentativa "
        "não encontrar nada (ex: para queijo mussarela, tente 'mussarela', depois 'queijo' "
        "se a primeira não achar nada — produtos genéricos como queijos, água, bebidas "
        "podem estar cadastrados de formas bem diferentes do nome na embalagem). Junte a "
        "identificação visual com o dado real de venda numa única resposta. Se o resultado "
        "vier com nivel_confianca_match 'aproximada', deixe claro ao usuário que o nome "
        "encontrado no sistema é parecido mas não idêntico, e mostre qual nome exato foi "
        "encontrado para ele confirmar. Se NENHUMA tentativa encontrar o produto, diga "
        "claramente que não encontrou esse produto na base de vendas do período, em vez "
        "de simplesmente não comentar sobre isso. Só faça uma pergunta de volta se a foto "
        "não tiver um produto claro para identificar.\n\n"
        "Responda em português do Brasil, direto ao ponto, sem rodeios. Pode usar "
        "negrito (**texto**) e emojis com moderação. Evite respostas longas — "
        "operadores de mercadinho leem isso rápido, no celular, entre uma tarefa e outra."
    )


SYSTEM_PROMPT = _construir_system_prompt()


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
        logger.warning(
            "Transcrição de áudio solicitada mas nenhuma variável de ambiente "
            "OPENAI_KEY ou OPENAI_API_KEY foi encontrada. Variáveis disponíveis "
            "que contêm 'OPENAI': " +
            str([k for k in os.environ.keys() if "OPENAI" in k.upper()])
        )
        return None

    logger.info(f"Transcrição: usando chave OpenAI (prefixo: {openai_key[:7]}...), arquivo={caminho_arquivo}")

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


def obter_historico_sessao(chat_id: int) -> list[dict]:
    """
    Recupera o histórico de conversa do agente para o dia atual. A memória
    é só em RAM (dados_usuario, já usado para outros estados do bot) e
    expira automaticamente à meia-noite — sem custo de banco, sem reter
    dados sensíveis por mais tempo do que o necessário.
    """
    from bot import dados_usuario

    hoje = datetime.now(BRASILIA).strftime("%d/%m/%Y")
    sessao = dados_usuario.get(chat_id, {}).get("agente_sessao", {})

    if sessao.get("data") != hoje:
        return []  # sessão de outro dia — não reaproveita

    return sessao.get("historico", [])


def salvar_historico_sessao(chat_id: int, historico: list[dict], limite_mensagens: int = 20):
    """
    Salva o histórico atualizado, truncando para as últimas N mensagens
    (evita o histórico crescer sem limite dentro do mesmo dia e inflar o
    custo de cada chamada nova).
    """
    from bot import dados_usuario

    hoje = datetime.now(BRASILIA).strftime("%d/%m/%Y")
    if chat_id not in dados_usuario:
        dados_usuario[chat_id] = {}

    dados_usuario[chat_id]["agente_sessao"] = {
        "data": hoje,
        "historico": historico[-limite_mensagens:],
    }


async def processar_mensagem_agente(chat_id: int, texto_usuario: str = None,
                                     historico: list[dict] = None,
                                     imagem_base64: str = None,
                                     imagem_media_type: str = None,
                                     usar_memoria_sessao: bool = True) -> str:
    """
    Ponto de entrada principal do agente. Recebe a mensagem do usuário
    (texto e/ou imagem), decide com o Claude se precisa chamar ferramentas,
    executa o que for necessário, e retorna a resposta final em texto.

    Quando há imagem, o Claude recebe a foto junto com as MESMAS ferramentas
    disponíveis para texto — assim ele pode, por exemplo, identificar um
    produto na foto E já buscar dados reais de venda desse produto no PDV
    Legal, tudo em uma única interação, em vez de duas chamadas separadas
    sem contexto compartilhado.

    historico: lista opcional de mensagens anteriores, passada explicitamente
    (sobrepõe a memória de sessão automática se usar_memoria_sessao=True).
    usar_memoria_sessao: se True (padrão), recupera e salva automaticamente
    o histórico do dia atual em dados_usuario — o usuário não precisa repetir
    contexto de perguntas anteriores na mesma sessão/dia.
    """
    client = anthropic.AsyncAnthropic(api_key=ANTHROPIC_KEY)

    if historico is not None:
        messages = list(historico)
    elif usar_memoria_sessao:
        messages = obter_historico_sessao(chat_id)
    else:
        messages = []

    if imagem_base64:
        texto_pedido = (texto_usuario or "").strip() or (
            "Identifique este produto (nome, marca, categoria). Se fizer sentido, "
            "verifique nos dados de vendas se esse produto específico aparece no "
            "histórico recente e comente brevemente sobre o desempenho dele."
        )
        messages.append({
            "role": "user",
            "content": [
                {
                    "type": "image",
                    "source": {
                        "type": "base64",
                        "media_type": imagem_media_type or "image/jpeg",
                        "data": imagem_base64,
                    },
                },
                {"type": "text", "text": texto_pedido},
            ],
        })
    else:
        messages.append({"role": "user", "content": texto_usuario})

    try:
        # Loop de tool-use: o Claude pode pedir para chamar uma ou mais
        # ferramentas antes de dar a resposta final. Repetimos até ele
        # parar de pedir ferramentas (stop_reason != "tool_use").
        for _ in range(5):  # limite de segurança contra loop infinito
            resp = await client.messages.create(
                model=MODEL,
                max_tokens=1024,
                system=_construir_system_prompt(),
                tools=TOOLS,
                messages=messages,
            )

            if resp.stop_reason != "tool_use":
                # Claude decidiu responder direto, sem (mais) ferramentas
                texto_final = "".join(
                    block.text for block in resp.content if block.type == "text"
                )
                resposta_final = texto_final.strip() or "Não consegui formular uma resposta. Tente reformular a pergunta."

                if usar_memoria_sessao:
                    messages.append({"role": "assistant", "content": resp.content})
                    salvar_historico_sessao(chat_id, messages)

                return resposta_final

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
