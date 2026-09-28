"""Local regressions from the disclosed five-case repair packet; no provider calls."""
import json
from pathlib import Path
from types import SimpleNamespace

from project.llm.services.free_work import FreeWorkBranch
from project.llm.services import rental
from project.llm.services.rental import assess as assess_rental
from project.llm.services.work import assess as assess_work
from project.llm.services.work import field, validate_fields
from project.llm.services.vacancies import Vacancy
from project.llm.services.condition_coverage import (
    rental_extra, other_task_requested, update_wishes, consent_to_unchecked, TOTAL_COST,
)
from project.llm.integrations.trudvsem import _normalize
import re


PACKET = json.loads((Path(__file__).resolve().parents[3] /
    'docs/test-results/quality-independent-2026-09-26/independent-review-private/repair-packet-private.json').read_text(encoding='utf-8'))
CASES = {case['id']: case for case in PACKET['examples']}
NEIGHBORS = {case['id']: case for case in json.loads((Path(__file__).resolve().parents[3] /
    'docs/quality-acceptance/open-neighbors-after-control.json').read_text(encoding='utf-8'))['cases']}


def test_disclosed_conflicting_cards_are_excluded():
    work = CASES['W0001']['actual_turns'][1]['assessed_cards']
    place = work[0]['place']
    values = work[0]['conditions']
    for item in work:
        job = Vacancy(**{**item['card'], 'contacts': tuple()})
        reason, _ = assess_work(job, values, place)
        assert bool(reason) == (job.id in {'W0001-T02-C3', 'W0001-T02-C4'})

    rental = CASES['H0010']['actual_turns'][2]['assessed_cards']
    for item in rental:
        reason, unknown = assess_rental(item['card'], item['place'], item['budget'])
        number = item['card']['ad_id'][-1]
        if number in {'3', '4'}:
            assert reason
        elif number == '2':
            assert reason is None and unknown
        else:
            assert reason is None and not unknown


def test_disclosed_schedule_survives_model_omission_and_optional_query_stays_optional():
    class Owner:
        def record(self, *args, **kwargs): pass

    branch = FreeWorkBranch(Owner())
    state = SimpleNamespace(work={}, values={'role': 'child'}, offset=0, buffer=[], seen=set())
    first = CASES['W0034']['actual_turns'][1]
    branch.apply(state, json.loads(first['llm'][0]['raw'])['work_fields'], first['user'])
    assert state.work['fields']['schedule']['status'] == 'known'
    assert 'удал' in state.work['fields']['schedule']['value'].lower()

    state = SimpleNamespace(work={}, values={'role': 'child'}, offset=0, buffer=[], seen=set())
    first = CASES['W0082']['actual_turns'][0]
    branch.apply(state, first['checked_updates'][0]['raw'], first['user'])
    assert 'query' not in state.work['pending_fields']


def test_disclosed_rental_wish_and_text_consent(monkeypatch):
    class Owner:
        def record(self, *args, **kwargs): pass
        branches = {'rental': {}}

    branch = rental.RentalBranch(Owner())
    state = SimpleNamespace(rental={})
    branch.state(state)

    async def ready(*args): return []
    monkeypatch.setattr(branch, 'ready', ready)

    async def scenario():
        for turn in CASES['H0037']['actual_turns'][1:3]:
            if turn['turn'] == 3: state.rental['awaiting'] = 'other'
            async def saved(*args, **kwargs): return turn['llm'][0]['raw']
            monkeypatch.setattr(rental, 'call_llm', saved)
            await branch.input(None, state, turn['user'])
        assert 'холодильник' in state.rental['other']
        assert state.rental['other_accepted'] is True

    import asyncio
    asyncio.run(scenario())


def test_open_neighbor_card_conflicts():
    first = _normalize(NEIGHBORS['ON01']['cards'][0]['source_fields'], housing_filter=False)
    assert assess_work(first, {'schedule': 'удалённая работа'}, None)[0]
    second = _normalize(NEIGHBORS['ON02']['cards'][0]['source_fields'], housing_filter=False)
    assert assess_work(second, {'housing': True}, None)[0]
    third = NEIGHBORS['ON03']['cards'][0]['source_fields']
    assert assess_rental(third, {'name': third['location']['name'], 'location_id': third['location']['location_id']}, 27000)[0]


