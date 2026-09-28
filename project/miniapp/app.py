"""Public mini-app API. Editor APIs and chat sessions are never exposed here."""
import asyncio
from copy import deepcopy
import json
import logging
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
import os
from pathlib import Path
import secrets
import time
from types import SimpleNamespace
from typing import Literal
from weakref import WeakValueDictionary

import asyncpg
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from project.llm.services.scenario import ScenarioStore
from project.llm.bot import close_network_clients
from .auth import validate_launch
from .service import ENTRIES, MiniDialogue, present
from .favorites import Favorites, card_key
from .session_store import SessionStore, pack, unpack, token_hash
from project.llm.services.saved_answers import material

STATIC = Path(__file__).parent / 'static'
QUEUE_TIMEOUT = 5.0
logger = logging.getLogger(__name__)
OWNER_LOCKS = WeakValueDictionary()


@dataclass
class Session:
    engine: MiniDialogue
    owner: str = 'preview'
    seen_cards: dict = field(default_factory=dict)
    section: str = ''
    offered: set = field(default_factory=set)
    touched: float = field(default_factory=time.monotonic)
    last_request: float = 0
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)


@asynccontextmanager
async def lifespan(app):
    logging.basicConfig(level=logging.WARNING, format='%(asctime)s %(levelname)s %(message)s')
    app.state.preview = os.getenv('MINIAPP_PREVIEW') == '1'
    app.state.origin = os.getenv('MINIAPP_ORIGIN', '').rstrip('/')
    if not app.state.preview and not app.state.origin.startswith('https://'):
        raise RuntimeError('Set MINIAPP_ORIGIN to the public HTTPS origin')
    app.state.token = os.environ['MAX_BOT_TOKEN']
    app.state.bot_name = os.getenv('MINIAPP_BOT_NAME', 't143_hakaton_max_bot')
    pool = await asyncpg.create_pool(host=os.getenv('POSTGRES_HOST', 'postgres'),
        port=int(os.getenv('POSTGRES_PORT', '5432')), user=os.getenv('POSTGRES_USER', 'support_router'),
        password=os.environ['POSTGRES_PASSWORD'], database=os.getenv('POSTGRES_DB', 'support_router'),
        min_size=1, max_size=3)
    app.state.store = ScenarioStore(pool)
    app.state.published_cache = None
    app.state.favorites = Favorites(pool)
    await app.state.favorites.initialize()
    app.state.shared = SessionStore(pool)
    await app.state.shared.initialize()
    app.state.sessions = {}
    app.state.launches = {}
    app.state.capacity_lock = asyncio.Lock()
    app.state.search_slots = asyncio.Semaphore(8)
    app.state.active_searches=0;app.state.max_active_searches=0;app.state.action_metrics=[]
    yield
    await close_network_clients()
    await pool.close()


app = FastAPI(title='Точка опоры', lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)
from .help_api import router as help_router
app.include_router(help_router)


@app.get('/api/health')
async def health():
    await app.state.store.pool.fetchval('SELECT 1')
    return {'status': 'ok'}


@app.get('/api/internal/metrics', include_in_schema=False)
async def internal_metrics(request: Request):
    if request.client.host not in {'127.0.0.1', '::1'}:
        raise HTTPException(404)
    metrics = getattr(app.state, 'action_metrics', [])
    waits = sorted(item.get('slot_wait_ms', 0) for item in metrics)
    percentile = lambda fraction: waits[min(len(waits)-1, int((len(waits)-1)*fraction))] if waits else 0
    semaphore = getattr(app.state, 'search_slots', None)
    waiters = getattr(semaphore, '_waiters', None)
    return {'active': getattr(app.state, 'active_searches', 0),
            'waiting': len(waiters) if waiters else 0,
            'max_active': getattr(app.state, 'max_active_searches', 0),
            'sample_count': len(metrics), 'wait_p50_ms': percentile(.5),
            'wait_p95_ms': percentile(.95), 'wait_p99_ms': percentile(.99)}


