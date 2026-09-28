import asyncio
from copy import deepcopy
from types import SimpleNamespace

import pytest

from project.admin.contents_navigation_draft import configure
from project.admin.tests.test_work_contract import config, job, buttons, output
from project.llm.services import flow
from project.llm.services.work import field
from project.llm.integrations.trudvsem import TrudvsemError


@pytest.mark.parametrize('contract', [2, 3])
@pytest.mark.parametrize('case', ['empty', 'filtered_more', 'exhausted', 'error', 'success'])
def test_external_search_when_no_cards(monkeypatch, contract, case):
    async def search(query, **kwargs):
        if case == 'error':
            raise TrudvsemError('unavailable')
        items = [job(salary_from=10000, salary_to=20000)] if case == 'filtered_more' else [job()] if case == 'success' else []
        return SimpleNamespace(items=items, next_offset=100 if case == 'filtered_more' else None)

    monkeypatch.setattr(flow, 'search_vacancies', search)

    async def run():
        cfg, _ = configure(config())
        cfg['search']['contract_version'] = contract
        engine = flow.FlowDialogue(flow.PreviewReminders(), cfg, 3)
        session = engine.session(1)
        state = engine.work.state(session)
        state['fields']['salary'] = field('known', 60000, 'от 60000')
        state['fields']['age'] = field('known', 20, '20 лет')
        conditions = deepcopy(state['fields'])
        if case == 'exhausted':
            session.offset = None
        replies = await engine.work.search(session)
        text = output(replies)
        assert ('https://hh.ru/search/vacancy' in text) == (case != 'success')
        assert ('https://www.superjob.ru/vacancy/search/' in text) == (case != 'success')
        assert state['fields'] == conditions
        if case != 'success':
            assert next(r for r in replies if 'https://hh.ru/search/vacancy' in r['text'])['format'] == 'markdown'
        if case == 'error':
            assert any(b['text'] == 'К оглавлению' for b in buttons(replies))
            assert any(':more' in b.get('payload', '') for b in buttons(replies))
        if case == 'filtered_more':
            assert session.offset is not None
        if case == 'success':
            assert any(b.get('url') == job().url for b in buttons(replies))

    asyncio.run(run())
