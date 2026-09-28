"""Database-backed session, TTL, capacity and deletion race checks using synthetic IDs."""

import asyncio
from datetime import datetime, timedelta, timezone
import hashlib
import hmac
import json
import os
import secrets
import time
from urllib.parse import urlencode

import asyncpg
import httpx

from project.llm.services.account_epoch import EXPECTED_EPOCH, read_epoch
from project.llm.services.reminders import ReminderStore
from project.llm.services.bot_sessions import BotSessions
from project.llm.services.flow import FlowSession, PublishedDialogue, PreviewReminders
from project.llm.services.scenario import ScenarioStore
from project.miniapp.session_store import SessionStore, token_hash

BASE = os.getenv('CHECK_MINIAPP_URL', 'http://miniapp_second:8766')


def signed(owner):
    values = {'auth_date': str(int(time.time())), 'user': json.dumps({'id': owner})}
    body = '\n'.join(f'{key}={values[key]}' for key in sorted(values))
    secret = hmac.new(b'WebAppData', os.environ['MAX_BOT_TOKEN'].encode(), hashlib.sha256).digest()
    values['hash'] = hmac.new(secret, body.encode(), hashlib.sha256).hexdigest()
    return urlencode(values)


async def blocked(pool):
    for _ in range(100):
        if await pool.fetchval("SELECT count(*) FROM pg_locks WHERE locktype='advisory' AND NOT granted"):
            return
        await asyncio.sleep(.02)
    raise AssertionError('Expected owner-lock waiter was not observed')


async def race(pool, client, order, reminder=False):
    owner = int('7' + secrets.token_hex(5), 16)
    launch = await client.post(BASE + '/api/session', json={'init_data': signed(owner)})
    assert launch.status_code == 200
    bearer = launch.json()['session']
    headers = {'Authorization': 'Bearer ' + bearer}
    opened = await client.post(BASE + '/api/material', headers=headers, json={'node_id': 'exit_docs'})
    assert opened.status_code == 200
    card_id = opened.json()['card']['favorite_id']
    generation = await read_epoch(pool, owner)
    async def save():
        if reminder:
            token = EXPECTED_EPOCH.set(generation)
            try:
                return await ReminderStore(pool).create(owner, 'Синтетическое напоминание',
                    datetime.now(timezone.utc) + timedelta(days=1), 'Europe/Moscow')
            finally:
                EXPECTED_EPOCH.reset(token)
        return await client.post(BASE + '/api/favorites', headers=headers,
                                 json={'card_id': card_id, 'saved': True})
    async def delete():
        return await client.delete(BASE + '/api/account', headers=headers)
    connection = await pool.acquire()
    try:
        transaction = connection.transaction()
        await transaction.start()
        await connection.execute('SELECT pg_advisory_xact_lock(hashtextextended($1,0))', str(owner))
        first = asyncio.create_task(save() if order == 'save_first' else delete())
        await blocked(pool)
        second = asyncio.create_task(delete() if order == 'save_first' else save())
        await asyncio.sleep(.05)
        await transaction.commit()
        results = await asyncio.gather(first, second, return_exceptions=True)
    finally:
        await pool.release(connection)
    if order == 'save_first':
        assert not isinstance(results[0], Exception) and (reminder or results[0].status_code == 200)
        assert results[1].status_code == 200
    else:
        assert results[0].status_code == 200
        assert isinstance(results[1], ValueError) if reminder else results[1].status_code == 401
    assert await pool.fetchval('SELECT count(*) FROM miniapp_favorites WHERE owner=$1', str(owner)) == 0
    assert await pool.fetchval('SELECT count(*) FROM reminders WHERE user_id=$1', owner) == 0
    assert await pool.fetchval('SELECT count(*) FROM miniapp_sessions WHERE owner=$1', str(owner)) == 0
    assert await pool.fetchval('SELECT count(*) FROM miniapp_action_results WHERE bearer_hash=$1',
                               token_hash(bearer)) == 0
    await pool.execute('DELETE FROM account_epochs WHERE owner=$1', str(owner))


async def main():
    pool = await asyncpg.create_pool(host=os.getenv('POSTGRES_HOST', 'postgres'),
        user=os.getenv('POSTGRES_USER', 'support_router'), password=os.environ['POSTGRES_PASSWORD'],
        database=os.getenv('POSTGRES_DB', 'support_router'), min_size=1, max_size=5)
    try:
        async with httpx.AsyncClient(timeout=20) as client:
            for reminder in (False, True):
                for order in ('save_first', 'delete_first'):
                    await race(pool, client, order, reminder)
        sessions = SessionStore(pool)
        expired = secrets.token_urlsafe(32)
        await pool.execute('''INSERT INTO miniapp_sessions(bearer_hash,owner,state,expires_at)
            VALUES($1,'synthetic-expired','{}'::jsonb,now()-interval '1 second')''', token_hash(expired))
        try:
            await sessions.read(expired)
        except Exception as error:
            assert getattr(error, 'status_code', None) == 401
        else:
            raise AssertionError('Expired bearer accepted')
        await pool.execute("DELETE FROM miniapp_sessions WHERE owner='synthetic-expired'")
        count = await pool.fetchval('SELECT count(*) FROM miniapp_sessions WHERE expires_at>now()')
        assert count < 200, 'Capacity test needs a quiet clean environment'
        try:
            for _ in range(200 - count):
                await pool.execute('''INSERT INTO miniapp_sessions(bearer_hash,owner,state,expires_at)
                    VALUES($1,'synthetic-capacity','{}'::jsonb,now()+interval '1 hour')''',
                    token_hash(secrets.token_urlsafe(32)))
            try:
                await sessions.create('synthetic-overflow', None)
            except Exception as error:
                assert getattr(error, 'status_code', None) == 503
            else:
                raise AssertionError('Session capacity exceeded')
        finally:
            await pool.execute("DELETE FROM miniapp_sessions WHERE owner='synthetic-capacity'")
        bot = BotSessions(pool)
        await bot.initialize()
        bot_owner = int('5' + secrets.token_hex(5), 16)
        event_id = secrets.token_hex(12)
        try:
            await bot.save(bot_owner, FlowSession(node='work_search', work={'offset': 2}),
                           event_id, [{'text': 'Синтетический ответ'}])
            assert await bot.processed(event_id)
            restored = await bot.load(bot_owner)
            assert restored.node == 'work_search' and restored.work['offset'] == 2
        finally:
            await bot.delete(bot_owner)
        dialogue_owner = int('5' + secrets.token_hex(5), 16)
        dialogue_event = secrets.token_hex(12)
        try:
            first = PublishedDialogue(PreviewReminders(), ScenarioStore(pool), bot)
            replies = await first.handle(dialogue_owner, payload='jump:help_points', event_id=dialogue_event)
            assert replies and await bot.processed(dialogue_event)
            second = PublishedDialogue(PreviewReminders(), ScenarioStore(pool), bot)
            assert await second.handle(dialogue_owner, payload='jump:help_points',
                                       event_id=dialogue_event) is None
            assert (await bot.load(dialogue_owner)).node == (await first.refresh()).session(dialogue_owner).node
        finally:
            await bot.delete(dialogue_owner)
        print(json.dumps({'result': 'passed', 'checks': ['save_delete_both_orders',
            'reminder_delete_both_orders', 'ttl', 'capacity', 'bot_state',
            'bot_event_replay', 'cleanup']}))
    finally:
        await pool.close()


if __name__ == '__main__':
    asyncio.run(main())
