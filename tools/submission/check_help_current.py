"""Check the current city-first help path without sending MAX messages."""
import hashlib
import hmac
import json
import os
import time
from urllib.parse import urlencode
from urllib.request import Request, urlopen


def call(path, body, bearer=''):
    headers = {'Content-Type': 'application/json'}
    if bearer:
        headers['Authorization'] = 'Bearer ' + bearer
    request = Request('http://127.0.0.1:8766' + path,
                      data=json.dumps(body).encode(), headers=headers)
    with urlopen(request, timeout=20) as response:
        return json.load(response)


values = {'auth_date': str(int(time.time())), 'user': json.dumps({'id': 9999999984201})}
secret = hmac.new(b'WebAppData', os.environ['MAX_BOT_TOKEN'].encode(), hashlib.sha256).digest()
payload = '\n'.join(f'{key}={values[key]}' for key in sorted(values))
values['hash'] = hmac.new(secret, payload.encode(), hashlib.sha256).hexdigest()
token = call('/api/session', {'init_data': urlencode(values)})['session']
call('/api/action', {'section': 'help'}, token)
time.sleep(.5)
view = call('/api/action', {'text': 'Казань'}, token)
if not view['cards']:
    button = next(item for item in view['controls'] if item['label'].startswith('Город Казань'))
    time.sleep(.5)
    view = call('/api/action', {'payload': button['payload']}, token)
if not view['cards']:
    button = next(item for item in view['controls'] if 'Все места' in item['label'])
    time.sleep(.5)
    view = call('/api/action', {'payload': button['payload']}, token)
assert view['cards']
assert all(card['section'] == 'help' for card in view['cards'])
print(json.dumps({'help_city_first': 'ok', 'cards': len(view['cards'])}))
