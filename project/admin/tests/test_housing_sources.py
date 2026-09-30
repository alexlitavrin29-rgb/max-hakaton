"""Housing content must reach chat and saved cards without changing routes."""
import asyncio
from copy import deepcopy
import json
from pathlib import Path

from project.admin.housing_sources_update import configure, ANSWERS, EXTRA, INTROS
from project.llm.services.flow import FlowDialogue, PreviewReminders
from project.llm.services.saved_answers import material, TITLES


def baseline():
    return json.loads((Path(__file__).resolve().parents[3] / 'submission/scenario.json').read_text(encoding='utf-8'))['config']


def test_only_housing_content_changes_and_reapply_is_safe():
    original = baseline()
    result = configure(original)
    assert configure(result) == result
    allowed = {'text', 'adult_text', 'tasks', 'sources', 'content_status', 'editorial_sources', 'editorial_note', 'format'}
    for before, after in zip(original['nodes'], result['nodes']):
        if before['branch'] != 'housing':
            assert before == after
        else:
            assert {k:v for k,v in before.items() if k not in allowed} == {k:v for k,v in after.items() if k not in allowed}
    for key in original.keys() - {'nodes', 'sources', 'materials'}:
        assert original[key] == result[key]
    assert result['rules']['child_only']


def test_chat_and_saved_cards_expose_sources_and_navigation():
    async def run():
        config = configure(baseline())
        engine = FlowDialogue(PreviewReminders(), config, 10)
        count = 0
        for node_id in set(ANSWERS) | set(EXTRA) | set(INTROS):
            node = engine.nodes[node_id]
            session = engine.session(1)
            session.values['role'] = 'child'
            replies = await engine.enter(1, session, node_id)
            assert session.node == node_id
            assert node['text'] in replies[0]['text']
            buttons = [b for r in replies for a in r.get('attachments', []) for row in a['payload']['buttons'] for b in row]
            expected = {config['sources'][s]['url'] for s in node['editorial_sources']}
            assert replies[0]['format'] == 'markdown'
            assert not node['sources']
            assert not expected.intersection(b.get('url') for b in buttons)
            for key in node['editorial_sources']:
                source = config['sources'][key]
                assert '[' + source['title'] + '](' + source['url'] + ')' in replies[0]['text']
            assert any(b.get('payload') for b in buttons), node_id
            if node_id in TITLES:
                card = material(config, node_id)
                assert card and not expected.intersection(a['url'] for a in card['actions'])
                assert card['text'] == node['text']
                altered = deepcopy(config)
                next(n for n in altered['nodes'] if n['id'] == node_id)['text'] += '\nОбновлено'
                assert material(altered, node_id)['content_hash'] != card['content_hash']
                count += 1
        assert count >= 33
    asyncio.run(run())


def test_housing_has_no_abandonment_disclaimer_or_missing_source():
    config = configure(baseline())
    for node in config['nodes']:
        if node['branch'] != 'housing':
            continue
        assert node.get('editorial_sources') and not node['sources'], node['id']
        for text in [node.get('text', ''), node.get('adult_text', '')]:
            assert 'не несёт ответственности' not in text.lower()
            assert 'не несет ответственности' not in text.lower()
        assert all(config['sources'][s]['url'].startswith('https://') for s in node['editorial_sources'])
