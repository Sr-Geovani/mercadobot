"""
database.py — Banco de dados de usuários do MercadoBot SaaS
Usa SQLite via aiosqlite para operações assíncronas.
"""
import aiosqlite
import logging
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

logger = logging.getLogger(__name__)
BRASILIA = ZoneInfo("America/Sao_Paulo")
DB_PATH  = Path("/app/data/mercadobot.db")


async def inicializar_banco():
    """Cria as tabelas se não existirem."""
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("""
            CREATE TABLE IF NOT EXISTS usuarios (
                chat_id         INTEGER PRIMARY KEY,
                nome            TEXT,
                email           TEXT,
                pdv_email       TEXT,
                pdv_senha       TEXT,
                status          TEXT DEFAULT 'pendente',
                plano           TEXT DEFAULT 'mensal',
                trial_fim       TEXT,
                assinatura_fim  TEXT,
                asaas_id        TEXT,
                criado_em       TEXT,
                atualizado_em   TEXT
            )
        """)
        # status: pendente | trial | ativo | cancelado | bloqueado
        await db.commit()
    logger.info("Banco de dados inicializado.")


async def criar_usuario(chat_id: int, nome: str, pdv_email: str) -> dict:
    agora = datetime.now(BRASILIA).isoformat()
    trial_fim = (datetime.now(BRASILIA) + timedelta(days=7)).isoformat()
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("""
            INSERT OR IGNORE INTO usuarios
            (chat_id, nome, email, status, trial_fim, criado_em, atualizado_em)
            VALUES (?, ?, ?, 'pendente', ?, ?, ?)
        """, (chat_id, nome, pdv_email, trial_fim, agora, agora))
        await db.commit()
    return await buscar_usuario(chat_id)


async def buscar_usuario(chat_id: int) -> dict | None:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT * FROM usuarios WHERE chat_id = ?", (chat_id,)
        ) as cursor:
            row = await cursor.fetchone()
            return dict(row) if row else None


async def atualizar_usuario(chat_id: int, **campos):
    agora = datetime.now(BRASILIA).isoformat()
    campos["atualizado_em"] = agora
    sets = ", ".join(f"{k} = ?" for k in campos)
    vals = list(campos.values()) + [chat_id]
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(f"UPDATE usuarios SET {sets} WHERE chat_id = ?", vals)
        await db.commit()


async def usuario_tem_acesso(chat_id: int) -> tuple[bool, str]:
    """
    Verifica se o usuário pode usar o bot.
    Retorna (tem_acesso, motivo).
    """
    user = await buscar_usuario(chat_id)
    if not user:
        return False, "nao_cadastrado"

    agora = datetime.now(BRASILIA)
    status = user["status"]

    if status == "ativo":
        fim = datetime.fromisoformat(user["assinatura_fim"])
        if agora <= fim:
            return True, "ativo"
        else:
            await atualizar_usuario(chat_id, status="bloqueado")
            return False, "expirado"

    if status == "trial":
        fim = datetime.fromisoformat(user["trial_fim"])
        if agora <= fim:
            return True, "trial"
        else:
            await atualizar_usuario(chat_id, status="bloqueado")
            return False, "trial_expirado"

    if status == "bloqueado":
        return False, "bloqueado"

    if status == "cancelado":
        return False, "cancelado"

    return False, "pendente"


async def listar_usuarios_ativos() -> list:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT * FROM usuarios WHERE status IN ('trial', 'ativo')"
        ) as cursor:
            rows = await cursor.fetchall()
            return [dict(r) for r in rows]
