"""Bounded, synthetic 30+10 minute load run. Sends no MAX messages."""

import argparse
import asyncio
from collections import defaultdict
import hashlib
import hmac
import json
import os
from pathlib import Path
import secrets
import statistics
import time
from urllib.parse import urlencode

import asyncpg
import httpx

from project.llm.integrations import headhunter, reefapi, trudvsem


def signed(owner):
    values = {'auth_date': str(int(time.time())), 'user': json.dumps({'id': owner})}
    body = '\n'.join(f'{key}={values[key]}' for key in sorted(values))
    secret = hmac.new(b'WebAppData', os.environ['MAX_BOT_TOKEN'].encode(), hashlib.sha256).digest()
    values['hash'] = hmac.new(secret, body.encode(), hashlib.sha256).hexdigest()
    return urlencode(values)


def percentiles(numbers):
    values = sorted(numbers)
    return {key: round(values[min(len(values)-1, int((len(values)-1)*fraction))], 2) if values else None
            for key, fraction in [('p50_ms', .5), ('p95_ms', .95), ('p99_ms', .99)]}


async def probe_sources(result):
    for name, call in [('hh', lambda: headhunter.search_vacancies('повар', limit=5)),
                       ('trudvsem', lambda: trudvsem.search_vacancies('повар', region_code='1600000000000', limit=5)),
                       ('reefapi', lambda: reefapi.locations('Казань'))]:
        started = time.perf_counter()
        try:
            await asyncio.wait_for(call(), timeout=35)
            outcome = 'ok'
        except TimeoutError:
            outcome = 'timeout'
        except Exception as error:
            outcome = type(error).__name__
        result.append({'source': name, 'calls': 1, 'outcome': outcome,
                       'duration_ms': round((time.perf_counter()-started)*1000, 2)})
    await headhunter.close(); await trudvsem.close(); await reefapi.close()


async def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--steady-seconds', type=int, default=1800)
    parser.add_argument('--spike-seconds', type=int, default=600)
    parser.add_argument('--base', default='http://miniapp:8766')
    parser.add_argument('--output', required=True)
    args = parser.parse_args()
    if args.steady_seconds < 1800 or args.spike_seconds < 600:
        raise ValueError('Official sustained/spike run must be at least 30+10 minutes')
    pool = await asyncpg.create_pool(host=os.getenv('POSTGRES_HOST', 'postgres'),
        user=os.getenv('POSTGRES_USER', 'support_router'), password=os.environ['POSTGRES_PASSWORD'],
        database=os.getenv('POSTGRES_DB', 'support_router'), min_size=1, max_size=3)
    results = defaultdict(list)
    statuses = defaultdict(lambda: defaultdict(int))
    samples = []
    probes = []
    owners = [int('6' + secrets.token_hex(5), 16) for _ in range(4)]
    limits = {'health': 4, 'help': 4, 'read': 4, 'write': 2, 'action': 4}
    slots = {name: asyncio.Semaphore(value) for name, value in limits.items()}
    async with httpx.AsyncClient(timeout=httpx.Timeout(15, connect=5), limits=httpx.Limits(max_connections=24)) as client:
        try:
            bearers = []
            for owner in owners:
                response = await client.post(args.base + '/api/session', json={'init_data': signed(owner)})
                assert response.status_code == 200, ('launch', response.status_code)
                bearers.append({'Authorization': 'Bearer ' + response.json()['session']})
            async def request(stage, group, index):
                headers = bearers[index % 4]
                try:
                    async with slots[group]:
                        started = time.perf_counter()
                        if group == 'health':
                            response = await client.get(args.base + '/api/health')
                        elif group == 'help':
                            response = await client.get(args.base + '/api/help/points',
                                params={'place': 'Казань', 'region': 'Татарстан', 'limit': 4})
                        elif group == 'read':
                            response = await client.get(args.base + '/api/favorites', headers=headers)
                        elif group == 'write':
                            response = await client.post(args.base + '/api/material', headers=headers,
                                json={'node_id': 'exit_docs', 'save': True})
                        else:
                            body = {'section': 'help'} if (index // 4) % 2 == 0 else {'text': 'Казань'}
                            response = await client.post(args.base + '/api/action',
                                headers={**headers, 'Idempotency-Key': secrets.token_hex(16)}, json=body)
                        elapsed = (time.perf_counter()-started)*1000
                        results[stage, group].append(elapsed)
                        statuses[stage, group][str(response.status_code)] += 1
                except httpx.TimeoutException:
                    statuses[stage, group]['timeout'] += 1
                except httpx.HTTPError:
                    statuses[stage, group]['transport_error'] += 1
            async def schedule(stage, group, period, duration, per_tick=1):
                start = time.monotonic()
                count = int(duration / period)
                tasks = set()
                for tick in range(count):
                    await asyncio.sleep(max(0, start + tick*period - time.monotonic()))
                    for index in range(per_tick):
                        task = asyncio.create_task(request(stage, group, tick*per_tick+index))
                        tasks.add(task); task.add_done_callback(tasks.discard)
                if tasks:
                    await asyncio.gather(*tasks)
            async def monitor(stage, duration):
                for _ in range(max(1, duration // 10)):
                    count = await pool.fetchval("SELECT count(*) FROM pg_stat_activity WHERE datname=current_database()")
                    samples.append({'stage': stage, 'pg_connections': count})
                    await asyncio.sleep(10)
            async def stage(name, duration, rates):
                await asyncio.gather(monitor(name, duration), *(schedule(name, group, period, duration, per_tick)
                    for group, (period, per_tick) in rates.items()))
            await probe_sources(probes)
            await stage('steady', args.steady_seconds, {'health': (1, 1), 'help': (5, 1),
                'read': (5, 4), 'write': (30, 4), 'action': (30, 4)})
            await probe_sources(probes)
            await stage('spike', args.spike_seconds, {'health': (.25, 1), 'help': (.5, 1),
                'read': (1, 4), 'write': (5, 4), 'action': (5, 4)})
        finally:
            for headers in locals().get('bearers', []):
                try:
                    await client.delete(args.base + '/api/account', headers=headers)
                except httpx.HTTPError:
                    pass
            await pool.close()
    summary = {'duration_seconds': {'steady': args.steady_seconds, 'spike': args.spike_seconds},
               'concurrency_limits': limits, 'groups': {}, 'provider_probes': probes,
               'pg_connections': {'max': max((x['pg_connections'] for x in samples), default=None),
                                  'samples': len(samples)}}
    for (stage, group), elapsed in sorted(results.items()):
        counts = dict(statuses[stage, group])
        total = sum(counts.values())
        errors = sum(value for status, value in counts.items() if status != '200')
        summary['groups'][stage + '/' + group] = {'requests': total, 'status': counts,
            'error_fraction': round(errors/total, 5) if total else None,
            'timeouts': counts.get('timeout', 0), **percentiles(elapsed)}
    Path(args.output).write_text(json.dumps(summary, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    print(json.dumps({'result': 'completed', 'groups': {k: v['requests'] for k, v in summary['groups'].items()}}))


if __name__ == '__main__':
    asyncio.run(main())
