"""Simplify draft navigation and link presentation without publishing to MAX."""

from collections import defaultdict
from copy import deepcopy
import json
import re

from project.admin.documents_reference_draft import ROOT, request
from project.llm.services.scenario import validate


NAVIGATION = {'Назад', 'На главную', 'Главное меню', 'К оглавлению', 'Начать заново'}
UPWARD = {'К разделу семьи', 'К темам для родителей', 'К льготам и выплатам',
          'К документам', 'К учёбе', 'К видам помощи'}
MORE = 'Что ещё может понадобиться'
EMOJI = re.compile(r'[\U0001F000-\U0001FAFF\u2600-\u27BF]\ufe0f?\s*')
BARE_LINK = re.compile(r'(?m)^([^\n:]{2,65}):\s*(https://\S+)\s*$')
HEADING_IDS = {'doc_lost_start', 'doc_lost_inventory', 'doc_lost_police',
               'doc_lost_passport', 'doc_lost_others', 'doc_lost_photos', 'doc_lost_fraud'}
HEADING = re.compile(r'^(?:[1-7])\.\s+(?=[А-ЯЁA-Z])')


def clean_emoji(value):
    if not isinstance(value, str) or not EMOJI.search(value): return value
    return EMOJI.sub('', value).strip()


def configure(original):
    config = deepcopy(original)
    nodes = {node['id']: node for node in config['nodes']}
    incoming = defaultdict(list)
    for node in config['nodes']:
        for button in node.get('buttons', []):
            if button.get('target') in nodes and button['label'] not in NAVIGATION | UPWARD | {MORE}:
                incoming[button['target']].append(node['id'])

    extras = set()
    counts = defaultdict(int)
    for node in config['nodes']:
        for key in ('title', 'text', 'adult_text'):
            if key in node:
                cleaned = clean_emoji(node[key])
                if cleaned != node[key]: counts['emoji_fields'] += 1
                node[key] = cleaned

        if node['id'] in HEADING_IDS:
            for key in ('text', 'adult_text'):
                if key in node:
                    prefix = 'Памятка обращена к ребёнку или выпускнику.\n\n'
                    body = node[key][len(prefix):] if node[key].startswith(prefix) else node[key]
                    body, changed = HEADING.subn('', body, count=1)
                    node[key] = (prefix if node[key].startswith(prefix) else '') + body
                    counts['headings'] += changed
        if node['id'] == 'doc_lost_police':
            for key in ('text', 'adult_text'):
                node[key] = node[key].replace('\nОбратись в ближайший отдел полиции.',
                                              '\n1. Обратись в ближайший отдел полиции.', 1)
        if node['id'] == 'doc_lost_passport_q1':
            for key in ('text', 'adult_text'):
                node[key] = node[key].replace('\nОбратись в подразделение МВД',
                                              '\n1. Обратись в подразделение МВД', 1)
                node[key] = node[key].replace('\nЗаполни заявление', '\n2. Заполни заявление', 1)

        buttons = node.get('buttons', [])
        more = next((button for button in buttons if button['label'] == MORE), None)
        if more:
            extra = nodes[more['target']]
            for key in ('text', 'adult_text'):
                if extra.get(key):
                    node[key] = (node.get(key) or node['text']) + '\n\n' + extra[key]
            extras.add(extra['id'])
            counts['merged_extras'] += 1

        links = [button for button in buttons if button.get('url')]
        if links:
            for key in ('text', 'adult_text'):
                if key not in node: continue
                additions = [f"[{clean_emoji(button['label'])}]({button['url']})" for button in links
                             if button['url'] not in node[key]]
                if additions:
                    node[key] += '\n\nСсылки:\n' + '\n'.join(additions)
            node['format'] = 'markdown'
            counts['inline_links'] += len(links)

        for key in ('text', 'adult_text'):
            if key in node:
                node[key], changed = BARE_LINK.subn(r'[\1](\2)', node[key])
                if changed:
                    node['format'] = 'markdown'
                    counts['bare_links'] += changed

        nav = next((button for button in buttons if button['label'] == 'Назад'), None)
        fallback = (nav or {}).get('target') or next((parent for parent in incoming[node['id']]
                                                      if parent != node['id']), None)
        if node['id'] in {config['menu'], 'pa_home', 'family'}: fallback = config['start']
        fallback = fallback or (config['start'] if node['id'] == config['menu'] else config['menu'])
        kept = []
        for button in buttons:
            if button['label'] in NAVIGATION | UPWARD or button['label'] == MORE or button.get('url'):
                counts['removed_buttons'] += 1
                continue
            updated = deepcopy(button)
            updated['label'] = clean_emoji(updated['label'])
            if updated['label'] != button['label']: counts['emoji_fields'] += 1
            kept.append(updated)
        if node['id'] != config['start']:
            kept.append(dict(label='Назад', target=fallback, values={'navigation': 'back'}))
            counts['back_buttons'] += 1
        node['buttons'] = kept

    config['nodes'] = [node for node in config['nodes'] if node['id'] not in extras]
    config['ui']['button_menu']['text'] = 'Назад'
    config['ui']['button_cancel']['text'] = 'Назад'
    config['rules']['navigation_back'] = True
    validate(config)
    return config, dict(counts)


def main():
    before = request('state')
    config, counts = configure(before['config'])
    saved = request('draft', dict(config=config, revision=before['revision']), 'PUT')
    after = request('state')
    assert after['revision'] == saved['revision'] and after['config'] == config
    assert after['published_id'] == before['published_id']
    result = dict(revision_before=before['revision'], revision=after['revision'],
                  published_id=after['published_id'], nodes=len(config['nodes']), **counts)
    output = ROOT / 'docs/test-results/navigation-copy'
    output.mkdir(parents=True, exist_ok=True)
    (output / 'update.json').write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps(result, ensure_ascii=False))


if __name__ == '__main__': main()
