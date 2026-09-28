"""Post-deploy check with synthetic sessions; no messages sent to MAX."""
import asyncio
import hashlib
import hmac
import json
import os
import sys
import time
from urllib.parse import urlencode
from urllib.request import Request, urlopen


def app_check():
    def call(path, body, token=''):
        headers = {'Content-Type': 'application/json'}
        if token: headers['Authorization'] = 'Bearer ' + token
        request = Request('http://127.0.0.1:8766' + path,
                          data=json.dumps(body).encode(), headers=headers)
        with urlopen(request, timeout=50) as response: return json.load(response)

    values = dict(auth_date=str(int(time.time())), user=json.dumps({'id': 9999999981100}))
    secret = hmac.new(b'WebAppData', os.environ['MAX_BOT_TOKEN'].encode(), hashlib.sha256).digest()
    check = '\n'.join(f'{k}={values[k]}' for k in sorted(values))
    values['hash'] = hmac.new(secret, check.encode(), hashlib.sha256).hexdigest()
    token = call('/api/session', {'init_data': urlencode(values)})['session']
    def action(body):
        time.sleep(.5)
        return call('/api/action', body, token)
    assert action({'section': 'rental'})['section'] == 'rental'
    started = time.monotonic()
    view = action({'text': 'Москва, до 70000 рублей в месяц'})
    assert view['cards'], 'First search returned no cards'
    print(json.dumps(dict(channel='miniapp', stage='search', cards=len(view['cards']),
                          seconds=round(time.monotonic()-started, 2)), ensure_ascii=False), flush=True)
    more = next((c['payload'] for c in view['controls'] if c.get('more')), None)
    if more:
        view = action({'payload': more})
        assert view['section'] == 'rental' and view['cards'], 'Next portion returned no cards'
        print(json.dumps(dict(channel='miniapp', stage='more', cards=len(view['cards']))), flush=True)
    view = action({'text': 'до 65000 рублей в месяц'})
    assert 'Москва' in view['summary'] and '65000' in view['summary']
    assert view['cards'], 'Changed budget returned no cards'
    home = next(c['payload'] for c in view['controls'] if c['label'] == 'К разделам')
    assert action({'payload': home})['section'] == 'home'
    print(json.dumps(dict(channel='miniapp', edit_and_home='ok')), flush=True)


async def bot_check():
    import asyncpg
    from project.llm.services.scenario import ScenarioStore
    from project.llm.services.flow import FlowDialogue, PreviewReminders
    from project.llm.bot import close_network_clients
    pool = await asyncpg.create_pool(host=os.getenv('POSTGRES_HOST', 'postgres'),
        port=int(os.getenv('POSTGRES_PORT', '5432')), user=os.getenv('POSTGRES_USER', 'support_router'),
        password=os.environ['POSTGRES_PASSWORD'], database=os.getenv('POSTGRES_DB', 'support_router'),
        min_size=1, max_size=1)
    try:
        data = await ScenarioStore(pool).read(published=True)
        engine = FlowDialogue(PreviewReminders(), data['config'], data['revision'])
        session = engine.session(1); session.values['role'] = 'child'
        await engine.enter(1, session, 'rental')
        await engine.handle(1, text='Москва, до 70000 рублей в месяц')
        assert session.result_cards, 'Bot engine returned no cards'
        print(json.dumps(dict(channel='bot_engine', revision=data['revision'],
                              cards=len(session.result_cards))), flush=True)
    finally:
        await close_network_clients()
        await pool.close()


if __name__ == '__main__':
    if '--bot' in sys.argv: asyncio.run(bot_check())
    else: app_check()
