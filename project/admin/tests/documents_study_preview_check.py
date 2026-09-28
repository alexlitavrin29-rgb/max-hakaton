"""Check the saved document and study draft through the live preview API."""

from collections import deque
import json

from project.admin.documents_reference_draft import ROOT, request
from project.admin.documents_study_questions_draft import ALIASES, CARD_IDS


def buttons(reply):
    return [button for message in reply['replies']
            for attachment in message.get('attachments', [])
            for row in attachment['payload']['buttons'] for button in row]


def reachable(nodes, start):
    seen = set()
    pending = deque([start])
    while pending:
        current = pending.popleft()
        if current in seen:
            continue
        seen.add(current)
        pending.extend(b['target'] for b in nodes[current].get('buttons', [])
                       if b.get('target') in nodes)
    return seen


def main():
    state = request('state')
    nodes = {node['id']: node for node in state['config']['nodes']}
    accessible = reachable(nodes, 'documents') | reachable(nodes, 'education')
    assert set(CARD_IDS.values()) <= accessible
    assert [b['target'] for b in nodes['documents']['buttons'][:5]] == [
        'identity', 'numbers', 'gosuslugi', 'lost', 'registration']
    assert nodes['documents']['buttons'][5]['target'] == 'exit_docs'

    preview = request('preview', dict(reset=True), 'POST')
    session = preview['session']
    child = next(b for b in buttons(preview) if b['text'] == 'Я ребёнок')
    preview = request('preview', dict(session=session, payload=child['payload']), 'POST')
    assert preview['values']['role'] == 'child'

    def click(label):
        nonlocal preview
        selected = next(b for b in buttons(preview) if b['text'] == label)
        preview = request('preview', dict(session=session, payload=selected['payload']), 'POST')
        return preview['node']

    assert click('Документы') == 'documents'
    assert click('Паспорт и свидетельство о рождении') == 'identity'
    assert click('Паспорт РФ') == 'doc_passport'
    assert click('Первый паспорт в 14 лет') == 'doc_passport_q1'
    preview = request('preview', dict(session=session, text='меню'), 'POST')
    assert click('Учёба') == 'education'
    assert click('Поступление') == 'admission'
    assert click('Колледж после 9 класса') == 'college9'

    checks = []
    for code, key in CARD_IDS.items():
        node = nodes[key]
        preview = request('preview', dict(session=session, payload='jump:' + key), 'POST')
        assert preview['node'] == key, (code, preview['node'])
        assert preview['replies'][0]['text'].startswith(node['text']), code
        assert preview['replies'][0]['format'] == 'markdown', code
        assert [b['text'] for b in buttons(preview)] == [
            'Что ещё может понадобиться', 'Напомнить'], code
        assert 'Для этой задачи у меня пока нет проверенного списка документов' not in node['text']
        assert 'Это не полный список' not in node['text'], code
        assert 'Это не полный список' not in node['adult_text'], code
        assert not any(t['kind'].startswith('llm') or t['kind'].endswith('error')
                       for t in preview['trace']), code
        more = buttons(preview)[0]
        preview = request('preview', dict(session=session, payload=more['payload']), 'POST')
        assert preview['node'] == key + '_extra', code
        checks.append(code)

    for old, canonical in ALIASES.items():
        assert nodes[old]['text'] == nodes[canonical]['text']
        assert [b['label'] for b in nodes[old]['buttons']] == [
            'Что ещё может понадобиться', 'Напомнить']
        preview = request('preview', dict(session=session, payload='jump:' + old), 'POST')
        assert preview['node'] == old
        assert 'Для этой задачи у меня пока нет проверенного списка документов' not in preview['replies'][0]['text']
    for old in ('birth', 'diploma_lost'):
        preview = request('preview', dict(session=session, payload='jump:' + old), 'POST')
        assert preview['node'] == old
        assert 'Для этой задачи у меня пока нет проверенного списка документов' not in preview['replies'][0]['text']

    preview = request('preview', dict(reset=True), 'POST')
    session = preview['session']
    parent = next(b for b in buttons(preview) if b['text'] == 'Я родитель / опекун')
    preview = request('preview', dict(session=session, payload=parent['payload']), 'POST')
    assert preview['node'] == 'pa_home'
    preview = request('preview', dict(session=session, text='меню'), 'POST')
    assert preview['node'] == 'pa_home'

    assert request('state')['published_id'] == state['published_id'] == 1
    report = dict(revision=state['revision'], published_id=state['published_id'],
                  cards=len(checks), old_cards_updated=len(ALIASES) + 2,
                  checks=checks, passed=True)
    output = ROOT / 'docs/test-results/documents-study-questions/preview.json'
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps({k: v for k, v in report.items() if k != 'checks'}, ensure_ascii=False))


if __name__ == '__main__':
    main()
