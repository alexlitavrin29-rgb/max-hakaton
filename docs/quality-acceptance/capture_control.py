"""Frozen FlowDialogue capture with real LLM and native HTTP fixtures.

This is NOT a semantic oracle. Every turn must subsequently receive independent
semantic review via execution_guard.validate_semantic_review before evaluation.
No command-line live execution is exposed until binding/preflight is approved.
"""
import copy
import asyncio
import dataclasses
import json
import time
from datetime import datetime, timezone
from contextlib import ExitStack
from contextvars import ContextVar
from types import SimpleNamespace
from unittest.mock import patch

from execution_guard import BudgetStopped

ACTIVE = ContextVar('independent_control_turn')


def fixture_locations(dictionary, query):
    """Return controlled dictionary rows by declared literal names/aliases only.

    A short canonical homonym returns every matching row. No production resolver
    or expected per-turn state decides which region the source returns.
    """
    def key(value):
        return ' '.join(value.casefold().replace('ё', 'е').split())
    result = []
    for place in dictionary['places']:
        native = place['native_housing']
        aliases = [native['name'], place['name'], place.get('prepositional', '')] + place.get('location_queries', [])
        if key(query) in {key(value) for value in aliases if value}:
            result.append(plain(native))
    return result


def plain(value):
    if dataclasses.is_dataclass(value):
        return plain(dataclasses.asdict(value))
    if isinstance(value, dict):
        return {str(k): plain(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [plain(v) for v in value]
    if isinstance(value, set):
        return sorted(plain(v) for v in value)
    return copy.deepcopy(value)


class Capture:
    def __init__(self, scenario, revision, geography, budget, sink, settings=None):
        self.scenario, self.revision = scenario, revision
        self.geography, self.budget, self.sink = geography, budget, sink
        self.settings = settings

    def bindings(self):
        """Only external data/transport and observation hooks are replaced."""
        import httpx
        from project.llm.integrations import reefapi, trudvsem
        from project.llm.services import flow, free_work, geography, rental, work
        stack = ExitStack()
        original_client = httpx.AsyncClient

        async def http_fixture(request):
            context = ACTIVE.get()
            if request.url.host == 'api.reefapi.com' and request.url.path.endswith('/locations'):
                data = json.loads(request.content)
                query = data.get('query', '')
                rows = fixture_locations(self.geography, query)
                context['geography_calls'].append(dict(method=request.method, url=str(request.url), json=data, response=plain(rows)))
                return httpx.Response(200, json=dict(ok=True, data=dict(locations=rows)))
            call = dict(method=request.method, url=str(request.url).split('?')[0])
            if request.method == 'GET':
                call['params'] = dict(request.url.params)
            else:
                call['json'] = json.loads(request.content)
            ordinal = len(context['source_calls'])
            context['source_calls'].append(call)
            expected = context['oracle']['calls']
            if ordinal >= len(expected):
                context['wire_errors'].append(dict(kind='extra_call', actual=call))
                return httpx.Response(503, json={})
            oracle = expected[ordinal]
            wire = plain(oracle['wire'])
            if 'query' in wire:
                if 'params' in wire:
                    raise ValueError('Ambiguous fixture query representation')
                wire['params'] = wire.pop('query')
            if 'params' in wire:
                wire['params'] = {k: str(v).lower() if isinstance(v, bool) else str(v) for k, v in wire['params'].items()}
            if wire != call:
                context['wire_errors'].append(dict(kind='request_mismatch', expected=wire, actual=call))
            response = oracle['response']
            context['source_responses'].append(plain(response))
            if response['kind'] == 'source_timeout':
                raise httpx.ReadTimeout('Synthetic fixture timeout', request=request)
            if response['kind'] == 'source_error':
                return httpx.Response(503, json={})
            return httpx.Response(200, json=response['native'])

        proxy = SimpleNamespace(**{name: getattr(httpx, name) for name in
            ('TimeoutException', 'HTTPError', 'HTTPStatusError', 'RequestError')})
        proxy.AsyncClient = lambda **kwargs: original_client(**kwargs, transport=httpx.MockTransport(http_fixture))
        stack.enter_context(patch.object(trudvsem, 'httpx', proxy))
        stack.enter_context(patch.object(reefapi, 'httpx', proxy))
        # Reef's key check remains intact; use a dummy key for intercepted fixture HTTP.
        stack.enter_context(patch.dict('os.environ', {'HOUSING_API_KEY': 'fixture-only-never-network'}))
        places = [p['native_work'] for p in self.geography['places']]
        stack.enter_context(patch.object(geography, 'places', lambda: plain(places)))
        stack.enter_context(patch.object(geography, 'indexes', geography.build_indexes))

        from project.llm.services.llm import call_llm, LLMError
        async def capture_llm(messages, **kwargs):
            context = ACTIVE.get()
            record = dict(started_utc=datetime.now(timezone.utc).isoformat(), messages=plain(messages), options={k: plain(v) for k, v in kwargs.items() if k not in {'settings', 'metrics'}})
            started = time.monotonic()
            context['llm'].append(record)
            if len(context['llm']) > 1:
                raise BudgetStopped('Unexpected second LLM request in one fixed turn')
            if context['oracle'].get('provider_fixture'):
                record['injected_error'] = 'timeout'
                record['elapsed_seconds'] = time.monotonic() - started
                raise LLMError('timeout')
            key = f"{context['id']}:{context['turn']}:{len(context['llm'])}"
            record['budget'] = await self.budget.reserve(key, messages, kwargs.get('max_tokens'), kwargs.get('response_schema'))
            metrics = {}
            kwargs['metrics'] = metrics
            if self.settings is not None:
                kwargs['settings'] = self.settings
            try:
                raw = await call_llm(messages, **kwargs)
                record['raw'] = raw
                return raw
            except LLMError as error:
                record['error'] = error.code
                raise
            except asyncio.CancelledError:
                record['error'] = 'execution_cancelled_usage_may_be_unknown'
                raise
            finally:
                record['elapsed_seconds'] = time.monotonic() - started
                record['metrics'] = metrics
                await self.budget.settle(key, metrics.get('usage'))
        stack.enter_context(patch.object(flow, 'call_llm', capture_llm))
        stack.enter_context(patch.object(rental, 'call_llm', capture_llm))

        original_normalize = trudvsem._normalize
        def capture_normalize(row, **kwargs):
            item = dict(raw=plain(row))
            ACTIVE.get()['normalized_cards'].append(item)
            try:
                result = original_normalize(row, **kwargs)
                item['normalized'] = plain(result)
                return result
            except Exception as error:
                item['error'] = type(error).__name__
                raise
        stack.enter_context(patch.object(trudvsem, '_normalize', capture_normalize))

        original_apply = free_work.FreeWorkBranch.apply
        def capture_apply(branch, session, raw, text):
            result = original_apply(branch, session, raw, text)
            ACTIVE.get()['checked_updates'].append(dict(kind='work_apply', raw=plain(raw), returned=plain(result), state=plain(session.work)))
            return result
        stack.enter_context(patch.object(free_work.FreeWorkBranch, 'apply', capture_apply))
        original_job = free_work.FreeWorkBranch.assess_job
        def capture_job(branch, session, job, values, place):
            result = original_job(branch, session, job, values, place)
            ACTIVE.get()['assessed_cards'].append(dict(branch='work', card=plain(job), conditions=plain(values), place=plain(place), result=plain(result)))
            return result
        stack.enter_context(patch.object(free_work.FreeWorkBranch, 'assess_job', capture_job))
        original_rental = rental.assess
        def capture_rental(row, place, budget):
            result = original_rental(row, place, budget)
            ACTIVE.get()['assessed_cards'].append(dict(branch='housing', card=plain(row), place=plain(place), budget=budget, result=plain(result)))
            return result
        stack.enter_context(patch.object(rental, 'assess', capture_rental))
        return stack

    async def dialogue(self, record):
        from project.llm.services.flow import FlowDialogue, PreviewReminders
        engine = FlowDialogue(PreviewReminders(), plain(self.scenario), self.revision)
        session = engine.session(1)
        session.values['role'] = 'candidate' if record['role'] == 'prospective_parent' else record['role']
        branch = 'rental' if record['branch'] == 'housing' else 'work'
        await engine.handle(1, payload='jump:' + branch)
        for turn in record['turns']:
            started = time.monotonic()
            context = dict(id=record['id'], turn=turn['index'], branch=record['branch'],
                started_utc=datetime.now(timezone.utc).isoformat(),
                oracle=turn['oracle'], llm=[], source_calls=[], source_responses=[], geography_calls=[], normalized_cards=[],
                wire_errors=[], checked_updates=[], assessed_cards=[], before=plain(session), user=turn['user'])
            token = ACTIVE.set(context)
            try:
                # User input is replayed exactly; event/oracle labels never select actions.
                context['replies'] = await engine.handle(1, text=turn['user'])
            except BudgetStopped:
                context['execution_error'] = 'budget_stopped'
                raise
            except asyncio.CancelledError:
                context['execution_error'] = 'execution_cancelled'
                raise
            except Exception as error:
                # No secret-containing exception repr/body is persisted.
                context['execution_error'] = type(error).__name__
                context['replies'] = []
            finally:
                context['elapsed_seconds'] = time.monotonic() - started
                context['after'] = plain(session)
                context['checked_updates'].append(dict(kind='validated_state_delta', before=context['before']['rental' if branch == 'rental' else 'work'], after=context['after']['rental' if branch == 'rental' else 'work']))
                if len(context['source_calls']) < len(turn['oracle']['calls']):
                    context['wire_errors'].append(dict(kind='missing_calls', expected=len(turn['oracle']['calls']), actual=len(context['source_calls'])))
                context.pop('oracle')
                self.sink(plain(context))
                ACTIVE.reset(token)
