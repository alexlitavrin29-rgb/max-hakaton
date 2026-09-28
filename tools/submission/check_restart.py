"""Preview-only restart check on an isolated Compose project."""
import json
import os
import subprocess
import time
from urllib.error import HTTPError
from urllib.request import Request, urlopen


COMPOSE = ['docker', 'compose', '-p', os.getenv('TOCHKA_CHECK_PROJECT', 'tochka-technical-rc'), '--env-file', 'submission/.env',
           '-f', 'submission/compose.yaml']
BASE = 'http://127.0.0.1:18866'


def call(path, body=None, token='', method=None):
    headers = {'Content-Type': 'application/json'}
    if token:
        headers['Authorization'] = 'Bearer ' + token
    request = Request(BASE + path, data=json.dumps(body).encode() if body is not None else None,
                      headers=headers, method=method)
    try:
        with urlopen(request, timeout=10) as response:
            return response.status, json.load(response)
    except HTTPError as error:
        return error.code, None


first = call('/api/session', {})[1]['session']
status, _ = call('/api/material', {'node_id': 'exit_docs', 'save': True}, first)
assert status == 200
subprocess.run([*COMPOSE, 'restart', 'miniapp'], check=True, capture_output=True)
for _ in range(40):
    try:
        if call('/api/health')[0] == 200:
            break
    except OSError:
        pass
    time.sleep(.25)
else:
    raise RuntimeError('Miniapp did not recover after restart')
assert call('/api/favorites', token=first)[0] == 401
second = call('/api/session', {})[1]['session']
assert len(call('/api/favorites', token=second)[1]['cards']) == 1
assert call('/api/account', token=second, method='DELETE')[0] == 200
print(json.dumps({'old_session_after_restart': 401, 'saved_card_after_restart': True,
                  'cleanup': True}))
