"""Exercise API and a signed synthetic mini-app session without sending chat messages."""
import hashlib
import hmac
import json
import os
import time
from urllib.parse import urlencode
from urllib.request import Request, urlopen
from urllib.error import HTTPError

base = 'http://127.0.0.1:8766'


def call(path, body=None, token=''):
    headers = {'Content-Type': 'application/json'}
    if token:
        headers['Authorization'] = 'Bearer ' + token
    req = Request(base + path, data=json.dumps(body).encode() if body is not None else None, headers=headers)
    with urlopen(req, timeout=40) as response:
        return json.load(response)


assert call('/api/health')['status'] == 'ok'
assert call('/api/info')['preview'] is False
try:
    call('/api/session', {'init_data': ''})
    raise AssertionError('Unauthenticated session was allowed')
except HTTPError as error:
    assert error.code == 401
values = dict(auth_date=str(int(time.time())), user=json.dumps({'id': 9999999990001}))
secret = hmac.new(b'WebAppData', os.environ['MAX_BOT_TOKEN'].encode(), hashlib.sha256).digest()
check = '\n'.join(f'{k}={values[k]}' for k in sorted(values))
values['hash'] = hmac.new(secret, check.encode(), hashlib.sha256).hexdigest()
session = call('/api/session', {'init_data': urlencode(values)})['session']
view = call('/api/action', {'section': 'help'}, session)
assert 'город' in json.dumps(view, ensure_ascii=False)
time.sleep(.5)
view = call('/api/action', {'text': 'Горно-Алтайск'}, session)
button = next(b for b in view['controls'] if 'Все места' in b['label'])
time.sleep(.5)
view = call('/api/action', {'payload': button['payload']}, session)
assert view['cards']
assert 'Горно-Алтайск' in json.dumps(view, ensure_ascii=False)
print(json.dumps({'health': 'ok', 'unsigned_session': 401, 'signed_help_flow': 'ok', 'cards': len(view['cards'])}))
