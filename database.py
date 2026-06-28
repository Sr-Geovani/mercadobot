"""
database.py — Banco de dados PostgreSQL persistente
Usa asyncpg para operações assíncronas.
"""
import os
import logging
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import asyncpg

logger   = logging.getLogger(__name__)
BRASILIA = ZoneInfo("America/Sao_Paulo")

DATABASE_URL = os.environ.get("DATABASE_URL", "")

# Pool global de conexões
_pool = None


async def get_pool():
    global _pool
    if _pool is None:
        _pool = await asyncpg.create_pool(DATABASE_URL, min_size=1, max_size=5)
    return _pool


async def inicializar_banco():
    """Cria as tabelas se não existirem."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS usuarios (
                chat_id             BIGINT PRIMARY KEY,
                nome                TEXT,
                nome_mercadinho     TEXT,
                email               TEXT,
                pdv_email           TEXT,
                pdv_senha           TEXT,
                status              TEXT DEFAULT 'pendente',
                plano               TEXT DEFAULT 'mensal',
                trial_fim           TEXT,
                assinatura_fim      TEXT,
                asaas_id            TEXT,
                assinatura_asaas_id TEXT,
                trial_usado         BOOLEAN DEFAULT FALSE,
                criado_em           TEXT,
                atualizado_em       TEXT
            )
        """)
        # Adiciona colunas novas se não existirem (migração segura)
        for col, tipo in [
            ("nome_mercadinho",     "TEXT"),
            ("assinatura_asaas_id", "TEXT"),
            ("trial_usado",         "BOOLEAN DEFAULT FALSE"),
            ("telefone",            "TEXT"),
            ("cep",                 "TEXT"),
            ("endereco_numero",     "TEXT"),
            ("ultimo_checkout_id",  "TEXT"),
        ]:
            try:
                await conn.execute(f"ALTER TABLE usuarios ADD COLUMN IF NOT EXISTS {col} {tipo}")
            except Exception:
                pass
        # ─── Benchmark entre clientes — produtos campeões, dados agregados
        # e anonimizados (nunca por chat_id individual em consultas externas) ──
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS benchmark_produtos (
                id              SERIAL PRIMARY KEY,
                chat_id         BIGINT NOT NULL,
                nome_produto    TEXT NOT NULL,
                quantidade      INTEGER NOT NULL,
                valor_total     NUMERIC(12,2) NOT NULL,
                periodo_ini     TEXT NOT NULL,
                periodo_fim     TEXT NOT NULL,
                registrado_em   TEXT NOT NULL
            )
        """)
        await conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_benchmark_produto_nome ON benchmark_produtos (nome_produto)"
        )

        # ─── Histórico de padrões detectados — evita re-alertar o mesmo
        # padrão repetidamente (rede de segurança contra spam) ──
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS padroes_detectados (
                id              SERIAL PRIMARY KEY,
                chat_id         BIGINT NOT NULL,
                tipo_padrao     TEXT NOT NULL,
                chave_padrao    TEXT NOT NULL,
                detectado_em    TEXT NOT NULL,
                notificado      BOOLEAN DEFAULT FALSE
            )
        """)
        await conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_padroes_chat_chave ON padroes_detectados (chat_id, chave_padrao)"
        )

    logger.info("✅ Banco PostgreSQL inicializado.")


async def criar_usuario(chat_id: int, nome: str, pdv_email: str) -> dict:
    agora     = datetime.now(BRASILIA).isoformat()
    trial_fim = (datetime.now(BRASILIA) + timedelta(days=7)).isoformat()
    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute("""
            INSERT INTO usuarios (chat_id, nome, email, status, trial_fim, criado_em, atualizado_em)
            VALUES ($1, $2, $3, 'pendente', $4, $5, $6)
            ON CONFLICT (chat_id) DO NOTHING
        """, chat_id, nome, pdv_email, trial_fim, agora, agora)
    return await buscar_usuario(chat_id)


async def buscar_usuario(chat_id: int) -> dict | None:
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT * FROM usuarios WHERE chat_id = $1", chat_id
        )
        return dict(row) if row else None


async def atualizar_usuario(chat_id: int, **campos):
    agora = datetime.now(BRASILIA).isoformat()
    campos["atualizado_em"] = agora
    sets  = ", ".join(f"{k} = ${i+2}" for i, k in enumerate(campos))
    vals  = [chat_id] + list(campos.values())
    pool  = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute(
            f"UPDATE usuarios SET {sets} WHERE chat_id = $1", *vals
        )


