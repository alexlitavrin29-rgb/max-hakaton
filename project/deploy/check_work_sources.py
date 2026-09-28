"""Check the deployed common work engine and signed mini-app; send no MAX messages."""
import asyncio
import hashlib
import hmac
import json
import os
import sys
import time
from urllib.parse import urlencode
from urllib.request import Request, urlopen


def emit(**values):
    print(json.dumps(values, ensure_ascii=False), flush=True)


def sources(cards):
    return sorted({'hh' if 'Источник: HeadHunter' in c['text'] else 'trudvsem'
                   for c in cards if 'Источник:' in c['text']})


def app_check():
    def call(path, body, token=''):
        headers = {'Content-Type': 'application/json'}
        if token: headers['Authorization'] = 'Bearer ' + token
        request = Request('http://127.0.0.1:8766' + path,
                          data=json.dumps(body).encode(), headers=headers)
        with urlopen(request, timeout=50) as response: return json.load(response)

    values = dict(auth_date=str(int(time.time())), user=json.dumps({'id': 9999999981101}))
    secret = hmac.new(b'WebAppData', os.environ['MAX_BOT_TOKEN'].encode(), hashlib.sha256).digest()
    check = '\n'.join(f'{k}={values[k]}' for k in sorted(values))
    values['hash'] = hmac.new(secret, check.encode(), hashlib.sha256).hexdigest()
    token = call('/api/session', {'init_data': urlencode(values)})['session']
    def action(body):
        time.sleep(.5)
        return call('/api/action', body, token)
    action({'section': 'work'})
    started = time.monotonic()
    view = action({'text': 'Томск, мне 20, зарплата от 40000 рублей в месяц'})
    assert view['cards'], 'No work cards'
    found = set(sources(view['cards']))
    urls = {a['url'] for c in view['cards'] for a in c['actions'] if 'url' in a}
    emit(channel='miniapp', stage='search', cards=len(view['cards']), sources=sorted(found),
         seconds=round(time.monotonic()-started, 2))
    more = next(c['payload'] for c in view['controls'] if c.get('more'))
    view = action({'payload': more})
    assert view['cards'], 'No next portion'
    next_urls = {a['url'] for c in view['cards'] for a in c['actions'] if 'url' in a}
    assert not urls & next_urls, 'Repeated cards'
    found.update(sources(view['cards']))
    emit(channel='miniapp', stage='more', cards=len(view['cards']), sources=sources(view['cards']))
    assert found == {'hh', 'trudvsem'}, 'Both sources must appear across two portions'
    view = action({'text': 'зарплата от 45000 рублей в месяц'})
    assert 'Томск' in view['summary'] and '45 000' in view['summary'], 'Conditions lost'
    assert view['cards'], 'No cards after correction'
    emit(channel='miniapp', stage='salary_change', cards=len(view['cards']), sources=sources(view['cards']))
    # The app's fixed header button sends a section action.
    assert action({'section': 'home'})['section'] == 'home'
    emit(channel='miniapp', stage='home', ok=True)


async def bot_check():
    import asyncpg
    from project.llm.services.scenario import ScenarioStore
    from project.llm.services.flow import FlowDialogue, PreviewReminders
    from project.llm.services.work import field
    from project.llm.services.geography import resolve_legacy
    from project.llm.bot import close_network_clients
    pool = await asyncpg.create_pool(host=os.getenv('POSTGRES_HOST', 'postgres'),
        port=int(os.getenv('POSTGRES_PORT', '5432')), user=os.getenv('POSTGRES_USER', 'support_router'),
        password=os.environ['POSTGRES_PASSWORD'], database=os.getenv('POSTGRES_DB', 'support_router'),
        min_size=1, max_size=1)
    try:
        data = await ScenarioStore(pool).read(published=True)
        engine = FlowDialogue(PreviewReminders(), data['config'], data['revision'])
        session = engine.session(1); session.values['role'] = 'child'
        state = engine.work.state(session)
        state['fields']['age'] = field('known', 20, '20')
        engine.work.select_place(session, resolve_legacy('Томск')[0])
        replies = await engine.work.search(session)
        cards = [replies[c['index']] for c in session.result_cards]
        assert cards and sources(cards) == ['hh', 'trudvsem'], 'Both sources must appear'
        assert not state.get('failed_sources'), 'A source failed'
        emit(channel='bot_engine', revision=data['revision'], cards=len(cards), sources=sources(cards))
    finally:
        await close_network_clients()
        await pool.close()


if __name__ == '__main__':
    if '--bot' in sys.argv: asyncio.run(bot_check())
    else: app_check()
