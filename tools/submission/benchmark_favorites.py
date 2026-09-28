"""Repeatable protected-read benchmark with four synthetic accounts."""

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


def signed(owner):
    values = {'auth_date': str(int(time.time())), 'user': json.dumps({'id': owner})}
    body = '\n'.join(f'{key}={values[key]}' for key in sorted(values))
    secret = hmac.new(b'WebAppData', os.environ['MAX_BOT_TOKEN'].encode(), hashlib.sha256).digest()
    values['hash'] = hmac.new(secret, body.encode(), hashlib.sha256).hexdigest()
    return urlencode(values)


async def main():
    base = os.getenv('CHECK_MINIAPP_URL', 'http://miniapp:8766')
    owners = [int('3' + secrets.token_hex(5), 16) for _ in range(4)]
    samples, statuses = [], {}
    pool = await asyncpg.create_pool(host=os.getenv('POSTGRES_HOST', 'postgres'),
        user=os.getenv('POSTGRES_USER', 'support_router'), password=os.environ['POSTGRES_PASSWORD'],
        database=os.getenv('POSTGRES_DB', 'support_router'), min_size=1, max_size=2)
    try:
        async with httpx.AsyncClient(timeout=20, limits=httpx.Limits(max_connections=8)) as client:
            headers = []
            try:
                for owner in owners:
                    response = await client.post(base + '/api/session', json={'init_data': signed(owner)})
                    assert response.status_code == 200
                    header = {'Authorization': 'Bearer ' + response.json()['session']}
                    assert (await client.post(base + '/api/material', headers=header,
                        json={'node_id': 'exit_docs', 'save': True})).status_code == 200
                    headers.append(header)
                sem = asyncio.Semaphore(4)
                async def read(index):
                    async with sem:
                        started = time.perf_counter()
                        response = await client.get(base + '/api/favorites', headers=headers[index % 4])
                        samples.append((time.perf_counter()-started)*1000)
                        statuses[response.status_code] = statuses.get(response.status_code, 0) + 1
                await asyncio.gather(*(read(index) for index in range(200)))
            finally:
                for header in headers:
                    await client.delete(base + '/api/account', headers=header)
        for owner in owners:
            await pool.execute('DELETE FROM account_epochs WHERE owner=$1', str(owner))
    finally:
        await pool.close()
    samples.sort()
    result = {'requests': len(samples), 'concurrency': 4, 'status': statuses,
              'p50_ms': round(samples[99], 2), 'p95_ms': round(samples[189], 2),
              'p99_ms': round(samples[197], 2)}
    print(json.dumps(result))


if __name__ == '__main__':
    asyncio.run(main())