def test_open_neighbor_negation_consent_and_wish_changes():
    for case_id in ('ON04', 'ON05', 'ON06'):
        text = NEIGHBORS[case_id]['turns'][0]['user']
        assert not other_task_requested(text)
        assert not rental_extra(text)
    for case_id in ('ON07', 'ON08', 'ON09', 'ON10', 'ON11', 'ON12'):
        turns = NEIGHBORS[case_id]['turns']
        assert not consent_to_unchecked(turns[0]['user'])
        assert consent_to_unchecked(turns[-1]['user'])
    assert not consent_to_unchecked(NEIGHBORS['ON09']['turns'][1]['user'])
    for case_id, expected in [('ON10', 2), ('ON12', 1)]:
        first, second = [t['user'] for t in NEIGHBORS[case_id]['turns'][:2]]
        wishes = update_wishes([], first, rental_extra(first))
        assert len(wishes) == 3 if case_id == 'ON10' else len(wishes) == 2
        assert len(update_wishes(wishes, second, rental_extra(second))) == expected


def test_open_neighbor_monthly_rent_boundaries():
    assert not rental_extra(NEIGHBORS['ON16']['turns'][0]['user'])
    assert rental.budget_value('75 тысяч при заселении') is None
    assert not re.search(TOTAL_COST, 'месячная аренда отдельно от коммунальных платежей', re.I)
    assert not rental_extra('Месячная аренда отдельно от разового залога и комиссии.')
    assert rental_extra('Нужна квартира без залога и комиссии.')
    assert re.search(TOTAL_COST, NEIGHBORS['ON18']['turns'][0]['user'], re.I)


def test_open_neighbor_monthly_budget_state(monkeypatch):
    class Owner:
        branches = {'rental': {}}
        def record(self, *args, **kwargs): pass

    branch = rental.RentalBranch(Owner())
    state = SimpleNamespace(rental={})
    branch.state(state).update(city={'name': 'Улан-Удэ', 'location_id': 1}, awaiting='budget')
    async def ready(*args): return []
    monkeypatch.setattr(branch, 'ready', ready)

    async def feed(text, evidence):
        async def saved(*args, **kwargs):
            return json.dumps({'city': None, 'budget': {'value': None, 'evidence': evidence}, 'other': None, 'action': None}, ensure_ascii=False)
        monkeypatch.setattr(rental, 'call_llm', saved)
        await branch.input(None, state, text)

    async def scenario():
        first = NEIGHBORS['ON18']['turns'][0]['user']
        await feed(first, 'максимум 34 тысячи рублей каждый месяц')
        assert state.rental['budget'] is None and state.rental['budget_issue']
        assert 'коммунальными' in state.rental['other']
        state.rental.update(awaiting='budget', other_shown=True)
        second = NEIGHBORS['ON18']['turns'][1]['user']
        await feed(second, '30 тысяч рублей за месяц')
        assert state.rental['budget'] == 30000
        assert state.rental['other_accepted']
        assert len(state.rental['other_parts']) == 1

        state.rental.update(budget=None, budget_issue=False, other=None, other_parts=[], other_accepted=False, awaiting='budget')
        first = NEIGHBORS['ON17']['turns'][0]['user']
        await feed(first, '75 тысяч')
        assert state.rental['budget'] is None

    import asyncio
    asyncio.run(scenario())


def test_open_neighbor_work_wishes_and_optional_profession():
    class Owner:
        def record(self, *args, **kwargs): pass

    branch = FreeWorkBranch(Owner())
    for case_id, expected in [('ON08', 1), ('ON11', 2)]:
        state = SimpleNamespace(work={}, values={'role': 'child'}, offset=0, buffer=[], seen=set())
        first = NEIGHBORS[case_id]['turns'][0]['user']
        branch.apply(state, {}, first)
        assert len(state.work['other_parts']) == (3 if case_id == 'ON11' else 1)
        state.work['awaiting'] = 'other'
        second = NEIGHBORS[case_id]['turns'][1]['user']
        branch.apply(state, {}, second)
        assert len(state.work['other_parts']) == expected
        assert state.work['other_accepted']

    for case_id in ('ON13', 'ON15'):
        text = NEIGHBORS[case_id]['turns'][0]['user']
        assert validate_fields({}, text, NEIGHBORS[case_id]['role'])['query']['status'] == 'any'
    state = SimpleNamespace(work={}, values={'role': 'child'}, offset=0, buffer=[], seen=set())
    branch.state(state)['fields']['query'] = field('known', 'реставратор мебели', 'реставратором мебели')
    branch.apply(state, {}, NEIGHBORS['ON14']['turns'][1]['user'])
    assert state.work['fields']['query']['status'] == 'any'