@app.middleware('http')
async def boundary(request: Request, call_next):
    if getattr(app.state, 'preview', False):
        if request.url.hostname not in {'localhost', '127.0.0.1', 'testserver'}:
            return JSONResponse({'detail': 'Предпросмотр доступен только локально.'}, 403)
    if request.method in {'POST', 'DELETE'}:
        origin = request.headers.get('origin')
        expected = str(request.base_url).rstrip('/') if app.state.preview else app.state.origin
        if origin and origin != expected:
            return JSONResponse({'detail': 'Недопустимый источник запроса.'}, 403)
        body = bytearray()
        async for chunk in request.stream():
            body.extend(chunk)
            if len(body) > 24000:
                return JSONResponse({'detail': 'Слишком большой запрос.'}, 413)
        request._body = bytes(body)
    response = await call_next(request)
    response.headers['Cache-Control'] = 'no-store'
    response.headers['X-Content-Type-Options'] = 'nosniff'
    response.headers['Referrer-Policy'] = 'no-referrer'
    response.headers['Content-Security-Policy'] = "default-src 'self'; script-src 'self' https://st.max.ru; style-src 'self'; img-src 'self' data:; connect-src 'self'; base-uri 'none'; object-src 'none'; form-action 'self'"
    return response


@app.get('/')
async def index():
    return FileResponse(STATIC / 'index.html')


@app.get('/api/info')
async def info():
    return dict(preview=app.state.preview, bot_name=app.state.bot_name)


class Launch(BaseModel):
    init_data: str = Field(default='', max_length=16000)


@app.post('/api/session')
async def launch(body: Launch):
    identity = 'preview'
    if not app.state.preview:
        try:
            identity = validate_launch(body.init_data, app.state.token)
        except (ValueError, KeyError, TypeError):
            raise HTTPException(401, 'Откройте приложение заново из MAX.') from None
    data = await current_config()
    if getattr(app.state, 'shared', None):
        state = Session(MiniDialogue(data['config'], data['revision']), owner=str(identity))
        return dict(session=await app.state.shared.create(str(identity), state))
    now = time.monotonic()
    async with app.state.capacity_lock:
        app.state.sessions = {k: s for k, s in app.state.sessions.items() if now - s.touched < 3600 or s.lock.locked()}
        app.state.launches = {k: v for k, v in app.state.launches.items() if now - v < 5}
        if identity in app.state.launches:
            raise HTTPException(429, 'Подождите несколько секунд и повторите.')
        if len(app.state.sessions) >= 200:
            raise HTTPException(503, 'Сейчас много запросов. Попробуйте немного позже.')
        app.state.launches[identity] = now
        key = secrets.token_urlsafe(32)
        app.state.sessions[key] = Session(MiniDialogue(data['config'], data['revision']), owner=str(identity))
    return dict(session=key)


def authenticated_session(request):
    key = request.headers.get('authorization', '').removeprefix('Bearer ')
    state = app.state.sessions.get(key)
    if not state or time.monotonic() - state.touched > 3600:
        raise HTTPException(401, 'Сеанс завершён. Откройте приложение заново.')
    state.touched = time.monotonic()
    return state


async def current_config():
    store = app.state.store
    if not hasattr(store, 'pool'):
        return await store.read(published=True)
    revision = await store.pool.fetchval('SELECT published_id FROM scenario_state WHERE id=1')
    cached = getattr(app.state, 'published_cache', None)
    if cached and cached['revision'] == revision:
        return cached
    data = await store.read(published=True)
    app.state.published_cache = data
    return data


async def shared_owner(request):
    bearer = request.headers.get('authorization', '').removeprefix('Bearer ')
    return SimpleNamespace(owner=await app.state.shared.owner(bearer))


@asynccontextmanager
async def owner_transaction(owner):
    async with app.state.store.pool.acquire() as connection:
      async with connection.transaction():
        await connection.execute('SELECT pg_advisory_xact_lock(hashtextextended($1,0))', owner)
        yield connection


