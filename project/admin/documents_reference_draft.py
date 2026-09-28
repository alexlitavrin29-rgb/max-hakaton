"""Owner's five-section reference, edited for tone. Draft only, no publication."""
from copy import deepcopy
import json
from pathlib import Path
import urllib.request

from project.llm.services.scenario import validate

ROOT = Path(__file__).resolve().parents[2]
COPY = ROOT / 'docs/documents-reference-copy.json'
BASE = 'http://127.0.0.1:18765/api/'


def request(path, body=None, method=None):
    req = urllib.request.Request(
        BASE + path, method=method,
        data=None if body is None else json.dumps(body, ensure_ascii=False).encode(),
        headers={'Content-Type': 'application/json', 'X-Editor-Request': 'local'},
    )
    with urllib.request.urlopen(req, timeout=20) as response:
        return json.load(response)


def configure(config):
    c = deepcopy(config)
    sections = json.loads(COPY.read_text(encoding='utf-8'))
    nodes = {n['id']: n for n in c['nodes']}
    main = nodes[c['menu']]
    main['buttons'] = [dict(label='Работа', target='work'), dict(label='Документы', target='documents')] + [
        b for b in main['buttons'] if b.get('target') not in {'work', 'documents'}
    ]
    main['text'] = 'Выбери, с чем помочь. В «Работе» можно найти вакансии, в остальных разделах — прочитать памятки и списки.'
    main['adult_text'] = 'Выберите, с чем помочь. В «Работе» можно найти вакансии, в остальных разделах — прочитать памятки и списки.'
    for branch in c['branches']:
        if branch['id'] == 'documents':
            branch['status'] = 'draft'
    intro = ('ДОКУМЕНТЫ\n\nПосле выпуска из детского дома документы помогают подтвердить твои права '
             'и оформить то, что нужно для работы, учёбы, жилья и медицинской помощи. Здесь разберёмся, '
             'зачем нужны основные документы, где их оформляют, что делать при потере и как пользоваться Госуслугами.\n\n'
             'Выбери тему ниже.\n\nЧерновик по присланной памятке: факты, сроки и порядок оформления ещё не проверены.')
    nodes['documents'].update(text=intro, adult_text='Памятки для ребёнка или выпускника.\n\n' + intro,
                              next='', tasks=[], sources=[], routes=[], conditions=[], kind='message',
                              buttons=[dict(label=s['title'], target=s['id']) for s in sections] +
                                      [dict(label='На главную', target=c['menu'])])
    source_key = 'documents_owner_reference'
    c['sources'][source_key] = dict(title='Маршрут — присланная памятка о документах (не проверено)',
                                  url='https://www.marshrut.online/документы', checked='')
    material_ids = {'documents_reference_' + s['id'] for s in sections}
    c['materials'] = [m for m in c['materials'] if m['id'] not in material_ids]
    for section in sections:
        page_ids = [section['id']] + [section['id'] + '_reference_' + str(i + 1)
                                     for i in range(1, len(section['pages']))]
        for i, (key, page) in enumerate(zip(page_ids, section['pages'])):
            title = section['title'] if i == 0 else page['title']
            text = page['text']
            if len(page_ids) > 1:
                text = f"{section['title']} · {i + 1} из {len(page_ids)}\n\n" + text
            buttons = []
            if i + 1 < len(page_ids):
                buttons.append(dict(label='Продолжить', target=page_ids[i + 1]))
            if i:
                buttons.append(dict(label='Предыдущая страница', target=page_ids[i - 1]))
            buttons += [dict(label='К оглавлению', target='documents'),
                        dict(label='На главную', target=c['menu']),
                        dict(label='Напомнить', target='reminder_new',
                             values={'reminder_text': 'Вернуться к памятке о документах.'})]
            nodes[key] = dict(id=key, title=title, branch='documents', kind='message', text=text,
                              adult_text='Памятка обращена к ребёнку или выпускнику.\n\n' + text,
                              tasks=[], sources=[], routes=[], conditions=[], next='', buttons=buttons,
                              content_status='unverified_reference', editorial_sources=[source_key])
        c['materials'].append(dict(
            id='documents_reference_' + section['id'], title=section['title'] + ' — редакторский черновик',
            text='НЕ ПРОВЕРЕНО. Редактура присланного владельцем текста; не юридическая фактпроверка.\n\n'
                 + 'К проверке: ' + section['review'] + '\n\n'
                 + '\n\n'.join(p['text'] for p in section['pages']),
            branches=['documents'], roles=[], regions=[], min_age=None, max_age=None,
            sources=[source_key], checked='', review_days=90, enabled=False,
        ))
    c['nodes'] = list(nodes.values())
    return validate(c)


def main():
    before = request('state')
    config = configure(before['config'])
    assert configure(config) == config
    output = ROOT / 'docs/test-results/documents-reference'
    output.mkdir(parents=True, exist_ok=True)
    (output / 'before.json').write_text(json.dumps(before['config'], ensure_ascii=False, indent=2), encoding='utf-8')
    saved = request('draft', dict(config=config, revision=before['revision']), 'PUT')
    after = request('state')
    assert after['revision'] == saved['revision']
    assert after['published_id'] == before['published_id']
    assert after['config'] == config
    result = dict(revision_before=before['revision'], revision=after['revision'],
                  published_id=after['published_id'], sections=5, pages=10, saved_matches=True)
    (output / 'update.json').write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps(result, ensure_ascii=False))


if __name__ == '__main__':
    main()
