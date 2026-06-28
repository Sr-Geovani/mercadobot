"""
padroes.py — Detecção de padrões por IA, com anti-spam e benchmark entre clientes.

Três responsabilidades:
  1. Detectar padrões (dia fraco/forte recorrente, produto campeão) a partir
     de dados reais de vendas — sem regra fixa pré-programada, deixando o
     Claude interpretar a série de dados e descrever o que encontrar.
  2. Evitar spam: cada padrão só notifica uma vez por janela de dias
     (controlado via tabela padroes_detectados).
  3. Alimentar e consultar o benchmark entre clientes — só para produtos
     campeões (top do período), nunca para a base completa, evitando
     comparar itens nichados/regionais que não fazem sentido entre
     mercadinhos de regiões diferentes.
"""
import logging
import os
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import anthropic

logger   = logging.getLogger(__name__)
BRASILIA = ZoneInfo("America/Sao_Paulo")
ANTHROPIC_KEY = os.environ.get("ANTHROPIC_KEY")
MODEL = "claude-sonnet-4-5"


async def detectar_padroes_vendas(chat_id: int, vendas, produtos=None, dias_minimos: int = 14) -> list[dict]:
    """
    Analisa a série de vendas (idealmente >= dias_minimos de histórico) e
    pede ao Claude para identificar padrões reais — dia da semana
    consistentemente mais fraco/forte, produto campeão, tendência de queda
    ou crescimento. Não usa regra fixa: o modelo decide o que é
    estatisticamente relevante, com base nos números agregados que
    fornecemos (não dados brutos linha a linha, para não gastar tokens).

    Retorna lista de dicts: [{"tipo": ..., "chave": ..., "descricao": ...}]
    chave é um identificador estável (para controle anti-spam); descricao é
    o texto pronto para mostrar ao usuário.
    """
    import pandas as pd

    if vendas is None or len(vendas) == 0:
        return []

    vendas = vendas.copy()
    if "HoraAbertura" in vendas.columns and "data_dt" not in vendas.columns:
        # Tenta extrair a data de alguma coluna disponível
        for col_data in ["DataAbertura", "data", "Data"]:
            if col_data in vendas.columns:
                vendas["data_dt"] = pd.to_datetime(vendas[col_data], errors="coerce", dayfirst=True)
                break

    if "data_dt" not in vendas.columns or vendas["data_dt"].isna().all():
        logger.warning(f"Padrões: sem coluna de data utilizável para chat_id={chat_id}")
        return []

    vendas["dia_semana"] = vendas["data_dt"].dt.day_name()
    n_dias_unicos = vendas["data_dt"].dt.date.nunique()

    if n_dias_unicos < dias_minimos:
        logger.info(f"Padrões: chat_id={chat_id} tem só {n_dias_unicos} dias de dados, abaixo do mínimo de {dias_minimos}")
        return []

    # Agrega por dia da semana — média de faturamento e nº de dias observados
    por_dia_semana = (
        vendas.groupby("dia_semana")
        .agg(faturamento_medio=("valor", "sum"), n_transacoes=("valor", "count"))
    )
    # Normaliza pela contagem de ocorrências de cada dia da semana no período
    contagem_dias = vendas.groupby("dia_semana")["data_dt"].apply(lambda s: s.dt.date.nunique())
    por_dia_semana["faturamento_medio"] = por_dia_semana["faturamento_medio"] / contagem_dias
    resumo_dia_semana = por_dia_semana["faturamento_medio"].round(2).to_dict()

    # Produto campeão do período (se tivermos dados de produtos)
    produto_campeao = None
    if produtos is not None and len(produtos) > 0 and "produto" in produtos.columns:
        agregado = produtos.groupby("produto")["quantidade"].sum().sort_values(ascending=False)
        if len(agregado) > 0:
            produto_campeao = {"nome": agregado.index[0], "quantidade": int(agregado.iloc[0])}

    contexto = {
        "dias_analisados": n_dias_unicos,
        "faturamento_medio_por_dia_da_semana": resumo_dia_semana,
        "produto_campeao": produto_campeao,
    }

    client = anthropic.AsyncAnthropic(api_key=ANTHROPIC_KEY)
    prompt = (
        "Analise estes dados agregados de vendas de um mercadinho autônomo e "
        "identifique APENAS padrões estatisticamente relevantes e ACIONÁVEIS — "
        "não force achar algo se os números forem parecidos entre si.\n\n"
        f"Dados: {contexto}\n\n"
        "Procure por: (a) um dia da semana visivelmente mais fraco que os "
        "demais (diferença de pelo menos 25% para baixo da média), (b) um dia "
        "da semana visivelmente mais forte (pelo menos 25% acima da média), "
        "(c) um produto campeão claro de destaque.\n\n"
        "Responda em JSON puro, sem markdown, uma lista de objetos com os campos "
        "'tipo' (um de: 'dia_fraco', 'dia_forte', 'produto_campeao'), 'chave' "
        "(identificador curto e estável em snake_case, ex: 'dia_fraco_terca'), "
        "e 'descricao' (texto curto e direto em português para mostrar ao "
        "operador, 1-2 frases, com emoji no início). Se não houver nenhum "
        "padrão relevante, responda uma lista vazia []."
    )

    try:
        resp = await client.messages.create(
            model=MODEL,
            max_tokens=600,
            messages=[{"role": "user", "content": prompt}],
        )
        texto = "".join(b.text for b in resp.content if b.type == "text").strip()
        texto = texto.removeprefix("```json").removeprefix("```").removesuffix("```").strip()

        import json
        padroes = json.loads(texto)
        if not isinstance(padroes, list):
            return []
        return padroes
    except Exception as e:
        logger.error(f"Erro ao detectar padrões para chat_id={chat_id}: {e}")
        return []


