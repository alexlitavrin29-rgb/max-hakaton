"""Use Back in menus and role contents on final answers. Draft only."""

from copy import deepcopy
import json

from project.admin.documents_reference_draft import ROOT, request
from project.llm.services.scenario import validate


def configure(original):
    config = deepcopy(original)
    final = []
    for node in config['nodes']:
        navigation = [b for b in node.get('buttons', [])
                      if b.get('values', {}).get('navigation') in {'back', 'contents'}]
        forward = [b for b in node.get('buttons', []) if b not in navigation and b.get('target')]
        # Search results have action buttons but are a final result, too.
        terminal = node.get('action') in {'search', 'search_more', 'checklist'} or (
            node['kind'] in {'message', 'answer'} and not forward and not node.get('next')
            and node['id'] not in {config['start'], 'hp_city', 'rental'})
        if terminal:
            for button in navigation:
                button.update(label='К оглавлению', target=config['menu'])
                button['values']['navigation'] = 'contents'
            final.append(node['id'])
    config['rules']['navigation_contents'] = True
    validate(config)
    return config, final


def main():
    before = request('state')
    config, final = configure(before['config'])
    output = ROOT / 'docs/test-results/contents-navigation'
    output.mkdir(parents=True, exist_ok=True)
    (output / 'before.json').write_text(json.dumps(before, ensure_ascii=False, indent=2), encoding='utf-8')
    saved = request('draft', dict(config=config, revision=before['revision']), 'PUT')
    after = request('state')
    assert after['revision'] == saved['revision'] and after['config'] == config
    assert after['published_id'] == before['published_id']
    result = dict(revision=after['revision'], published_id=after['published_id'], final_nodes=final)
    (output / 'update.json').write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
    print(f"Draft {after['revision']}: {len(final)} final nodes; published version unchanged.")


if __name__ == '__main__':
    main()
