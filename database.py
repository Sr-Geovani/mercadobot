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
        ]:
            try:
                await conn.execute(f"ALTER TABLE usuarios ADD COLUMN IF NOT EXISTS {col} {tipo}")
            except Exception:
                pass
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
