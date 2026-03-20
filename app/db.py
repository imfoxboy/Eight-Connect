import aiosqlite
from pathlib import Path

DB_FILE = "./data/mappings.sqlite3"

INIT_SQL = '''
CREATE TABLE IF NOT EXISTS mappings (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    rp_token TEXT NOT NULL,                 -- payment.token из RP
    order_number TEXT,                      -- payment.order_number (merchant)
    provider TEXT NOT NULL,
    provider_operation_id TEXT,
    callback_url TEXT NOT NULL,
    status TEXT,
    merchant_private_key TEXT,              -- для шифрования в коллбеках
    digital_wallet TEXT,                    -- applepay | googlepay (IQONO)
    auth_password TEXT,                     -- IQONO password for callback hash validation
    UNIQUE(rp_token)
);
CREATE INDEX IF NOT EXISTS ix_mappings_order_number ON mappings(order_number);
CREATE INDEX IF NOT EXISTS ix_mappings_provider_operation_id ON mappings(provider_operation_id);
'''



async def init_db():
    Path("./data").mkdir(parents=True, exist_ok=True)
    async with aiosqlite.connect(DB_FILE) as db:
        # Выполним все стейтменты по одному
        for stmt in INIT_SQL.strip().split(';'):
            s = stmt.strip()
            if s:
                await db.execute(s + ';')

        # Мягкая миграция существующей БД под IQONO
        async with db.execute("PRAGMA table_info(mappings)") as cur:
            cols = {row[1] for row in await cur.fetchall()}
        if "digital_wallet" not in cols:
            await db.execute("ALTER TABLE mappings ADD COLUMN digital_wallet TEXT")
        if "auth_password" not in cols:
            await db.execute("ALTER TABLE mappings ADD COLUMN auth_password TEXT")

        await db.commit()


async def upsert_mapping(
    rp_token: str,
    provider: str,
    callback_url: str,
    provider_operation_id: str | None = None,
    status: str | None = None,
    order_number: str | None = None,
    merchant_private_key: str | None = None,
    digital_wallet: str | None = None,
    auth_password: str | None = None,
):
    async with aiosqlite.connect(DB_FILE) as db:
        await db.execute(
            """
            INSERT INTO mappings (rp_token, order_number, provider, provider_operation_id, callback_url, status, merchant_private_key, digital_wallet, auth_password)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(rp_token) DO UPDATE SET
              order_number=COALESCE(excluded.order_number, mappings.order_number),
              provider=excluded.provider,
              provider_operation_id=COALESCE(excluded.provider_operation_id, mappings.provider_operation_id),
              callback_url=excluded.callback_url,
              status=COALESCE(excluded.status, mappings.status),
              merchant_private_key=COALESCE(excluded.merchant_private_key, mappings.merchant_private_key),
              digital_wallet=COALESCE(excluded.digital_wallet, mappings.digital_wallet),
              auth_password=COALESCE(excluded.auth_password, mappings.auth_password)
            """,
            (rp_token, order_number, provider, provider_operation_id, callback_url, status, merchant_private_key, digital_wallet, auth_password)
        )
        await db.commit()


async def get_mapping_by_token_any(key: str):
    """
    Универсальный поиск: по rp_token, provider_operation_id (gateway_token), или order_number.
    Приоритет: rp_token -> provider_operation_id -> order_number
    """
    async with aiosqlite.connect(DB_FILE) as db:
        # rp_token
        async with db.execute(
            "SELECT rp_token, order_number, provider, provider_operation_id, callback_url, status, merchant_private_key, digital_wallet, auth_password FROM mappings WHERE rp_token = ?",
            (key,)
        ) as cur:
            row = await cur.fetchone()
            if row:
                return {
                    "rp_token": row[0],
                    "order_number": row[1],
                    "provider": row[2],
                    "provider_operation_id": row[3],
                    "callback_url": row[4],
                    "status": row[5],
                    "merchant_private_key": row[6],
                    "digital_wallet": row[7],
                    "auth_password": row[8],
                }
        # provider_operation_id (gateway_token from provider)
        async with db.execute(
            "SELECT rp_token, order_number, provider, provider_operation_id, callback_url, status, merchant_private_key, digital_wallet, auth_password FROM mappings WHERE provider_operation_id = ?",
            (key,)
        ) as cur:
            row = await cur.fetchone()
            if row:
                return {
                    "rp_token": row[0],
                    "order_number": row[1],
                    "provider": row[2],
                    "provider_operation_id": row[3],
                    "callback_url": row[4],
                    "status": row[5],
                    "merchant_private_key": row[6],
                    "digital_wallet": row[7],
                    "auth_password": row[8],
                }
        # order_number
        async with db.execute(
            "SELECT rp_token, order_number, provider, provider_operation_id, callback_url, status, merchant_private_key, digital_wallet, auth_password FROM mappings WHERE order_number = ?",
            (key,)
        ) as cur:
            row = await cur.fetchone()
            if row:
                return {
                    "rp_token": row[0],
                    "order_number": row[1],
                    "provider": row[2],
                    "provider_operation_id": row[3],
                    "callback_url": row[4],
                    "status": row[5],
                    "merchant_private_key": row[6],
                    "digital_wallet": row[7],
                    "auth_password": row[8],
                }
    return None


async def update_status_by_token_any(key: str, status: str):
    async with aiosqlite.connect(DB_FILE) as db:
        # Обновим по rp_token, если не зацепили — по order_number
        await db.execute("UPDATE mappings SET status=? WHERE rp_token=?", (status, key))
        await db.execute("UPDATE mappings SET status=? WHERE order_number=?", (status, key))
        await db.commit()

