"""Real PostgreSQL checks in an isolated temporary schema; no MAX messages."""

import asyncio
from datetime import datetime, timedelta, timezone
import os
import secrets

import asyncpg

from .services.reminders import ReminderStore


async def main():
    config = dict(host=os.environ.get("POSTGRES_HOST", "127.0.0.1"),
                  port=int(os.environ.get("POSTGRES_PORT", "5432")),
                  user=os.environ.get("POSTGRES_USER", "support_router"),
                  password=os.environ["POSTGRES_PASSWORD"],
                  database=os.environ.get("POSTGRES_DB", "support_router"))
    schema = "smoke_" + secrets.token_hex(8)
    admin = await asyncpg.connect(**config)
    pool = None
    try:
        await admin.execute(f'CREATE SCHEMA "{schema}"')
        pool = await asyncpg.create_pool(**config, min_size=1, max_size=2,
                                        server_settings={"search_path": schema})
        store = ReminderStore(pool)
        await store.initialize()
        future = datetime.now(timezone.utc) + timedelta(days=1)
        key = await store.create(-1, "Synthetic check", future, "Europe/Moscow")
        assert await store.get(-2, key) is None
        await store.delete(-2, key)
        assert not await store.reschedule(-2, key, future, "Europe/Moscow")
        await pool.close()
        pool = await asyncpg.create_pool(**config, min_size=1, max_size=2,
                                        server_settings={"search_path": schema})
        store = ReminderStore(pool)
        await store.initialize()
        assert (await store.get(-1, key))["text"] == "Synthetic check"
        await pool.execute("UPDATE reminders SET due_at=now()-interval '1 second' WHERE id=$1", key)
        rows = await store.claim_due()
        assert len(rows) == 1 and rows[0]["id"] == key
        assert not await store.claim_due()
        await store.finish_send(key, True)
        assert (await store.get(-1, key))["state"] == "delivered"
        assert await store.reschedule(-1, key, future, "Europe/Moscow")
        await pool.execute("UPDATE reminders SET due_at=now()-interval '1 second' WHERE id=$1", key)
        await store.initialize()
        assert (await store.get(-1, key))["state"] == "expired"
        assert (await store.get(-1, key))["text"] == ""
        await pool.execute("UPDATE reminders SET changed_at=now()-interval '8 days' WHERE id=$1", key)
        await store.cleanup()
        assert await store.get(-1, key) is None
        key = await store.create(-1, "Cancellation", future, "Europe/Moscow")
        await store.delete(-1, key)
        assert not await store.list(-1)
        print("PASS: persistence, ownership, one claim, delivery state, snooze, downtime expiry, seven-day cleanup, cancellation")
    finally:
        if pool: await pool.close()
        await admin.execute(f'DROP SCHEMA "{schema}" CASCADE')
        await admin.close()


if __name__ == "__main__":
    asyncio.run(main())