async def checked_shared_session(request, connection, *, require_idle=False):
    bearer = request.headers.get('authorization', '').removeprefix('Bearer ')
    row = await connection.fetchrow('''SELECT owner,state,claim,claim_until FROM miniapp_sessions
        WHERE bearer_hash=$1 AND expires_at>now() FOR UPDATE''', token_hash(bearer))
    if not row:
        raise HTTPException(401, 'Сеанс завершён. Откройте приложение заново.')
    if require_idle and row['claim'] and row['claim_until'] and row['claim_until'].timestamp() > time.time():
        raise HTTPException(429, 'Дождитесь ответа и повторите.')
    document = json.loads(row['state'])
    return SimpleNamespace(owner=row['owner'], seen_cards=document['seen_cards'], document=document)


def owner_lock(owner):
    return OWNER_LOCKS.setdefault(owner, asyncio.Lock())


class FavoriteAction(BaseModel):
    card_id: str = Field(pattern=r'^[a-f0-9]{64}$')
    saved: bool


@app.get('/api/favorites')
async def favorites(request: Request):
    state = await shared_owner(request) if getattr(app.state, 'shared', None) else authenticated_session(request)
    return dict(cards=await saved_cards(state))


async def saved_cards(state):
    cards = await app.state.favorites.list(state.owner)
    if any(c.get('section') == 'advice' for c in cards):
        data = await current_config()
        for index, old in enumerate(cards):
            if old.get('section') != 'advice':
                continue
            current = material(data['config'], old['node_id'])
            if current:
                cards[index] = dict(current, saved_at=old['saved_at'],
                                    updated=current['content_hash'] != old.get('content_hash'))
            else:
                cards[index] = dict(old, title='Материал больше недоступен', text='Этот ответ снят с публикации.',
                                    actions=[], unavailable=True)
    return cards


class MaterialAction(BaseModel):
    node_id: str = Field(pattern=r'^[A-Za-z][A-Za-z0-9_-]{0,79}$')
    save: bool = False


@app.post('/api/material')
async def open_material(body: MaterialAction, request: Request):
    if getattr(app.state, 'shared', None):
        state = await shared_owner(request)
        data = await current_config()
        card = material(data['config'], body.node_id)
        if not card:
            raise HTTPException(404, 'Этот материал больше недоступен. Другие сохранённые ответы остаются в «Моём».')
        async with owner_transaction(state.owner) as connection:
            state = await checked_shared_session(request, connection, require_idle=True)
            state.seen_cards[card['favorite_id']] = card.copy()
            while len(state.seen_cards) > 200:
                del state.seen_cards[next(iter(state.seen_cards))]
            if body.save:
                await app.state.favorites.save(state.owner, card['favorite_id'], card, connection)
            await connection.execute('''UPDATE miniapp_sessions SET state=$2::jsonb
                WHERE bearer_hash=$1''', token_hash(request.headers.get('authorization', '').removeprefix('Bearer ')),
                json.dumps(state.document, ensure_ascii=False))
        return dict(card=card, saved=body.save)
    state = authenticated_session(request)
    data = await current_config()
    card = material(data['config'], body.node_id)
    if not card:
        raise HTTPException(404, 'Этот материал больше недоступен. Другие сохранённые ответы остаются в «Моём».')
    state.seen_cards[card['favorite_id']] = card.copy()
    if body.save:
        async with owner_lock(state.owner):
            authenticated_session(request)
            await app.state.favorites.save(state.owner, card['favorite_id'], card)
    return dict(card=card, saved=body.save)


