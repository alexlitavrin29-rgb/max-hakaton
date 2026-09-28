"""Synthetic two-account deletion check; run inside the miniapp container."""
import asyncio
import hashlib
import hmac
import json
import os
import secrets
import time
from urllib.parse import urlencode

import asyncpg
import httpx


BASE = 'http://127.0.0.1:8766'


def launch(user_id):
    values = {'auth_date': str(int(time.time())), 'user': json.dumps({'id': user_id})}
    secret = hmac.new(b'WebAppData', os.environ['MAX_BOT_TOKEN'].encode(), hashlib.sha256).digest()
    payload = '\n'.join(f'{key}={values[key]}' for key in sorted(values))
    values['hash'] = hmac.new(secret, payload.encode(), hashlib.sha256).hexdigest()
    return urlencode(values)


async def main():
    first, second = 9999999984101, 9999999984102
    reminder_id = secrets.token_hex(8)
    pool = await asyncpg.create_pool(host=os.getenv('POSTGRES_HOST', 'postgres'),
        user=os.getenv('POSTGRES_USER', 'support_router'), password=os.environ['POSTGRES_PASSWORD'],
        database=os.getenv('POSTGRES_DB', 'support_router'), min_size=1, max_size=1)
    try:
        async with httpx.AsyncClient(base_url=BASE, timeout=15) as client:
            sessions = []
            for owner in (first, second):
                response = await client.post('/api/session', json={'init_data': launch(owner)})
                response.raise_for_status()
                sessions.append({'Authorization': 'Bearer ' + response.json()['session']})
            a, b = sessions
            for headers in (a, b):
                response = await client.post('/api/material', json={'node_id': 'exit_docs', 'save': True}, headers=headers)
                response.raise_for_status()
            await pool.execute("INSERT INTO reminders(id,user_id,text,due_at,zone) VALUES($1,$2,$3,now()+interval '2 years',$4)",
                               reminder_id, first, 'synthetic deletion check', 'Europe/Moscow')
            assert len((await client.get('/api/favorites', headers=a)).json()['cards']) == 1
            response = await client.delete('/api/account', headers=a)
            response.raise_for_status()
            assert response.json() == {'deleted': True}
            assert (await client.get('/api/favorites', headers=a)).status_code == 401
            assert len((await client.get('/api/favorites', headers=b)).json()['cards']) == 1
            assert await pool.fetchval('SELECT count(*) FROM miniapp_favorites WHERE owner=$1', str(first)) == 0
            assert await pool.fetchval('SELECT count(*) FROM reminders WHERE user_id=$1', first) == 0
            response = await client.delete('/api/account', headers=b)
            response.raise_for_status()
            print(json.dumps({'two_accounts': 'isolated', 'favorites_deleted': True,
                              'reminders_deleted': True, 'sessions_revoked': True}))
    finally:
        await pool.execute('DELETE FROM miniapp_favorites WHERE owner=ANY($1::text[])', [str(first), str(second)])
        await pool.execute('DELETE FROM reminders WHERE user_id=ANY($1::bigint[])', [first, second])
        await pool.close()


if __name__ == '__main__':
    asyncio.run(main())
