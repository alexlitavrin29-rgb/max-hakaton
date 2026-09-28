"""Restore Back beside Contents in the active scenario, preserving other fields."""
import asyncio
from copy import deepcopy
import json
import os
from pathlib import Path
import sys
import urllib.request

from project.llm.services.flow import FlowDialogue, PreviewReminders
from project.llm.services.scenario import validate


def request(path, body=None, method=None):
    req = urllib.request.Request('http://127.0.0.1:8765/api/' + path,
        data=json.dumps(body, ensure_ascii=False).encode() if body is not None else None,
        method=method, headers={'Content-Type': 'application/json', 'X-Editor-Request': 'local'})
    with urllib.request.urlopen(req, timeout=30) as response:
        return json.load(response)


def configure(original):
    config = deepcopy(original)
    nodes = {n['id']: n for n in config['nodes']}
    active = {b['id'] for b in config['branches'] if 'child' in b['roles']}
    incoming = {key: [] for key in nodes}
    for node in nodes.values():
        if node['branch'] not in active:
            continue
        for button in node.get('buttons', []):
            target = button.get('target')
            if target in nodes and target != node['id'] and not button.get('values', {}).get('navigation'):
                incoming[target].append(node['id'])
    final = {n['id'] for n in nodes.values() if n['branch'] in active
             and any(b.get('values', {}).get('navigation') == 'contents' for b in n.get('buttons', []))}
    penultimate = {parent for key in final for parent in incoming[key]}
    changed = []
    for key in sorted(final | penultimate):
        node = nodes[key]
        if key == config['start'] or any(b.get('values', {}).get('navigation') == 'back' for b in node.get('buttons', [])):
            continue
        parents = incoming[key]
        parent = next((p for p in parents if nodes[p]['branch'] == node['branch']), None)
        parent = parent or next(iter(parents), config['menu'])
        node.setdefault('buttons', []).append(dict(label='Назад', target=parent, values={'navigation': 'back'}))
        changed.append(key)
    validate(config)
    return config, dict(changed=changed, final=sorted(final), penultimate=sorted(penultimate))


async def verify(config, report):
    bot = FlowDialogue(PreviewReminders(), config, 1)
    checked = 0
    for key in sorted(set(report['final'] + report['penultimate']) - {config['start']}):
        node = bot.nodes[key]
        session = bot.session(1)
        session.values = {'role': 'child'}
        rendered = bot.node_buttons(node, session)
        back = [b for b in rendered if b['text'] == 'Назад']
        assert len(back) == 1, key
        if key in report['final']:
            assert any(b['text'] == 'К оглавлению' for b in rendered), key
        # Exercise actual callback dispatch, then the preceding screen's Back.
        fallback = next(b['target'] for b in node['buttons'] if b.get('values', {}).get('navigation') == 'back')
        parent = bot.nodes[fallback]
        if parent['kind'] != 'message':
            continue
        for history in ([], [fallback]):
            session.node = key
            session.history = list(history)
            await bot.handle(1, payload=back[0]['payload'])
            assert session.node == fallback, (key, fallback, session.node)
        checked += 1
    return dict(checked_back_routes=checked, final=len(report['final']), penultimate=len(report['penultimate']))


async def main():
    mode = sys.argv[1]
    path = Path('/tmp/back-navigation-before.json')
    if mode == 'snapshot':
        path.write_text(json.dumps(request('state'), ensure_ascii=False), encoding='utf-8')
        return
    before = json.loads(path.read_text(encoding='utf-8'))
    config, report = configure(before['config'])
    assert configure(config)[0] == config
    report['checks'] = await verify(config, report)
    if mode == 'publish':
        import asyncpg
        from project.llm.services.scenario import ScenarioStore
        assert request('state') == before, 'Draft changed since snapshot'
        pool = await asyncpg.create_pool(host=os.getenv('POSTGRES_HOST', 'postgres'),
            port=int(os.getenv('POSTGRES_PORT', '5432')), user=os.getenv('POSTGRES_USER', 'support_router'),
            password=os.environ['POSTGRES_PASSWORD'], database=os.getenv('POSTGRES_DB', 'support_router'))
        store = ScenarioStore(pool)
        current = await store.read(published=True)
        assert current['config'] == before['config'], 'Unpublished edits exist'
        saved = request('draft', dict(config=config, revision=before['revision']), 'PUT')
        result = request('publish', dict(revision=saved['revision'],
            note='Назад рядом с К оглавлению в конце веток; возврат на предпоследних экранах'), 'POST')
        after = await store.read(published=True)
        assert after['config'] == config and after['revision'] == result['published_id']
        report['checks_after_publish'] = await verify(after['config'], report)
        report.update(previous_published=before['published_id'], revision=saved['revision'], published=after['revision'])
        await pool.close()
    Path('/tmp/back-navigation-result.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps({k: v for k, v in report.items() if k not in {'changed', 'final', 'penultimate'}}, ensure_ascii=False))
    print('Added Back to', len(report['changed']), 'screens')


if __name__ == '__main__':
    asyncio.run(main())
