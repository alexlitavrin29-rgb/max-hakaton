"""Persistent state for the singleton MAX poller."""

import json

from .session_codec import decode_flow, encode_flow
from .account_epoch import EXPECTED_EPOCH, read_epoch


class BotSessions:
    def __init__(self, pool):
        self.pool = pool

    async def initialize(self):
        await self.pool.execute('''CREATE TABLE IF NOT EXISTS bot_sessions (
            user_id BIGINT PRIMARY KEY, state JSONB NOT NULL,
            expires_at TIMESTAMPTZ NOT NULL);
            CREATE INDEX IF NOT EXISTS bot_sessions_expiry ON bot_sessions(expires_at);
            CREATE TABLE IF NOT EXISTS bot_processed_events (
                event_id TEXT PRIMARY KEY, user_id BIGINT NOT NULL,
                result JSONB NOT NULL, expires_at TIMESTAMPTZ NOT NULL);
            CREATE INDEX IF NOT EXISTS bot_processed_events_expiry ON bot_processed_events(expires_at)''')

    async def processed(self, event_id):
        if not event_id:
            return False
        return bool(await self.pool.fetchval('''SELECT 1 FROM bot_processed_events
            WHERE event_id=$1 AND expires_at>now()''', event_id))

    async def load(self, user_id):
        row = await self.pool.fetchval('''SELECT state FROM bot_sessions
            WHERE user_id=$1 AND expires_at>now()''', user_id)
        return decode_flow(json.loads(row)) if row else None

    async def save(self, user_id, session, event_id=None, replies=None):
        expected = EXPECTED_EPOCH.get()
        if expected is None:
            expected = await read_epoch(self.pool, user_id)
        async with self.pool.acquire() as connection:
          async with connection.transaction():
            await connection.execute('SELECT pg_advisory_xact_lock(hashtextextended($1,0))', str(user_id))
            actual = await connection.fetchval('SELECT epoch FROM account_epochs WHERE owner=$1', str(user_id)) or 0
            if actual != expected:
                raise ValueError('Account was deleted during action')
            await connection.execute('''INSERT INTO bot_sessions(user_id,state,expires_at)
            VALUES($1,$2::jsonb,now()+interval '1 hour')
            ON CONFLICT(user_id) DO UPDATE SET state=EXCLUDED.state,
            expires_at=EXCLUDED.expires_at''', user_id,
            json.dumps(encode_flow(session), ensure_ascii=False))
            if event_id:
                await connection.execute('''INSERT INTO bot_processed_events(event_id,user_id,result,expires_at)
                    VALUES($1,$2,$3::jsonb,now()+interval '1 day')''',
                    str(event_id), user_id, json.dumps(replies, ensure_ascii=False))

    async def delete(self, user_id):
        await self.pool.execute('DELETE FROM bot_sessions WHERE user_id=$1', user_id)
        await self.pool.execute('DELETE FROM bot_processed_events WHERE user_id=$1', user_id)

    async def cleanup(self):
        await self.pool.execute('DELETE FROM bot_sessions WHERE expires_at<=now()')
        await self.pool.execute('DELETE FROM bot_processed_events WHERE expires_at<=now()')
