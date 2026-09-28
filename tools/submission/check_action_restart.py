"""Check that an action result survives a miniapp restart on PostgreSQL."""

import argparse
import secrets
import subprocess
import time

import httpx


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--project', required=True)
    parser.add_argument('--base', default='http://127.0.0.1:18866')
    args = parser.parse_args()
    compose = ['docker', 'compose', '-p', args.project, '--env-file', 'submission/.env',
               '-f', 'submission/compose.yaml']
    with httpx.Client(timeout=20) as client:
        for _ in range(12):
            response = client.post(args.base + '/api/session', json={})
            if response.status_code != 429:
                break
            time.sleep(.5)
        response.raise_for_status()
        headers = {'Authorization': 'Bearer ' + response.json()['session']}
        key = secrets.token_hex(16)
        first = client.post(args.base + '/api/action', json={'section': 'home'},
                            headers={**headers, 'Idempotency-Key': key})
        assert first.status_code == 200
        subprocess.run([*compose, 'restart', 'miniapp'], check=True, capture_output=True)
        for _ in range(50):
            try:
                if client.get(args.base + '/api/health').status_code == 200:
                    break
            except httpx.HTTPError:
                pass
            time.sleep(.2)
        else:
            raise AssertionError('Miniapp did not recover')
        repeated = client.post(args.base + '/api/action', json={'section': 'home'},
                               headers={**headers, 'Idempotency-Key': key})
        assert repeated.status_code == 200 and repeated.json() == first.json()
        conflict = client.post(args.base + '/api/action', json={'section': 'help'},
                               headers={**headers, 'Idempotency-Key': key})
        assert conflict.status_code == 409
        assert client.delete(args.base + '/api/account', headers=headers).status_code == 200
    print('action idempotency after restart: passed')


if __name__ == '__main__':
    main()