@app.post('/api/favorites')
async def favorite(body: FavoriteAction, request: Request):
    if getattr(app.state, 'shared', None):
        state = await shared_owner(request)
        async with owner_transaction(state.owner) as connection:
            state = await checked_shared_session(request, connection)
            if body.saved:
                card = state.seen_cards.get(body.card_id)
                if card is None:
                    raise HTTPException(409, 'Открой эту карточку в поиске заново, чтобы сохранить.')
                if card.get('section') == 'advice':
                    data = await current_config()
                    card = material(data['config'], card['node_id'])
                    if card is None:
                        raise HTTPException(404, 'Этот материал больше недоступен.')
                await app.state.favorites.save(state.owner, body.card_id, card, connection)
            else:
                await app.state.favorites.remove(state.owner, body.card_id, connection)
        return dict(cards=await saved_cards(state))
    state = authenticated_session(request)
    async with owner_lock(state.owner):
        authenticated_session(request)
        if body.saved:
            card = state.seen_cards.get(body.card_id)
            if card is None:
                raise HTTPException(409, 'Открой эту карточку в поиске заново, чтобы сохранить.')
            if card.get('section') == 'advice':
                data = await current_config()
                card = material(data['config'], card['node_id'])
                if card is None:
                    raise HTTPException(404, 'Этот материал больше недоступен.')
            await app.state.favorites.save(state.owner, body.card_id, card)
        else:
            await app.state.favorites.remove(state.owner, body.card_id)
        return dict(cards=await saved_cards(state))


@app.delete('/api/account')
async def delete_account(request: Request):
    if getattr(app.state, 'shared', None):
        state = await shared_owner(request)
        async with owner_transaction(state.owner) as connection:
            state = await checked_shared_session(request, connection)
            await app.state.favorites.delete_account(state.owner, connection)
            await app.state.shared.delete_owner(state.owner, connection)
            await connection.execute('DELETE FROM miniapp_launches WHERE owner=$1', state.owner)
            if state.owner.isdigit():
                await connection.execute('DELETE FROM bot_sessions WHERE user_id=$1', int(state.owner))
                await connection.execute('DELETE FROM bot_processed_events WHERE user_id=$1', int(state.owner))
            await connection.execute('''INSERT INTO account_epochs(owner,epoch) VALUES($1,1)
                ON CONFLICT(owner) DO UPDATE SET epoch=account_epochs.epoch+1,
                changed_at=now()''', state.owner)
        return {'deleted': True}
    state = authenticated_session(request)
    async with owner_lock(state.owner):
        authenticated_session(request)
        await app.state.favorites.delete_account(state.owner)
        for key, session in list(app.state.sessions.items()):
            if session.owner == state.owner:
                app.state.sessions.pop(key, None)
    return {'deleted': True}


class Action(BaseModel):
    section: Literal['home', 'work', 'rental', 'help'] | None = None
    text: str = Field(default='', max_length=2500)
    payload: str | None = Field(default=None, max_length=200)


