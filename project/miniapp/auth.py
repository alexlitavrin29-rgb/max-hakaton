"""Validate MAX launch data on the server; never send the bot token to a browser."""
import hashlib
import hmac
import json
import time
from urllib.parse import parse_qsl


def validate_launch(raw, token, now=None):
    if not token or not raw or len(raw) > 16000:
        raise ValueError('invalid launch')
    pairs = parse_qsl(raw, keep_blank_values=True, strict_parsing=True)
    values = dict(pairs)
    if len(values) != len(pairs):
        raise ValueError('duplicate launch fields')
    signature = values.pop('hash', '')
    secret = hmac.new(b'WebAppData', token.encode(), hashlib.sha256).digest()
    payload = '\n'.join(f'{key}={values[key]}' for key in sorted(values))
    expected = hmac.new(secret, payload.encode(), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(expected, signature):
        raise ValueError('invalid signature')
    stamp = int(values['auth_date'])
    if not -30 <= (time.time() if now is None else now) - stamp <= 3600:
        raise ValueError('expired launch')
    user = json.loads(values['user'])
    if not isinstance(user, dict) or type(user.get('id')) is not int or user['id'] <= 0:
        raise ValueError('invalid user')
    return user['id']
