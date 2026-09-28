import asyncio
from types import SimpleNamespace

import pytest

from project.admin.seed import initial_config
from project.admin.work_draft import configure_work, configure_free_work
from project.llm.services import flow
from project.llm.services.work import field
from project.miniapp.service import MiniDialogue, present


@pytest.mark.parametrize('configure', [configure_work, configure_free_work])
@pytest.mark.parametrize('role', ['child', 'parent', 'miniapp'])
def test_search_and_more_without_age(monkeypatch, configure, role):
    calls = []

    async def search(*args, **kwargs):
        calls.append(kwargs)
        return SimpleNamespace(items=[], next_offset=kwargs['offset']+1)

    monkeypatch.setattr(flow, 'search_vacancies', search)

    async def run():
        config = configure(initial_config())
        engine = MiniDialogue(config, 1) if role == 'miniapp' else flow.FlowDialogue(flow.PreviewReminders(), config, 1)
        session = engine.session(1)
        if role != 'miniapp': session.values['role'] = role
        replies = await engine.handle(1, payload='jump:work')
        assert 'возраст' not in '\n'.join(r['text'] for r in replies).lower()
        engine.work.apply(session, {'city': field('known', 'Томск', 'Томск')}, 'Томск')
        for entry in ('work_age', 'work_search', 'work_more'):
            replies = await engine.enter(1, session, entry)
            assert session.work['awaiting'] != 'age'
            assert session.work['fields']['age']['value'] is None
            assert 'возраст' not in '\n'.join(r['text'] for r in replies).lower()
            if role == 'miniapp':
                view, _ = present(engine, replies, 'work')
                assert 'возраст' not in view['summary'].lower()
        assert len(calls) == 3

    asyncio.run(run())
