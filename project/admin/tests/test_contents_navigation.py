import asyncio
from copy import deepcopy

import pytest

from project.admin.contents_navigation_draft import configure
from project.admin.seed import initial_config
from project.llm.services.flow import FlowDialogue, PreviewReminders


def buttons(replies):
    return [b for reply in replies for a in reply.get('attachments', [])
            for row in a['payload']['buttons'] for b in row]


def scenario():
    c = initial_config()
    c['rules']['navigation_back'] = True
    c['nodes'] = []
    c['branches'] = [dict(id='start', roles=['child', 'parent', 'candidate'])]
    c['start'], c['menu'] = 'welcome', 'home_child'
    c['bindings'] = {}
    c['ui'] = {}
    for role in ['child', 'parent', 'candidate']:
        for name, target in [('home', 'section'), ('section', 'questions'), ('questions', 'answer'), ('answer', None)]:
            parent = {'home': 'welcome', 'section': 'home_' + role,
                      'questions': 'section_' + role, 'answer': 'questions_' + role}[name]
            controls = [dict(label=target, target=target + '_' + role)] if target else []
            controls.append(dict(label='Назад', target=parent, values={'navigation': 'back'}))
            c['nodes'].append(dict(id=name + '_' + role, title=name, text=name, kind='message',
                                   branch='start', buttons=controls))
    c['nodes'].append(dict(id='welcome', title='start', text='start', kind='message', branch='start',
                          buttons=[dict(label=r, target='home_' + r, values={'role': r})
                                   for r in ['child', 'parent', 'candidate']]))
    return c


@pytest.mark.parametrize('role', ['child', 'parent', 'candidate'])
def test_final_answer_returns_to_role_home_then_role_selection(role):
    async def run():
        config, _ = configure(scenario())
        bot = FlowDialogue(PreviewReminders(), config)
        replies = await bot.handle(1, payload='reset')
        for label in [role, 'section', 'questions', 'answer']:
            replies = await bot.handle(1, payload=next(b['payload'] for b in buttons(replies) if b['text'] == label))
        assert [b['text'] for b in buttons(replies)] == ['К оглавлению']
        bot.session(1).rental['budget'] = 30000
        replies = await bot.handle(1, payload=buttons(replies)[0]['payload'])
        assert bot.session(1).node == 'home_' + role
        assert bot.session(1).values['role'] == role
        assert bot.session(1).rental['budget'] == 30000
        assert 'К оглавлению' not in [b['text'] for b in buttons(replies)]
        await bot.handle(1, payload=next(b['payload'] for b in buttons(replies) if b['text'] == 'Назад'))
        assert bot.session(1).node == 'welcome'
    asyncio.run(run())


@pytest.mark.parametrize('role', ['child', 'parent', 'candidate'])
def test_intermediate_back_and_direct_final_entry(role):
    async def run():
        config, _ = configure(scenario())
        bot = FlowDialogue(PreviewReminders(), config)
        bot.session(1).values['role'] = role
        replies = await bot.handle(1, payload='jump:questions_' + role)
        back = next(b for b in buttons(replies) if b['text'] == 'Назад')
        await bot.handle(1, payload=back['payload'])
        assert bot.session(1).node == 'section_' + role
        replies = await bot.handle(1, payload='jump:answer_' + role)
        await bot.handle(1, payload=buttons(replies)[0]['payload'])
        assert bot.session(1).node == 'home_' + role
    asyncio.run(run())


def test_configure_is_idempotent_and_preserves_content():
    before = scenario()
    original = deepcopy(before)
    changed, final = configure(before)
    assert before == original
    assert configure(changed)[0] == changed
    assert set(final) == {'answer_child', 'answer_parent', 'answer_candidate'}
    assert [(n['id'], n['text']) for n in changed['nodes']] == [(n['id'], n['text']) for n in before['nodes']]


def test_dynamic_results_and_legacy_button():
    from project.llm.services.rental import RentalBranch
    from project.llm.services.help_points import HelpPointsBranch
    async def run():
        c, _ = configure(scenario())
        bot = FlowDialogue(PreviewReminders(), c)
        s = bot.session(1)
        s.values['role'] = 'candidate'
        controls = RentalBranch(bot).controls(s)
        assert controls[-1]['text'] == 'К оглавлению'
        await bot.handle(1, payload=controls[-1]['payload'])
        assert s.node == 'home_candidate'
        assert HelpPointsBranch(bot).navigation(final=True) == [bot.menu_button(contents=True)]
        assert HelpPointsBranch(bot).navigation() == [bot.menu_button()]
        del c['rules']['navigation_contents']
        assert bot.menu_button(contents=True)['text'] == 'Назад'
    asyncio.run(run())