async def notificar_padroes_novos(chat_id: int, padroes: list[dict], janela_dias: int = 7) -> list[dict]:
    """
    Filtra a lista de padrões detectados, mantendo só os que ainda não foram
    notificados nos últimos `janela_dias` para esse usuário — evita repetir
    o mesmo alerta todo dia e virar spam. Marca os novos como notificados.
    """
    from database import ja_notificou_padrao, registrar_padrao_notificado

    novos = []
    for padrao in padroes:
        chave = padrao.get("chave", "")
        if not chave:
            continue
        if await ja_notificou_padrao(chat_id, chave, dias_validade=janela_dias):
            continue
        novos.append(padrao)
        await registrar_padrao_notificado(chat_id, padrao.get("tipo", "desconhecido"), chave)

    return novos


async def registrar_produto_campeao_benchmark(chat_id: int, produtos, periodo_ini: str, periodo_fim: str,
                                               top_n: int = 3):
    """
    Registra os top N produtos campeões desse cliente no benchmark agregado
    entre clientes. Só os campeões — nunca a base completa de produtos —
    porque a maior parte do catálogo é nichada/regional e não serve para
    comparação justa entre mercadinhos de bairros/cidades diferentes. Os
    produtos mais vendidos (refrigerantes, salgadinhos, águas) tendem a ser
    mais universais e comparáveis.
    """
    from database import registrar_benchmark_produto

    if produtos is None or len(produtos) == 0 or "produto" not in produtos.columns:
        return

    agregado = (
        produtos.groupby("produto")
        .agg(quantidade=("quantidade", "sum"), valor_total=("valor", "sum"))
        .sort_values("quantidade", ascending=False)
        .head(top_n)
    )

    for nome_produto, row in agregado.iterrows():
        try:
            await registrar_benchmark_produto(
                chat_id, nome_produto,
                int(row["quantidade"]), float(row["valor_total"]),
                periodo_ini, periodo_fim
            )
        except Exception as e:
            logger.warning(f"Erro ao registrar benchmark para '{nome_produto}': {e}")
