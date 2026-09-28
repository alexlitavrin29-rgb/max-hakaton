"""Small internal VPS probe with synthetic MAX identity; sends no MAX messages."""
import asyncio
import hashlib
import hmac
import json
import os
import statistics
import time
from urllib.parse import urlencode

import httpx

BASE = 'http://127.0.0.1:8766'
OWNER = 9999999984401


def signed_launch():
    values = {'auth_date': str(int(time.time())), 'user': json.dumps({'id': OWNER})}
    secret = hmac.new(b'WebAppData', os.environ['MAX_BOT_TOKEN'].encode(), hashlib.sha256).digest()
    payload = '\n'.join(f'{key}={values[key]}' for key in sorted(values))
    values['hash'] = hmac.new(secret, payload.encode(), hashlib.sha256).hexdigest()
    return urlencode(values)


def summary(rows):
    times = sorted(row[0] for row in rows)
    return {'requests': len(rows), 'parallelism': 4,
            'p50_ms': round(statistics.median(times), 2),
            'p95_ms': round(times[max(0, int(len(times) * .95 + .9999) - 1)], 2),
            'error_fraction': round(sum(code >= 400 for _, code in rows) / len(rows), 3)}


async def main():
    async with httpx.AsyncClient(base_url=BASE, timeout=130) as client:
        async def batch(path, count):
            semaphore = asyncio.Semaphore(4)
            async def one():
                async with semaphore:
                    start = time.perf_counter()
                    response = await client.get(path)
                    return ((time.perf_counter() - start) * 1000, response.status_code)
            return summary(await asyncio.gather(*(one() for _ in range(count))))

        public = {'health': await batch('/api/health', 40),
                  'help_points': await batch('/api/help/points?place=Казань&region=Татарстан&limit=4', 40)}
        if os.getenv('TOCHKA_MEASURE_PUBLIC_ONLY') == '1':
            print(json.dumps({'public': public}, ensure_ascii=False))
            return
        session = await client.post('/api/session', json={'init_data': signed_launch()})
        session.raise_for_status()
        headers = {'Authorization': 'Bearer ' + session.json()['session']}
        try:
            async def protected_batch():
                semaphore = asyncio.Semaphore(4)
                async def one():
                    async with semaphore:
                        start = time.perf_counter()
                        response = await client.get('/api/favorites', headers=headers)
                        return ((time.perf_counter() - start) * 1000, response.status_code)
                return summary(await asyncio.gather(*(one() for _ in range(20))))

            protected = await protected_batch()
            writes = []
            for _ in range(2):
                start = time.perf_counter()
                response = await client.post('/api/material', json={'node_id': 'exit_docs', 'save': True}, headers=headers)
                writes.append({'status': response.status_code, 'ms': round((time.perf_counter() - start) * 1000, 2)})
            saved = await client.get('/api/favorites', headers=headers)
            assert saved.status_code == 200 and len(saved.json()['cards']) == 1

            sources = {}
            for section, query in [('work', 'Казань, оператор, без опыта, от 40000 рублей'),
                                   ('rental', 'Казань, до 30000 рублей в месяц, без залога')]:
                steps = []
                for body in ({'section': section}, {'text': query}):
                    await asyncio.sleep(.55)
                    start = time.perf_counter()
                    response = await client.post('/api/action', json=body, headers=headers)
                    steps.append({'status': response.status_code, 'ms': round((time.perf_counter() - start) * 1000, 2),
                                  'cards': len(response.json().get('cards', [])) if response.status_code == 200 else 0})
                    if response.status_code != 200:
                        break
                if steps[-1]['status'] == 200:
                    view = response.json()
                    consent = next((c for c in view.get('controls', []) if 'Искать' in c.get('label', '')), None)
                    if consent:
                        await asyncio.sleep(.55)
                        start = time.perf_counter()
                        response = await client.post('/api/action', json={'payload': consent['payload']}, headers=headers)
                        steps.append({'status': response.status_code, 'ms': round((time.perf_counter() - start) * 1000, 2),
                                      'cards': len(response.json().get('cards', [])) if response.status_code == 200 else 0})
                sources[section] = steps
            print(json.dumps({'public': public, 'protected_reads': protected,
                              'idempotent_material_writes': writes, 'sources': sources}, ensure_ascii=False))
        finally:
            await client.delete('/api/account', headers=headers)


if __name__ == '__main__':
    asyncio.run(main())
