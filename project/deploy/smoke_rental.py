"""Signed synthetic housing requests; no chat delivery, secrets never printed."""
import hashlib
import hmac
import json
import os
import time
from urllib.parse import urlencode
from urllib.request import Request, urlopen


def call(path, body, token=''):
    headers={'Content-Type':'application/json'}
    if token: headers['Authorization']='Bearer '+token
    request=Request('http://127.0.0.1:8766'+path,data=json.dumps(body).encode(),headers=headers)
    with urlopen(request,timeout=50) as response: return json.load(response)


for attempt in range(2):
    values=dict(auth_date=str(int(time.time())),user=json.dumps({'id':9999999981000+attempt}))
    secret=hmac.new(b'WebAppData',os.environ['MAX_BOT_TOKEN'].encode(),hashlib.sha256).digest()
    check='\n'.join(f'{k}={values[k]}' for k in sorted(values))
    values['hash']=hmac.new(secret,check.encode(),hashlib.sha256).hexdigest()
    token=call('/api/session',{'init_data':urlencode(values)})['session']
    call('/api/action',{'section':'rental'},token)
    time.sleep(.5)
    started=time.monotonic()
    view=call('/api/action',{'text':'Москва, до 70000 рублей в месяц'},token)
    assert view['section']=='rental'
    assert view['cards'], 'No housing cards returned'
    print(json.dumps(dict(attempt=attempt+1,seconds=round(time.monotonic()-started,2),
                         cards=len(view['cards']),summary=view['summary']),ensure_ascii=False),flush=True)
