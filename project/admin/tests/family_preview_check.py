"""Synthetic parent routes through the same scenario engine as MAX."""
import json

from project.admin.documents_reference_draft import ROOT, request
from project.admin.family_questions_draft import SOURCES, STAGES
from project.admin.tests.benefits_preview_check import buttons


def main():
    state = request('state')
    nodes = {n['id']: n for n in state['config']['nodes']}
    family = {k: n for k, n in nodes.items() if k == 'family' or k.startswith('fq_')}
    checks = []

    def chat(session='', payload=None, reset=False, text=''):
        r = request('preview', dict(session=session, payload=payload, reset=reset, text=text))
        assert r['sandbox']
        assert not any(t['kind'].startswith('llm') or t['kind'].endswith('error') for t in r['trace']), r['trace']
        return r

    def press(r, label):
        return chat(r['session'], next(b['payload'] for b in buttons(r) if b['text'] == label))

    for role, label in [('parent', 'Я родитель / опекун'), ('candidate', 'Хочу принять ребёнка в семью')]:
        r = press(chat(reset=True), label)
        sid = r['session']
        if role == 'candidate':
            assert r['node'] == 'family'
        else:
            r = press(r, 'Семья')
            assert r['node'] == 'family'
        assert len([b for b in buttons(r) if b.get('payload')]) == 7
        checks.append(dict(role=role, check='six_entries', passed=True))

        for key, node in family.items():
            parent = node.get('editorial_parent')
            if not parent:
                continue
            control = next(b for b in nodes[parent]['buttons'] if b.get('target') == key)
            r = press(chat(sid, 'jump:' + parent), control['label'])
            assert r['node'] == key, (key, r['node'])
            assert node['text'] in '\n'.join(m['text'] for m in r['replies'])
            assert [b['url'] for b in buttons(r) if 'url' in b] == [b['url'] for b in node['buttons'] if 'url' in b]
            assert press(r, 'Назад')['node'] == parent
            checks.append(dict(role=role, node=key, check='forward_text_links_back', passed=True))
            if node['tasks']:
                r = press(chat(sid, 'jump:' + key), 'Отметить выполненное')
                toggle = next(b['payload'] for b in buttons(r) if b.get('payload', '').startswith('done:'))
                r = chat(sid, toggle)
                assert '☑' in '\n'.join(m['text'] for m in r['replies'])
                checks.append(dict(role=role, node=key, check='checklist_toggle', passed=True))

        for label, target in STAGES:
            r = press(chat(sid, 'jump:fq_section_next'), label)
            assert r['node'] == target
            checks.append(dict(role=role, check='stage', target=target, passed=True))
        for key in ['forms', 'forms_extra', 'docs_family', 'family_steps', 'adoption', 'adoption_extra',
                    'guardianship', 'guardianship_extra', 'foster', 'foster_extra',
                    'child_documents', 'child_documents_extra', 'children']:
            r = chat(sid, 'jump:' + key)
            assert nodes[key]['text'] in '\n'.join(m['text'] for m in r['replies'])
            checks.append(dict(role=role, check='legacy_entry', node=key, passed=True))
        r = press(chat(sid, 'jump:fq_medical_steps'), 'Напомнить')
        assert any('город' in m['text'].lower() for m in r['replies'])
        checks.append(dict(role=role, check='sandbox_reminder', passed=True))
        r = chat(sid, 'jump:family')
        chat(sid, text='Найдите моё дело в опеке и скажите, можно ли мне усыновить ребёнка')
        checks.append(dict(role=role, check='unknown_no_llm', passed=True))

    r = press(chat(reset=True), 'Я ребёнок')
    sid = r['session']
    assert not any(b['text'] == 'Семья' for b in buttons(r))
    for key, node in nodes.items():
        if node['branch'] != 'family':
            continue
        r = chat(sid, 'jump:' + key)
        assert r['node'] != key, key
        checks.append(dict(role='child', check='family_denied', node=key, passed=True))

    before = json.loads((ROOT / 'docs/test-results/family-questions/before.json').read_text(encoding='utf-8'))
    touched = set(family) | {'forms', 'forms_extra', 'docs_family', 'family_steps', 'adoption', 'adoption_extra',
                           'guardianship', 'guardianship_extra', 'foster', 'foster_extra',
                           'child_documents', 'child_documents_extra', 'children'}
    assert all(n == nodes[n['id']] for n in before['nodes'] if n['id'] not in touched)
    actual = {b['url'] for n in family.values() for b in n['buttons'] if 'url' in b}
    assert actual == {url for _, url in SOURCES.values()}
    assert state['config']['search'] == before['search']
    checks.append(dict(check='unrelated_nodes_and_work_settings_preserved', passed=True))
    after = request('state')
    assert (after['revision'], after['published_id']) == (state['revision'], state['published_id'])
    result = dict(revision=state['revision'], published_id=state['published_id'], checks=checks,
                  external_links=len(actual), limitation='No authenticated government forms inspected or submitted.')
    (ROOT / 'docs/test-results/family-questions/preview.json').write_text(
        json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps(dict(passed=len(checks), revision=state['revision'], published_id=state['published_id'])))


if __name__ == '__main__':
    main()
