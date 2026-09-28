"""PostgreSQL-backed mini-app state and action claims.

Claims keep a connection free during supplier calls. A result and its state are
committed together; a lost HTTP response can be read again with the same input.
"""

import hashlib
import json
import secrets

from fastapi import HTTPException

from project.llm.services.session_codec import decode_flow, encode_flow


def token_hash(token):
    return hashlib.sha256(token.encode()).digest()


def pack(state):
    return {'version': 1, 'flow': encode_flow(state.engine.session(1)),
            'section': state.section, 'offered': sorted(state.offered),
            'seen_cards': state.seen_cards}


def unpack(document, config, revision, session_type, dialogue_type, owner):
    if document.get('version') != 1:
        raise ValueError('Unsupported mini-app state version')
    engine = dialogue_type(config, revision)
    engine.sessions[1] = decode_flow(document['flow'])
    return session_type(engine, owner=owner, section=document['section'],
                        offered=set(document['offered']), seen_cards=document['seen_cards'])


class SessionStore:
    def __init__(self, pool):
        self.pool = pool

    async def initialize(self):
        await self.pool.execute('''
            CREATE TABLE IF NOT EXISTS miniapp_sessions (
                bearer_hash BYTEA PRIMARY KEY, owner TEXT NOT NULL,
                state JSONB NOT NULL, expires_at TIMESTAMPTZ NOT NULL,
                last_request TIMESTAMPTZ, claim TEXT, claim_until TIMESTAMPTZ,
                last_input BYTEA, last_response JSONB, last_completed TIMESTAMPTZ,
                revision BIGINT NOT NULL DEFAULT 0);
            CREATE INDEX IF NOT EXISTS miniapp_sessions_owner ON miniapp_sessions(owner);
            CREATE INDEX IF NOT EXISTS miniapp_sessions_expiry ON miniapp_sessions(expires_at);
            CREATE TABLE IF NOT EXISTS miniapp_action_results (
                bearer_hash BYTEA NOT NULL REFERENCES miniapp_sessions(bearer_hash) ON DELETE CASCADE,
                key_hash BYTEA NOT NULL, body_hash BYTEA NOT NULL, response JSONB NOT NULL,
                expires_at TIMESTAMPTZ NOT NULL,
                PRIMARY KEY(bearer_hash,key_hash));
            CREATE INDEX IF NOT EXISTS miniapp_action_results_expiry ON miniapp_action_results(expires_at);
            CREATE TABLE IF NOT EXISTS miniapp_launches (
                owner TEXT PRIMARY KEY, launched_at TIMESTAMPTZ NOT NULL);
            CREATE TABLE IF NOT EXISTS account_epochs (
                owner TEXT PRIMARY KEY, epoch BIGINT NOT NULL DEFAULT 0,
                changed_at TIMESTAMPTZ NOT NULL DEFAULT now());
            ALTER TABLE account_epochs ADD COLUMN IF NOT EXISTS changed_at
                TIMESTAMPTZ NOT NULL DEFAULT now();
            CREATE TABLE IF NOT EXISTS bot_sessions (
                user_id BIGINT PRIMARY KEY, state JSONB NOT NULL,
                expires_at TIMESTAMPTZ NOT NULL);
            CREATE TABLE IF NOT EXISTS bot_processed_events (
                event_id TEXT PRIMARY KEY, user_id BIGINT NOT NULL,
                result JSONB NOT NULL, expires_at TIMESTAMPTZ NOT NULL);
            CREATE TABLE IF NOT EXISTS reminders (
                id TEXT PRIMARY KEY, user_id BIGINT NOT NULL,
                text TEXT NOT NULL, due_at TIMESTAMPTZ NOT NULL,
                zone TEXT NOT NULL, state TEXT NOT NULL DEFAULT 'pending',
                changed_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                CHECK (state IN ('pending','sending','delivered','expired','failed')));
        ''')
        # The old cache keyed a hash of key+body, so a changed body bypassed it.
        # Its five-minute entries cannot be mapped back to keys; discard only that
        # transient cache when upgrading an existing database.
        old_schema = await self.pool.fetchval('''SELECT EXISTS (
            SELECT 1 FROM information_schema.columns WHERE table_name='miniapp_action_results'
            AND column_name='input_hash')''')
        if old_schema:
            await self.pool.execute('''DROP TABLE miniapp_action_results;
                CREATE TABLE miniapp_action_results (
                    bearer_hash BYTEA NOT NULL REFERENCES miniapp_sessions(bearer_hash) ON DELETE CASCADE,
                    key_hash BYTEA NOT NULL, body_hash BYTEA NOT NULL, response JSONB NOT NULL,
                    expires_at TIMESTAMPTZ NOT NULL,
                    PRIMARY KEY(bearer_hash,key_hash));
                CREATE INDEX miniapp_action_results_expiry ON miniapp_action_results(expires_at);''')
        await self.pool.execute('''UPDATE miniapp_sessions SET last_input=NULL,
            last_response=NULL,last_completed=NULL WHERE last_response IS NOT NULL''')

    async def create(self, owner, state):
        bearer = secrets.token_urlsafe(32)
        async with self.pool.acquire() as connection:
            async with connection.transaction():
                await connection.execute('SELECT pg_advisory_xact_lock(71922001)')
                await connection.execute('DELETE FROM miniapp_sessions WHERE expires_at < now()')
                await connection.execute('DELETE FROM miniapp_action_results WHERE expires_at < now()')
                await connection.execute('''DELETE FROM account_epochs e
                    WHERE changed_at < now()-interval '1 day'
                    AND NOT EXISTS (SELECT 1 FROM miniapp_sessions s WHERE s.owner=e.owner)
                    AND NOT EXISTS (SELECT 1 FROM bot_sessions b WHERE b.user_id::text=e.owner)''')
                await connection.execute("DELETE FROM miniapp_launches WHERE launched_at < now()-interval '5 seconds'")
                count = await connection.fetchval('SELECT count(*) FROM miniapp_sessions')
                if count >= 200:
                    raise HTTPException(503, 'Сейчас много запросов. Попробуйте немного позже.')
                result = await connection.execute('''INSERT INTO miniapp_launches(owner,launched_at)
                    VALUES($1,now()) ON CONFLICT(owner) DO NOTHING''', owner)
                if result != 'INSERT 0 1':
                    raise HTTPException(429, 'Подождите несколько секунд и повторите.')
                await connection.execute('''INSERT INTO miniapp_sessions(bearer_hash,owner,state,expires_at)
                    VALUES($1,$2,$3::jsonb,now()+interval '1 hour')''',
                    token_hash(bearer), owner, json.dumps(pack(state), ensure_ascii=False))
        return bearer

    async def read(self, bearer, *, touch=True):
        if not bearer:
            raise HTTPException(401, 'Сеанс завершён. Откройте приложение заново.')
        row = await self.pool.fetchrow('''UPDATE miniapp_sessions SET
            expires_at=CASE WHEN $2 THEN now()+interval '1 hour' ELSE expires_at END
            WHERE bearer_hash=$1 AND expires_at>now() RETURNING owner,state''', token_hash(bearer), touch)
        if not row:
            raise HTTPException(401, 'Сеанс завершён. Откройте приложение заново.')
        return row

    async def owner(self, bearer):
        if not bearer:
            raise HTTPException(401, 'Сеанс завершён. Откройте приложение заново.')
        owner = await self.pool.fetchval('''UPDATE miniapp_sessions
            SET expires_at=now()+interval '1 hour'
            WHERE bearer_hash=$1 AND expires_at>now() RETURNING owner''', token_hash(bearer))
        if owner is None:
            raise HTTPException(401, 'Сеанс завершён. Откройте приложение заново.')
        return owner

    async def claim(self, bearer, key_bytes, body_bytes):
        key_hash = hashlib.sha256(key_bytes).digest()
        body_hash = hashlib.sha256(body_bytes).digest()
        async with self.pool.acquire() as connection:
            async with connection.transaction():
                row = await connection.fetchrow('''SELECT owner,state,claim,claim_until,last_request FROM miniapp_sessions
                    WHERE bearer_hash=$1 AND expires_at>now() FOR UPDATE''', token_hash(bearer))
                if not row:
                    raise HTTPException(401, 'Сеанс завершён. Откройте приложение заново.')
                replay = await connection.fetchrow('''SELECT body_hash,response FROM miniapp_action_results
                    WHERE bearer_hash=$1 AND key_hash=$2 AND expires_at>now()''', token_hash(bearer), key_hash)
                if replay is not None:
                    if replay['body_hash'] != body_hash:
                        raise HTTPException(409, 'Этот ключ уже использован с другим действием.')
                    return row, None, json.loads(replay['response'])
                if row['claim'] and await connection.fetchval('SELECT $1::timestamptz > now()', row['claim_until']):
                    raise HTTPException(429, 'Дождитесь ответа и повторите.')
                recent = row['last_request'] and await connection.fetchval("SELECT $1::timestamptz > now()-interval '400 milliseconds'", row['last_request'])
                if recent:
                    raise HTTPException(429, 'Дождитесь ответа и повторите.')
                claim = secrets.token_hex(16)
                await connection.execute('''UPDATE miniapp_sessions SET claim=$2,
                    claim_until=now()+interval '130 seconds',last_request=now()
                    WHERE bearer_hash=$1''', token_hash(bearer), claim)
                return row, claim, None

    async def finish(self, bearer, claim, key_bytes, body_bytes, state, response):
        async with self.pool.acquire() as connection:
          async with connection.transaction():
            result = await connection.execute('''UPDATE miniapp_sessions SET state=$3::jsonb,
            claim=NULL,claim_until=NULL,expires_at=now()+interval '1 hour',revision=revision+1
            WHERE bearer_hash=$1 AND claim=$2 AND expires_at>now()''',
            token_hash(bearer), claim, json.dumps(pack(state), ensure_ascii=False))
            if result != 'UPDATE 1':
                raise HTTPException(401, 'Сеанс завершён. Откройте приложение заново.')
            await connection.execute('''INSERT INTO miniapp_action_results
                (bearer_hash,key_hash,body_hash,response,expires_at)
                VALUES($1,$2,$3,$4::jsonb,now()+interval '5 minutes')''', token_hash(bearer),
                hashlib.sha256(key_bytes).digest(), hashlib.sha256(body_bytes).digest(),
                json.dumps(response, ensure_ascii=False))

    async def release(self, bearer, claim):
        await self.pool.execute('''UPDATE miniapp_sessions SET claim=NULL,claim_until=NULL,
            last_request=NULL WHERE bearer_hash=$1 AND claim=$2''', token_hash(bearer), claim)

    async def delete_owner(self, owner, connection):
        await connection.execute('DELETE FROM miniapp_sessions WHERE owner=$1', owner)
