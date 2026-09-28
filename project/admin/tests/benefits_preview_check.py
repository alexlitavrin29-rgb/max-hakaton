"""Synthetic conversations through the admin/MAX shared scenario engine."""
import json

from project.admin.benefits_questions_draft import SOURCES
from project.admin.documents_reference_draft import ROOT, request


def buttons(reply):
    return [b for r in reply['replies'] for a in r.get('attachments', [])
            for row in a['payload']['buttons'] for b in row]


def main():
    state = request('state')
    nodes = {n['id']: n for n in state['config']['nodes']}
    checks = []

    def chat(session='', payload=None, reset=False, text=None):
        result = request('preview', dict(session=session, payload=payload, reset=reset, text=text or ''))
        assert result['sandbox']
        assert not any(t['kind'].startswith('llm') or t['kind'].endswith('error') for t in result['trace']), result['trace']
        return result

    def press(reply, label):
        return chat(reply['session'], next(b['payload'] for b in buttons(reply) if b['text'] == label))

    for role, label in [('child', 'Я ребёнок'), ('parent', 'Я родитель / опекун'),
                        ('candidate', 'Хочу принять ребёнка в семью')]:
        r = press(chat(reset=True), label)
        sid = r['session']
        r = chat(sid, 'jump:' + state['config']['menu'])
        assert [b['text'] for b in buttons(r)][:3] == ['Ищу работу', 'Ищу жильё', 'Ищу наставника']
        r = press(r, 'Льготы и выплаты')
        assert r['node'] == 'benefits'
        assert len([b for b in buttons(r) if 'payload' in b]) == 7
        checks.append(dict(role=role, check='main_entry', passed=True))

        # Follow every actual child button recursively, including back navigation.
        def walk(key):
            parent = nodes[key]
            for control in parent['buttons']:
                child = control.get('target', '')
                if not child.startswith('bq_') or control['label'] == 'Назад':
                    continue
                reply = chat(sid, 'jump:' + key)
                reply = press(reply, control['label'])
                assert reply['node'] == child, (key, child, reply['node'])
                content = '\n'.join(m['text'] for m in reply['replies'])
                assert nodes[child]['text'] in content
                assert ('Если помогаешь ребёнку' in content) == (role != 'child')
                assert [b['url'] for b in buttons(reply) if 'url' in b] == [
                    b['url'] for b in nodes[child]['buttons'] if 'url' in b]
                back = press(reply, 'Назад')
                assert back['node'] == key
                checks.append(dict(role=role, node=child, check='forward_text_links_back', passed=True))
                walk(child)
        walk('benefits')

        for key, node in nodes.items():
            if not key.startswith('bq_') or not node.get('tasks'):
                continue
            r = press(chat(sid, 'jump:' + key), 'Отметить выполненное')
            first = next(b for b in buttons(r) if b.get('payload', '').startswith('done:'))
            r = chat(sid, first['payload'])
            assert '☑' in '\n'.join(m['text'] for m in r['replies'])
            checks.append(dict(role=role, node=key, check='checklist_toggle', passed=True))

        r = press(chat(sid, 'jump:bq_quota_steps'), 'Напомнить')
        assert any('город' in m['text'].lower() for m in r['replies'])
        checks.append(dict(role=role, check='sandbox_reminder_city', passed=True))
        r = chat(sid, 'jump:benefits')
        r = chat(sid, text='Какая региональная выплата мне точно положена?')
        assert not any('llm' in t['kind'] for t in r['trace'])
        checks.append(dict(role=role, check='unknown_question_no_llm', passed=True))

        r = chat(sid, 'jump:work_entry')
        r = press(r, 'Поиск работы')
        assert r['node'] == 'work'
        r = chat(sid, 'jump:housing')
        assert any(b['text'] == 'Поиск жилья' for b in buttons(r))
        checks.append(dict(role=role, check='work_housing_preserved', passed=True))

    allowed = {url for _, url in SOURCES.values()}
    actual = {b['url'] for n in nodes.values() if n['id'].startswith('bq_')
              for b in n['buttons'] if 'url' in b}
    assert actual == allowed
    assert 'https://www.gosuslugi.ru/' not in actual
    after = request('state')
    assert after['revision'] == state['revision'] and after['published_id'] == state['published_id']
    result = dict(revision=state['revision'], published_id=state['published_id'], checks=checks,
                  external_links=len(actual), link_scope='Targets only; no authenticated government forms submitted.')
    out = ROOT / 'docs/test-results/benefits-questions/preview.json'
    out.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps(dict(passed=len(checks), revision=state['revision'], published_id=state['published_id'])))


if __name__ == '__main__':
    main()
