import aiosqlite
from pathlib import Path

DB_FILE = "./data/iqono_mappings.sqlite3"

INIT_SQL = """
CREATE TABLE IF NOT EXISTS mappings (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    rp_token TEXT NOT NULL,
    order_number TEXT,
    provider TEXT NOT NULL DEFAULT 'iqono',
    provider_operation_id TEXT,
    callback_url TEXT NOT NULL,
    status TEXT,
    merchant_private_key TEXT,
    digital_wallet TEXT,
    auth_password TEXT,
    order_description TEXT,
    UNIQUE(rp_token)
);
CREATE INDEX IF NOT EXISTS ix_iqono_order_number ON mappings(order_number);
CREATE INDEX IF NOT EXISTS ix_iqono_provider_op_id ON mappings(provider_operation_id);
"""

# Migration: add order_description column if it doesn't exist
MIGRATE_SQL = """
ALTER TABLE mappings ADD COLUMN order_description TEXT;
"""


async def init_db():
    Path("./data").mkdir(parents=True, exist_ok=True)
    async with aiosqlite.connect(DB_FILE) as db:
        for stmt in INIT_SQL.strip().split(";"):
            s = stmt.strip()
            if s:
                await db.execute(s + ";")
        # Safe migration: add column if missing
        try:
            await db.execute(MIGRATE_SQL)
        except Exception:
            pass  # column already exists
        await db.commit()


async def upsert_mapping(
    rp_token: str,
    callback_url: str,
    provider_operation_id: str | None = None,
    status: str | None = None,
    order_number: str | None = None,
    merchant_private_key: str | None = None,
    digital_wallet: str | None = None,
    auth_password: str | None = None,
    order_description: str | None = None,
):
    async with aiosqlite.connect(DB_FILE) as db:
        await db.execute(
            """
            INSERT INTO mappings
              (rp_token, order_number, provider, provider_operation_id, callback_url,
               status, merchant_private_key, digital_wallet, auth_password, order_description)
            VALUES (?, ?, 'iqono', ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(rp_token) DO UPDATE SET
              order_number              = COALESCE(excluded.order_number,              mappings.order_number),
              provider_operation_id     = COALESCE(excluded.provider_operation_id,     mappings.provider_operation_id),
              callback_url              = excluded.callback_url,
              status                    = COALESCE(excluded.status,                    mappings.status),
              merchant_private_key      = COALESCE(excluded.merchant_private_key,      mappings.merchant_private_key),
              digital_wallet            = COALESCE(excluded.digital_wallet,            mappings.digital_wallet),
              auth_password             = COALESCE(excluded.auth_password,             mappings.auth_password),
              order_description         = COALESCE(excluded.order_description,         mappings.order_description)
            """,
            (rp_token, order_number, provider_operation_id, callback_url,
             status, merchant_private_key, digital_wallet, auth_password, order_description),
        )
        await db.commit()


async def get_mapping(key: str) -> dict | None:
    """Look up by rp_token → provider_operation_id → order_number."""
    if not key:
        return None
    async with aiosqlite.connect(DB_FILE) as db:
        for col in ("rp_token", "provider_operation_id", "order_number"):
            async with db.execute(
                f"""SELECT rp_token, order_number, provider_operation_id,
                           callback_url, status, merchant_private_key,
                           digital_wallet, auth_password, order_description
                    FROM mappings WHERE {col} = ?""",
                (key,),
            ) as cur:
                row = await cur.fetchone()
                if row:
                    return {
                        "rp_token":               row[0],
                        "order_number":           row[1],
                        "provider_operation_id":  row[2],
                        "callback_url":           row[3],
                        "status":                 row[4],
                        "merchant_private_key":   row[5],
                        "digital_wallet":         row[6],
                        "auth_password":          row[7],
                        "order_description":      row[8],
                    }
    return None
