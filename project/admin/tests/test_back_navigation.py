import asyncio
from copy import deepcopy
from pathlib import Path
import runpy

from project.admin.tests.test_contents_navigation import buttons, scenario
from project.admin.contents_navigation_draft import configure as contents_configure
from project.llm.services.flow import FlowDialogue, PreviewReminders


configure = runpy.run_path(str(Path(__file__).resolve().parents[3] /
    'docs/back-navigation-2026-09-27/update.py'))['configure']


def test_back_returns_to_questions_then_allows_another_answer():
    async def run():
        original, _ = contents_configure(scenario())
        sibling = deepcopy(next(n for n in original['nodes'] if n['id'] == 'answer_child'))
        sibling['id'] = 'another_answer'
        original['nodes'].append(sibling)
        questions = next(n for n in original['nodes'] if n['id'] == 'questions_child')
        questions['buttons'].insert(1, dict(label='another', target='another_answer'))
        config, _ = configure(original)
        bot = FlowDialogue(PreviewReminders(), config)
        replies = await bot.handle(1, payload='reset')
        for label in ['child', 'section', 'questions', 'answer', 'Назад', 'another']:
            replies = await bot.handle(1, payload=next(b['payload'] for b in buttons(replies) if b['text'] == label))
        assert bot.session(1).node == 'another_answer'
        for expected in ['questions_child', 'section_child']:
            replies = await bot.handle(1, payload=next(b['payload'] for b in buttons(replies) if b['text'] == 'Назад'))
            assert bot.session(1).node == expected
    asyncio.run(run())


def test_existing_contents_and_other_fields_are_preserved():
    original, _ = contents_configure(scenario())
    untouched = deepcopy(original)
    config, report = configure(original)
    assert original == untouched
    assert configure(config)[0] == config
    assert report['changed'] == ['answer_candidate', 'answer_child', 'answer_parent']
    for old, new in zip(original['nodes'], config['nodes']):
        assert {k: v for k, v in old.items() if k != 'buttons'} == {k: v for k, v in new.items() if k != 'buttons'}
        assert new['buttons'][:len(old['buttons'])] == old['buttons']
    async def run():
        bot = FlowDialogue(PreviewReminders(), config)
        replies = await bot.handle(1, payload='jump:answer_child')
        await bot.handle(1, payload=next(b['payload'] for b in buttons(replies) if b['text'] == 'К оглавлению'))
        assert bot.session(1).node == 'home_child'
    asyncio.run(run())


def test_missing_penultimate_back_is_added():
    original, _ = contents_configure(scenario())
    questions = next(n for n in original['nodes'] if n['id'] == 'questions_child')
    questions['buttons'] = [b for b in questions['buttons'] if b.get('values', {}).get('navigation') != 'back']
    config, report = configure(original)
    assert 'questions_child' in report['changed']
    questions = next(n for n in config['nodes'] if n['id'] == 'questions_child')
    assert questions['buttons'][-1] == dict(label='Назад', target='section_child', values={'navigation': 'back'})
