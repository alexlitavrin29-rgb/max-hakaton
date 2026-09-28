"""Check public TLS and editor authentication without logging credentials."""
import base64
import json
from pathlib import Path
from urllib.request import Request, urlopen
from urllib.error import HTTPError

app = 'https://135-106-229-210.sslip.io'
admin = 'https://admin.135-106-229-210.sslip.io'
assert urlopen(app, timeout=20).status == 200
assert json.load(urlopen(app + '/api/health'))['status'] == 'ok'
try:
    urlopen(admin + '/api/state')
    raise AssertionError('Editor exposed')
except HTTPError as error:
    assert error.code == 401
password = Path('/opt/tochka/admin-access.txt').read_text().split('Password: ', 1)[1].strip()
authorization = 'Basic ' + base64.b64encode(('owner:' + password).encode()).decode()
request = Request(admin + '/api/state', headers={'Authorization': authorization})
data = json.load(urlopen(request))
print(json.dumps({'public_https': 200, 'editor_without_password': 401,
                  'editor_with_password': 200, 'revision': data.get('revision'),
                  'published_id': data.get('published_id')}))
