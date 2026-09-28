"""PostgreSQL reminders only. No transcripts, profiles or checklists are persisted."""

from datetime import datetime, timedelta, timezone
import re
import secrets
from zoneinfo import ZoneInfo
from .account_epoch import EXPECTED_EPOCH, read_epoch


def parse_time(text: str, zone: str, *, now: datetime | None = None) -> datetime:
    if not re.fullmatch(r"\d{2}:\d{2},\s*\d{2}\.\d{2}\.\d{4}", text.strip()):
        raise ValueError("format")
    date = datetime.strptime(re.sub(r",\s*", ", ", text.strip()), "%H:%M, %d.%m.%Y")
    local = date.replace(tzinfo=ZoneInfo(zone))
    utc = local.astimezone(timezone.utc)
    if utc <= (now or datetime.now(timezone.utc)):
        raise ValueError("past")
    if utc > (now or datetime.now(timezone.utc)) + timedelta(days=366 * 3):
        raise ValueError("too_far")
    return utc


class ReminderStore:
    def __init__(self, pool):
        self.pool = pool

    async def initialize(self):
        await self.pool.execute("""
            CREATE TABLE IF NOT EXISTS reminders (
                id TEXT PRIMARY KEY, user_id BIGINT NOT NULL,
                text TEXT NOT NULL, due_at TIMESTAMPTZ NOT NULL,
                zone TEXT NOT NULL, state TEXT NOT NULL DEFAULT 'pending',
                changed_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                CHECK (state IN ('pending','sending','delivered','expired','failed'))
            );
            CREATE INDEX IF NOT EXISTS reminders_due ON reminders(due_at) WHERE state='pending';
            CREATE TABLE IF NOT EXISTS account_epochs (
                owner TEXT PRIMARY KEY, epoch BIGINT NOT NULL DEFAULT 0,
                changed_at TIMESTAMPTZ NOT NULL DEFAULT now());
            ALTER TABLE account_epochs ADD COLUMN IF NOT EXISTS changed_at
                TIMESTAMPTZ NOT NULL DEFAULT now();
        """)
        # Never send reminders missed while the process was stopped.
        await self.pool.execute("""
            UPDATE reminders SET state='expired', text='', changed_at=now()
            WHERE (state='pending' AND due_at <= now()) OR state='sending'
        """)
        await self.cleanup()

    async def create(self, user_id: int, text: str, due_at: datetime, zone: str) -> str:
        if not text.strip() or len(text) > 300 or due_at <= datetime.now(timezone.utc):
            raise ValueError("Invalid reminder")
        identifier = secrets.token_hex(8)
        expected = EXPECTED_EPOCH.get()
        if expected is None:
            expected = await read_epoch(self.pool, user_id)
        async with self.pool.acquire() as connection:
            async with connection.transaction():
                await connection.execute('SELECT pg_advisory_xact_lock(hashtextextended($1,0))', str(user_id))
                actual = await connection.fetchval('SELECT epoch FROM account_epochs WHERE owner=$1', str(user_id)) or 0
                if actual != expected:
                    raise ValueError('Account was deleted during action')
                await connection.execute(
                    "INSERT INTO reminders(id,user_id,text,due_at,zone) VALUES($1,$2,$3,$4,$5)",
                    identifier, user_id, text.strip(), due_at, zone)
        return identifier

    async def list(self, user_id):
        await self.cleanup()
        return await self.pool.fetch("SELECT * FROM reminders WHERE user_id=$1 ORDER BY due_at LIMIT 30", user_id)

    async def get(self, user_id, identifier):
        return await self.pool.fetchrow("SELECT * FROM reminders WHERE id=$1 AND user_id=$2", identifier, user_id)

    async def delete(self, user_id, identifier):
        await self.pool.execute("DELETE FROM reminders WHERE id=$1 AND user_id=$2", identifier, user_id)

    async def delete_user(self, user_id):
        await self.pool.execute("DELETE FROM reminders WHERE user_id=$1", user_id)

    async def reschedule(self, user_id, identifier, due_at, zone):
        if due_at <= datetime.now(timezone.utc):
            raise ValueError("past")
        result = await self.pool.execute("""
            UPDATE reminders SET due_at=$3, zone=$4, state='pending', changed_at=now()
            WHERE id=$1 AND user_id=$2 AND state IN ('pending','delivered') AND text<>''
        """, identifier, user_id, due_at, zone)
        return result == "UPDATE 1"

    async def claim_due(self):
        # More than a minute late: treat as expired, not as a delayed notification.
        await self.pool.execute("""
            UPDATE reminders SET state='expired', text='', changed_at=now()
            WHERE state='pending' AND due_at < now()-interval '60 seconds'
        """)
        return await self.pool.fetch("""
            UPDATE reminders SET state='sending', changed_at=now() WHERE id IN (
                SELECT id FROM reminders WHERE state='pending' AND due_at<=now()
                ORDER BY due_at LIMIT 10 FOR UPDATE SKIP LOCKED
            ) RETURNING *
        """)

    async def finish_send(self, identifier, success: bool):
        await self.pool.execute("""
            UPDATE reminders SET state=$2, changed_at=now(),
                text=CASE WHEN $2='delivered' THEN text ELSE '' END
            WHERE id=$1 AND state='sending'
        """, identifier, "delivered" if success else "failed")

    async def expire(self, identifier):
        await self.pool.execute("UPDATE reminders SET state='expired',text='',changed_at=now() WHERE id=$1 AND state='sending'", identifier)

    async def cleanup(self):
        # Physical deletion, not a hidden user-visible archive.
        await self.pool.execute("DELETE FROM reminders WHERE state<>'pending' AND changed_at < now()-interval '7 days'")
