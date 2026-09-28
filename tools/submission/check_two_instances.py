"""Demonstrate current process-local sessions without MAX messages."""
import hashlib
import hmac
import json
import os
import subprocess
import time
from urllib.parse import urlencode

import httpx


values = {'auth_date': str(int(time.time())), 'user': json.dumps({'id': 9999999984301})}
secret = hmac.new(b'WebAppData', os.environ['MAX_BOT_TOKEN'].encode(), hashlib.sha256).digest()
payload = '\n'.join(f'{key}={values[key]}' for key in sorted(values))
values['hash'] = hmac.new(secret, payload.encode(), hashlib.sha256).hexdigest()
second = subprocess.Popen(['uvicorn', 'project.miniapp.app:app', '--host', '127.0.0.1', '--port', '8767'],
                          stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
try:
    with httpx.Client(timeout=5) as client:
        for _ in range(30):
            try:
                if client.get('http://127.0.0.1:8767/api/health').status_code == 200:
                    break
            except httpx.HTTPError:
                pass
            time.sleep(.2)
        else:
            raise RuntimeError('Second instance did not become healthy')
        response = client.post('http://127.0.0.1:8766/api/session', json={'init_data': urlencode(values)})
        response.raise_for_status()
        headers = {'Authorization': 'Bearer ' + response.json()['session']}
        primary = client.get('http://127.0.0.1:8766/api/favorites', headers=headers)
        secondary = client.get('http://127.0.0.1:8767/api/favorites', headers=headers)
        assert primary.status_code == 200 and secondary.status_code == 401
        print(json.dumps({'first_instance': 200, 'second_instance': 401,
                          'sessions_shared': False}))
finally:
    second.terminate()
    second.wait(timeout=5)
