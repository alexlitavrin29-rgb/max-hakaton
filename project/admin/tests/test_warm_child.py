import asyncio
import json
import re
from pathlib import Path

from project.admin.warm_child_draft import configure, SCREENS, UI
from project.admin.export_role_copy import graph
from project.llm.services.flow import FlowDialogue, PreviewReminders


def snapshot():
    return json.loads(Path('docs/test-results/warm-child-2026-09-27/server-before.json').read_text(encoding='utf-8'))['config']


def test_copy_preserves_details_links_placeholders_and_controls():
    original = snapshot()
    config, changed = configure(original)
    nodes = {n['id']: n for n in config['nodes']}
    assert len(changed) == 176
    for before in original['nodes']:
        after = nodes[before['id']]
        assert before['tasks'] == after['tasks']
        assert before['sources'] == after['sources']
        assert before.get('adult_text') == after.get('adult_text')
        if before['id'] != original['start']:
            assert before['buttons'] == after['buttons']
        for url in re.findall(r'https?://[^\s)]+', before['text']):
            assert url in after['text']
        if before['id'] in changed and before['id'] not in SCREENS:
            assert all(part in after['text'] for part in before['text'].split('\n\n'))
    for key in UI:
        if key in config['ui']:
            assert set(re.findall(r'{[^}]+}', config['ui'][key]['text'])) == set(re.findall(r'{[^}]+}', original['ui'][key]['text']))
    assert configure(config)[0] == config
    assert not original['rules'].get('child_only')


def test_all_static_child_screens_and_retired_role_entry():
    async def run():
        config, _ = configure(snapshot())
        bot = FlowDialogue(PreviewReminders(), config)
        replies = await bot.handle(1, payload='reset')
        controls = [b for a in replies[0]['attachments'] for row in a['payload']['buttons'] for b in row]
        assert [b['text'] for b in controls] == ['Я ребёнок']
        for role, target in [('parent', 'pa_home'), ('candidate', 'family')]:
            bot.session(1).values['role'] = role
            await bot.handle(1, payload='jump:' + target)
            assert bot.session(1).node == config['menu']
            assert bot.session(1).values['role'] == 'child'
        for key in graph(config, 'child'):
            node = bot.nodes[key]
            if node['kind'] != 'message' or key in {'rental', 'help_points', 'hp_city'}:
                continue
            replies = await bot.enter(1, bot.session(1), key)
            assert replies and replies[0]['text'].strip(), key
            assert len(replies[0]['text']) <= 4000, key
    asyncio.run(run())