@app.post('/api/action')
async def action(body: Action, request: Request):
    total_started=time.perf_counter();metric=dict(channel='miniapp',safe_error_code=None,total_timeout_ms=120000)
    if sum([body.section is not None, bool(body.text.strip()), body.payload is not None]) != 1:
        raise HTTPException(400, 'Выберите одно действие.')
    key = request.headers.get('authorization', '').removeprefix('Bearer ')
    shared = getattr(app.state, 'shared', None)
    claim = None
    canonical = json.dumps(body.model_dump(), sort_keys=True)
    idempotency_key = request.headers.get('idempotency-key') or secrets.token_hex(16)
    if len(idempotency_key) > 200:
        raise HTTPException(400, 'Неверный ключ повтора запроса.')
    key_bytes, body_bytes = idempotency_key.encode(), canonical.encode()
    if shared:
        row, claim, cached = await shared.claim(key, key_bytes, body_bytes)
        if cached is not None:
            return cached
        try:
            data = await current_config()
            state = unpack(json.loads(row['state']), data['config'], data['revision'], Session, MiniDialogue, row['owner'])
        except BaseException:
            await shared.release(key, claim)
            raise
    else:
        state = app.state.sessions.get(key)
    now = time.monotonic()
    if not state or (not shared and now - state.touched > 3600):
        raise HTTPException(401, 'Сеанс завершён. Откройте приложение заново.')
    if not shared and (state.lock.locked() or now - state.last_request < .4):
        raise HTTPException(429, 'Дождитесь ответа и повторите.')
    async with state.lock:
        state.last_request = state.touched = now
        if body.section == 'home' or body.payload == 'mini:home':
            state.section = ''; state.offered = set()
            result = dict(section='home', cards=[], notices=[], controls=[], summary='')
            if shared:
                await shared.finish(key, claim, key_bytes, body_bytes, state, result)
            return result
        if body.payload and body.payload not in state.offered:
            if shared:
                await shared.release(key, claim)
            raise HTTPException(409, 'Эта кнопка уже неактивна. Используйте текущие кнопки.')
        if not body.section and not state.section:
            if shared:
                await shared.release(key, claim)
            raise HTTPException(400, 'Выберите раздел.')
        previous = (deepcopy(state.engine.sessions), state.section,
                    state.offered.copy(), deepcopy(state.seen_cards))
        def restore():
            state.engine.sessions, state.section, state.offered, state.seen_cards = previous
        async def execute():
            engine = state.engine
            if body.section:
                state.section = body.section
                engine.sessions.clear()
                session = engine.session(1)
                session.values = {'role': 'user'}
                replies = await engine.enter(1, session, ENTRIES[body.section])
            else:
                replies = await engine.handle(1, text=body.text, payload=body.payload)
            actual_branch = engine.session(1).branch
            state.section = next((section for section, entry in ENTRIES.items() if entry == actual_branch), state.section)
            view, state.offered = present(engine, replies, state.section)
            for card in view['cards']:
                card['section'] = state.section
                card['favorite_id'] = card_key(state.section, card)
                state.seen_cards[card['favorite_id']] = card.copy()
            while len(state.seen_cards) > 200:
                del state.seen_cards[next(iter(state.seen_cards))]
            return view
        try:
            slot_started=time.perf_counter()
            metric['slot_limit_reached']=app.state.search_slots.locked()
            try:
                await asyncio.wait_for(app.state.search_slots.acquire(), timeout=QUEUE_TIMEOUT)
            except TimeoutError:
                metric['safe_error_code']='queue_timeout'
                metric['slot_wait_ms']=(time.perf_counter()-slot_started)*1000
                raise HTTPException(503, 'Сейчас много запросов. Новый запрос ещё не обработан. Повторите через несколько секунд.') from None
            try:
                metric['slot_wait_ms']=(time.perf_counter()-slot_started)*1000
                app.state.active_searches=getattr(app.state,'active_searches',0)+1
                app.state.max_active_searches=max(getattr(app.state,'max_active_searches',0),app.state.active_searches)
                metric['concurrent_requests']=app.state.active_searches
                active_started=time.perf_counter()
                try:
                    result = await asyncio.wait_for(execute(),timeout=120)
                    if shared:
                        await shared.finish(key, claim, key_bytes, body_bytes, state, result)
                    return result
                finally:
                    metric['slot_active_ms']=(time.perf_counter()-active_started)*1000
                    app.state.active_searches-=1
            finally:
                app.state.search_slots.release()
        except HTTPException:
            restore()
            if shared:
                await shared.release(key, claim)
            raise
        except asyncio.CancelledError:
            restore()
            if shared:
                await shared.release(key, claim)
            raise
        except TimeoutError:
            restore()
            if shared:
                await shared.release(key, claim)
            metric['safe_error_code']='timeout'
            raise HTTPException(503, 'Не получилось завершить запрос. Откройте раздел заново и повторите поиск.') from None
        except Exception as error:
            # Never log user queries, provider error bodies, or secrets.
            restore()
            if shared:
                await shared.release(key, claim)
            metric['safe_error_code']='action_failed'
            logger.error('miniapp_action_failed section=%s type=%s', state.section, type(error).__name__)
            raise HTTPException(503, 'Не получилось завершить запрос. Откройте раздел заново и повторите поиск.') from None
        finally:
            metric.setdefault('slot_wait_ms',0.0);metric.setdefault('slot_active_ms',0.0)
            metric['request_total_ms']=(time.perf_counter()-total_started)*1000
            metrics=getattr(app.state,'action_metrics',None)
            if metrics is not None:
                metrics.append(metric)
                if len(metrics)>500:del metrics[:-500]


app.mount('/static', StaticFiles(directory=STATIC), name='static')


