"""Replay disclosed repair prefixes through the real dialogue with offline inputs."""

import asyncio
from copy import deepcopy
import json
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

from project.llm.services import flow, rental
from project.llm.integrations import headhunter


ROOT = Path(__file__).resolve().parents[3]
CONTROL = ROOT / 'docs/quality-acceptance'
sys.path.insert(0, str(CONTROL))
from capture_control import Capture  # noqa: E402

PACKET = json.loads((ROOT / 'docs/test-results/quality-independent-2026-09-26/independent-review-private/repair-packet-private.json').read_text(encoding='utf-8'))
SCENARIO = json.loads((ROOT / 'submission/scenario.json').read_text(encoding='utf-8'))['config']
GEOGRAPHY = json.loads((CONTROL / 'sealed-2026-09-26/geography.json').read_text(encoding='utf-8'))
CURRENT = json.loads((Path(__file__).with_name('replay_publication9_v1.json')).read_text(encoding='utf-8'))


@pytest.mark.parametrize('case', PACKET['examples'], ids=lambda case: case['id'])
def test_disclosed_case_full_prefix_with_saved_model_and_native_sources(case):
    """Replay the current age-optional sequence; preserve the sealed old oracle."""
    original = dict(id=case['id'], branch='housing' if case['id'].startswith('H') else 'work',
                    role=case['role'], turns=case['sealed_turns'])
    turns = deepcopy(original['turns'][:case['through_turn']])
    expected = CURRENT['cases'].get(case['id'])
    if expected:
        region = expected['region']
        wire = dict(method='GET', url=f'https://opendata.trudvsem.ru/api/v1/vacancies/region/{region}',
                    query=dict(limit=100, offset=0))
        turns[0]['oracle']['calls'] = [dict(wire=wire, response=dict(kind='source_error')) for _ in range(2)]
    assert [turn['user'] for turn in turns] == [turn['user'] for turn in case['actual_turns']]
    captured = []
    capture = Capture(SCENARIO, 9, GEOGRAPHY, None, captured.append)
    position = 0

    async def saved_model(messages, **kwargs):
        nonlocal position
        raw = case['actual_turns'][position]['llm'][0]['raw']
        position += 1
        return raw

    async def run():
        # This historical packet contains only Trudvsem responses, never HH data.
        with capture.bindings(), patch.object(headhunter, 'enabled', return_value=False), patch.object(flow, 'call_llm', saved_model), patch.object(rental, 'call_llm', saved_model):
            await capture.dialogue({**original, 'turns': turns})

    asyncio.run(run())
    assert position == len(turns)
    assert len(captured) == len(turns)
    assert all('execution_error' not in turn for turn in captured)
    assert all(turn['after']['values'].get('role') == 'child' for turn in captured)
    assert all(not turn['wire_errors'] for turn in captured), [turn['wire_errors'] for turn in captured]
    if expected:
        assert [len(turn['source_calls']) for turn in captured] == expected['calls_by_turn']
        assert all(call['url'].endswith('/' + expected['region']) and call['params']['offset'] == '0'
                   for turn in captured for call in turn['source_calls'])
        assert len(captured[0]['source_calls']) == 2
        assert all(response['kind'] == 'source_error' for response in captured[0]['source_responses'])
        assert not captured[0]['assessed_cards']  # Failed source must not produce invented cards.
        assert captured[0]['after']['work']['fields']['city']['status'] == 'known'
        assert captured[0]['after']['work']['fields']['city']['value'] == expected['city']
        assert all(turn['after']['node'] == 'work_search' for turn in captured)
        assert all('сколько тебе лет' not in '\n'.join(reply.get('text', '').lower() for reply in turn['replies'])
                   for turn in captured)
    assert all(not any(event['kind'] == 'llm_error' for event in turn['after']['trace']) for turn in captured)
    last = captured[-1]
    assessed = {item['card'].get('id') or item['card'].get('ad_id'): item['result']
                for item in last['assessed_cards']}
    if expected:
        assert set(assessed) == set(expected['reject'] + expected['consider'])
        assert all(assessed[identifier][0] for identifier in expected['reject'])
        assert all(not assessed[identifier][0] for identifier in expected['consider'])
        assert all(last['after']['work']['fields'][name]['value'] == value
                   for name, value in expected['last_fields'].items())
        assert last['after']['work']['fields']['city']['value'] == expected['city']
        assert 'age' not in last['after']['work']['pending_fields']
    if case['id'] == 'W0001':
        assert assessed['W0001-T02-C3'][0] and assessed['W0001-T02-C4'][0]
        assert len(last['source_calls']) == 1
    elif case['id'] == 'H0010':
        assert captured[1]['after']['rental']['city'] is None
        assert captured[1]['after']['rental']['awaiting'] == 'city'
        assert assessed['H0010-T03-C3'][0] and assessed['H0010-T03-C4'][0]
    elif case['id'] == 'H0037':
        assert all(not turn['source_calls'] for turn in captured[:-1])
        assert last['after']['rental']['other_accepted'] is True
        assert 'холодильник' in last['after']['rental']['other']
        assert 'холодильник' in '\n'.join(reply.get('text', '') for reply in last['replies'])
    elif case['id'] == 'W0034':
        assert captured[1]['after']['work']['fields']['schedule']['status'] == 'known'
        assert 'удал' in last['after']['work']['fields']['schedule']['value']
        assert last['after']['work']['fields']['employment']['value'] == 'temporary'
    else:
        assert 'query' not in last['after']['work']['pending_fields']
        assert last['after']['work']['fields']['schedule']['status'] == 'known'
