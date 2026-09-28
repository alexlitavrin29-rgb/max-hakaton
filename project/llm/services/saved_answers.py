"""Explicit catalog of complete, public child-facing answers; no dialogue values."""
import hashlib
import json
from pathlib import Path
from urllib.parse import quote


TITLES = json.loads((Path(__file__).resolve().parents[1] / 'data/saved_answers.json').read_text(encoding='utf-8'))


def material(config, node_id):
    node = next((n for n in config['nodes'] if n['id'] == node_id), None)
    branch = next((b for b in config['branches'] if node and b['id'] == node['branch']), {})
    if (node_id not in TITLES or not node or node['kind'] != 'message'
            or 'child' not in branch.get('roles', []) or node.get('conditions')
            or node.get('routes') or node.get('next') or not node.get('text', '').strip()
            or '{' in node['text'] or any('{' in t for t in node.get('tasks', []))
            or node.get('content_status') == 'incomplete'
            or any(b.get('target') and not b.get('values', {}).get('navigation') for b in node.get('buttons', []))):
        return None
    text = node['text']
    if node.get('tasks'):
        text += '\n\n' + '\n'.join('☐ ' + task for task in node['tasks'])
    actions = [dict(label=b['label'], url=b['url']) for b in node.get('buttons', []) if b.get('url')]
    for key in node.get('sources', []):
        source = config.get('sources', {}).get(key)
        if source and not any(a['url'] == source['url'] for a in actions):
            actions.append(dict(label=source['title'], url=source['url']))
    digest = hashlib.sha256(json.dumps([TITLES[node_id], text, actions], ensure_ascii=False).encode()).hexdigest()
    return dict(section='advice', node_id=node_id, title=TITLES[node_id], text=text,
                subtitle=branch.get('title', ''), status='information', actions=actions,
                content_hash=digest, favorite_id=hashlib.sha256(('advice:' + node_id).encode()).hexdigest())


def buttons(config, node_id):
    bot = config.get('rules', {}).get('miniapp_answers_bot')
    card = material(config, node_id) if bot else None
    if not card:
        return []
    url = f'https://max.ru/{bot}?startapp=view_{node_id}'
    return [dict(type='open_app', text='♡ Сохранить в Моё', web_app=bot, payload='save_' + node_id),
            dict(type='link', text='Поделиться ↗',
                 url='https://max.ru/:share?text=' + quote(card['title'] + '\n' + url, safe=''))]
