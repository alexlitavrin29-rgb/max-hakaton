"""Read-only checks of the current draft and local preview; no MAX messages."""

import asyncio
import json

from project.admin.documents_reference_draft import ROOT, request
from project.admin.contents_navigation_draft import configure
from project.admin.tests.test_contents_navigation import buttons
from project.llm.services.flow import FlowDialogue, PreviewReminders


async def main():
    state = request('state')
    config = state['config']
    assert configure(config)[0] == config
    bot = FlowDialogue(PreviewReminders(), config, state['revision'])
    checked = 0
    for node in config['nodes']:
        navigation = [b for b in node['buttons'] if b.get('values', {}).get('navigation') in {'back', 'contents'}]
        assert len(navigation) == (0 if node['id'] == config['start'] else 1), node['id']
        if not navigation or navigation[0]['values']['navigation'] != 'contents' or node['kind'] != 'message':
            continue
        for role in bot.branches[node['branch']]['roles']:
            s = bot.session(1)
            s.values = {'role': role}
            s.history = [config['start'], 'documents', 'identity']
            replies = await bot.enter(1, s, node['id'])
            if s.node != node['id']:
                continue  # A role/condition route selected another screen.
            nav = [b for b in buttons(replies) if b['text'] in {'Назад', 'К оглавлению'}]
            assert len(nav) == 1 and nav[0]['text'] == 'К оглавлению', node['id']
            replies = await bot.handle(1, payload=nav[0]['payload'])
            assert s.node == {'child': 'menu', 'parent': 'pa_home', 'candidate': 'family'}[role], (node['id'], role, s.node)
            back = next(b for b in buttons(replies) if b['text'] == 'Назад')
            await bot.handle(1, payload=back['payload'])
            assert s.node == config['start']
            checked += 1

    # Exercise the rebuilt admin service, including role redirects and history reset.
    for role, home, leaf in [('child', 'menu', 'doc_passport_q1'),
                             ('parent', 'pa_home', 'pa_birth_first'),
                             ('candidate', 'family', 'fq_adoption_meaning')]:
        response = request('preview', {'reset': True}, 'POST')
        session = response['session']
        start = bot.nodes[config['start']]
        index = next(i for i, b in enumerate(start['buttons']) if b.get('values', {}).get('role') == role)
        response = request('preview', dict(session=session, payload=f"g:{state['revision']}:{start['id']}:{index}"), 'POST')
        assert response['node'] == home
        response = request('preview', dict(session=session, payload='jump:' + leaf), 'POST')
        nav = next(b for b in buttons(response['replies']) if b['text'] == 'К оглавлению')
        response = request('preview', dict(session=session, payload=nav['payload']), 'POST')
        assert response['node'] == home
        nav = next(b for b in buttons(response['replies']) if b['text'] == 'Назад')
        response = request('preview', dict(session=session, payload=nav['payload']), 'POST')
        assert response['node'] == config['start']

    result = dict(revision=state['revision'], published_id=state['published_id'],
                  static_role_paths=checked, preview_role_paths=3)
    output = ROOT / 'docs/test-results/contents-navigation/checks.json'
    output.write_text(json.dumps(result, indent=2), encoding='utf-8')
    print(json.dumps(result))


if __name__ == '__main__':
    asyncio.run(main())
