"""Exercise action retries against a running preview backed by PostgreSQL."""

import argparse
import secrets
import time

import httpx


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--first', default='http://127.0.0.1:18866')
    parser.add_argument('--second', default='http://127.0.0.1:18866')
    args = parser.parse_args()
    with httpx.Client(timeout=20) as client:
        for base in {args.first, args.second}:
            for _ in range(40):
                try:
                    if client.get(base + '/api/health').status_code == 200:
                        break
                except httpx.HTTPError:
                    pass
                time.sleep(.25)
            else:
                raise AssertionError('Preview is not healthy: ' + base)
        for _ in range(12):
            response = client.post(args.first + '/api/session', json={})
            if response.status_code != 429:
                break
            time.sleep(.5)
        response.raise_for_status()
        headers = {'Authorization': 'Bearer ' + response.json()['session']}
        key = secrets.token_hex(16)
        first = client.post(args.first + '/api/action', headers={**headers, 'Idempotency-Key': key},
                            json={'section': 'home'})
        assert first.status_code == 200, first.status_code
        repeated = client.post(args.second + '/api/action', headers={**headers, 'Idempotency-Key': key},
                               json={'section': 'home'})
        assert repeated.status_code == 200 and repeated.json() == first.json(), ('retry', repeated.status_code)
        time.sleep(.5)
        changed = client.post(args.second + '/api/action', headers={**headers, 'Idempotency-Key': key},
                              json={'section': 'help'})
        assert changed.status_code == 409, ('same key, different body', changed.status_code)
        assert 'ключ' in changed.json()['detail'].lower()
        fresh = client.post(args.first + '/api/action',
                            headers={**headers, 'Idempotency-Key': secrets.token_hex(16)},
                            json={'section': 'help'})
        assert fresh.status_code == 200, ('fresh key after rejected conflict', fresh.status_code)
        assert client.delete(args.first + '/api/account', headers=headers).status_code == 200
        assert client.post(args.second + '/api/action', headers={**headers, 'Idempotency-Key': key},
                           json={'section': 'home'}).status_code == 401
    print('action idempotency: retry, conflict, next action, account deletion passed')


if __name__ == '__main__':
    main()
