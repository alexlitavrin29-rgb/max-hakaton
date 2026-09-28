"""Prepare bot entry buttons after a public mini-app URL has been connected in MAX.

configure() is pure. This module never saves or publishes the scenario by itself.
"""
from copy import deepcopy
import re
from project.llm.services.scenario import validate


def configure(config, bot_name='t143_hakaton_max_bot'):
    if not re.fullmatch(r'[A-Za-z0-9_]+', bot_name):
        raise ValueError('Invalid MAX bot name')
    result = deepcopy(config)
    result.setdefault('rules', {})['miniapp_search_bot'] = bot_name
    result['rules']['miniapp_answers_bot'] = bot_name
    nodes = {node['id']: node for node in result['nodes']}
    for button in nodes[result['menu']].get('buttons', []):
        if button.get('target') == 'work' and button['label'] == 'Ищу работу' and 'work_entry' in nodes:
            button['target'] = 'work_entry'
        elif button.get('target') == 'rental' and button['label'] == 'Ищу жильё' and 'housing' in nodes:
            button['target'] = 'housing'
    targets = {result['start']: 'home', 'work': 'work', 'rental': 'rental', 'help_points': 'help'}
    for node_id, section in targets.items():
        node = nodes[node_id]
        url = f'https://max.ru/{bot_name}?startapp={section}'
        if any(button.get('url') == url for button in node.get('buttons', [])):
            continue
        button = dict(label='Мини-приложение' if section == 'home' else 'Открыть мини-приложение', url=url)
        if section == 'home':
            node['buttons'].append(button)
        else:
            node['buttons'].insert(0, button)
    return validate(result)
