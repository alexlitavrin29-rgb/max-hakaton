"""Two-instance synthetic MAX-ID check. No MAX messages are sent."""

import argparse
import asyncio
import asyncpg
import hashlib
import hmac
import json
import os
import secrets
import time
from urllib.parse import urlencode

import httpx


def signed(user_id, token):
    values = {'auth_date': str(int(time.time())), 'user': json.dumps({'id': user_id})}
    payload = '\n'.join(f'{key}={values[key]}' for key in sorted(values))
    secret = hmac.new(b'WebAppData', token.encode(), hashlib.sha256).digest()
    values['hash'] = hmac.new(secret, payload.encode(), hashlib.sha256).hexdigest()
    return urlencode(values)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--first', default='http://127.0.0.1:18866')
    parser.add_argument('--second', default='http://127.0.0.1:18867')
    args = parser.parse_args()
    token = os.environ['MAX_BOT_TOKEN']
    owners = [int('9' + secrets.token_hex(5), 16), int('8' + secrets.token_hex(5), 16)]
    with httpx.Client(timeout=20) as client:
        bearer = []
        for owner in owners:
            response = client.post(args.second + '/api/session', json={'init_data': signed(owner, token)})
            assert response.status_code == 200, ('launch', response.status_code)
            bearer.append(response.json()['session'])
        headers = [{'Authorization': 'Bearer ' + key} for key in bearer]
        first_key = secrets.token_hex(16)
        second_key = secrets.token_hex(16)
        opened = client.post(args.second + '/api/action',
            headers={**headers[0], 'Idempotency-Key': first_key}, json={'section': 'help'})
        assert opened.status_code == 200
        time.sleep(.5)
        response = client.post(args.first + '/api/action',
            headers={**headers[0], 'Idempotency-Key': second_key}, json={'text': 'Казань'})
        assert response.status_code == 200 and response.json()['section'] == 'help', ('transition', response.status_code, response.json().get('detail'), response.json().get('section'))
        assert response.json()['controls'], 'navigation lost across instances'
        old_retry = client.post(args.first + '/api/action',
            headers={**headers[0], 'Idempotency-Key': first_key}, json={'section': 'help'})
        assert old_retry.status_code == 200 and old_retry.json() == opened.json()
        again = client.post(args.second + '/api/action',
            headers={**headers[0], 'Idempotency-Key': second_key}, json={'text': 'Казань'})
        assert again.status_code == 200 and again.json() == response.json(), 'retry changed state'
        saved = client.post(args.first + '/api/material', headers=headers[0], json={'node_id': 'exit_docs', 'save': True})
        assert saved.status_code == 200, ('save', saved.status_code)
        assert len(client.get(args.second + '/api/favorites', headers=headers[0]).json()['cards']) == 1
        assert client.get(args.first + '/api/favorites', headers=headers[1]).json()['cards'] == []
        assert client.delete(args.second + '/api/account', headers=headers[0]).status_code == 200
        assert client.get(args.first + '/api/favorites', headers=headers[0]).status_code == 401
        assert client.get(args.second + '/api/favorites', headers=headers[1]).status_code == 200
        assert client.delete(args.first + '/api/account', headers=headers[1]).status_code == 200
    async def clear_test_markers():
        connection = await asyncpg.connect(host=os.getenv('POSTGRES_HOST', 'postgres'),
            user=os.getenv('POSTGRES_USER', 'support_router'),
            password=os.environ['POSTGRES_PASSWORD'], database=os.getenv('POSTGRES_DB', 'support_router'))
        try:
            for owner in owners:
                await connection.execute('DELETE FROM account_epochs WHERE owner=$1', str(owner))
        finally:
            await connection.close()
    asyncio.run(clear_test_markers())
    print(json.dumps({'checks': ['cross_instance', 'navigation', 'retry', 'isolation', 'revoke', 'cleanup'],
                      'result': 'passed'}))


if __name__ == '__main__':
    main()