async def usuario_tem_acesso(chat_id: int) -> tuple[bool, str]:
    user = await buscar_usuario(chat_id)
    if not user:
        return False, "nao_cadastrado"

    agora  = datetime.now(BRASILIA)
    status = user["status"]

    if status == "cancelado":
        return False, "cancelado"

    # Rede de segurança: se existe assinatura_fim válida no futuro,
    # o acesso é liberado independente do valor exato de "status" —
    # isso evita bloqueios indevidos por status desatualizado (ex: pagamento
    # confirmado mas status ainda marcado como "trial" ou "bloqueado").
    assinatura_fim_raw = user.get("assinatura_fim")
    if assinatura_fim_raw:
        try:
            fim_assinatura = datetime.fromisoformat(assinatura_fim_raw)
            if agora <= fim_assinatura:
                if status != "ativo":
                    await atualizar_usuario(chat_id, status="ativo")
                return True, "ativo"
        except Exception:
            pass

    if status == "trial":
        fim = datetime.fromisoformat(user["trial_fim"])
        if agora <= fim:
            return True, "trial"
        await atualizar_usuario(chat_id, status="bloqueado")
        return False, "trial_expirado"

    if status == "ativo":
        # assinatura_fim já checada acima e estava vencida
        await atualizar_usuario(chat_id, status="bloqueado")
        return False, "expirado"

    if status == "bloqueado":
        return False, "bloqueado"

    return False, "pendente"


async def listar_usuarios_ativos() -> list:
    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT * FROM usuarios WHERE status IN ('trial', 'ativo')"
        )
        return [dict(r) for r in rows]


# ─── BENCHMARK ENTRE CLIENTES ──────────────────────────────────────────────

async def registrar_benchmark_produto(chat_id: int, nome_produto: str, quantidade: int,
                                       valor_total: float, periodo_ini: str, periodo_fim: str):
    """
    Registra o desempenho de um produto campeão de um cliente, para
    alimentar o benchmark entre operadores. Só deve ser chamado para
    produtos campeões (top do período) — nunca para a base completa,
    evitando registrar itens muito nichados/regionais que não servem
    para comparação justa entre mercadinhos diferentes.
    """
    agora = datetime.now(BRASILIA).isoformat()
    pool  = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute("""
            INSERT INTO benchmark_produtos
                (chat_id, nome_produto, quantidade, valor_total, periodo_ini, periodo_fim, registrado_em)
            VALUES ($1, $2, $3, $4, $5, $6, $7)
        """, chat_id, nome_produto, quantidade, valor_total, periodo_ini, periodo_fim, agora)


async def buscar_benchmark_produto(nome_produto: str, chat_id_excluir: int = None) -> dict:
    """
    Busca como um produto específico performa em OUTROS clientes (benchmark
    agregado). Retorna média/mediana de quantidade vendida entre os clientes
    que têm esse produto registrado como campeão, excluindo o próprio
    cliente que está perguntando (comparar consigo mesmo não tem valor).
    """
    pool = await get_pool()
    async with pool.acquire() as conn:
        if chat_id_excluir:
            rows = await conn.fetch("""
                SELECT chat_id, quantidade, valor_total
                FROM benchmark_produtos
                WHERE nome_produto ILIKE $1 AND chat_id != $2
                ORDER BY registrado_em DESC
                LIMIT 50
            """, f"%{nome_produto}%", chat_id_excluir)
        else:
            rows = await conn.fetch("""
                SELECT chat_id, quantidade, valor_total
                FROM benchmark_produtos
                WHERE nome_produto ILIKE $1
                ORDER BY registrado_em DESC
                LIMIT 50
            """, f"%{nome_produto}%")

        if not rows:
            return {"amostras": 0}

        outros_clientes = len(set(r["chat_id"] for r in rows))
        quantidades = [r["quantidade"] for r in rows]
        return {
            "amostras": len(rows),
            "outros_clientes": outros_clientes,
            "quantidade_media": round(sum(quantidades) / len(quantidades), 1),
            "quantidade_max": max(quantidades),
            "quantidade_min": min(quantidades),
        }


# ─── PADRÕES DETECTADOS (anti-spam) ────────────────────────────────────────

async def ja_notificou_padrao(chat_id: int, chave_padrao: str, dias_validade: int = 7) -> bool:
    """
    Verifica se um padrão específico (ex: 'queda_terca_filial_A') já foi
    notificado para esse usuário nos últimos N dias — evita repetir o mesmo
    alerta todo dia e virar spam. chave_padrao deve ser um identificador
    estável do padrão (tipo + contexto), não um texto livre.
    """
    pool = await get_pool()
    limite = (datetime.now(BRASILIA) - timedelta(days=dias_validade)).isoformat()
    async with pool.acquire() as conn:
        row = await conn.fetchrow("""
            SELECT id FROM padroes_detectados
            WHERE chat_id = $1 AND chave_padrao = $2
                  AND notificado = TRUE AND detectado_em >= $3
            ORDER BY detectado_em DESC LIMIT 1
        """, chat_id, chave_padrao, limite)
        return row is not None


async def registrar_padrao_notificado(chat_id: int, tipo_padrao: str, chave_padrao: str):
    """Registra que um padrão foi notificado, para a checagem anti-spam acima."""
    agora = datetime.now(BRASILIA).isoformat()
    pool  = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute("""
            INSERT INTO padroes_detectados (chat_id, tipo_padrao, chave_padrao, detectado_em, notificado)
            VALUES ($1, $2, $3, $4, TRUE)
        """, chat_id, tipo_padrao, chave_padrao, agora)
