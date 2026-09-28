"""Move the existing child help entry to fourth place in the draft menu."""

from copy import deepcopy

from project.admin.documents_reference_draft import request
from project.llm.services.scenario import validate


def configure(config):
    result = deepcopy(config)
    main = next(node for node in result['nodes'] if node['id'] == result['menu'])
    buttons = main['buttons']
    help_button = next(button for button in buttons if button.get('target') == 'help_points')
    buttons.remove(help_button)
    buttons.insert(3, help_button)
    return validate(result)


def main():
    before = request('state')
    config = configure(before['config'])
    assert configure(config) == config
    assert [button.get('target') for button in next(node for node in config['nodes']
            if node['id'] == config['menu'])['buttons'][:4]] == [
                'work_entry', 'housing', 'mentor', 'help_points']
    saved = request('draft', {'config': config, 'revision': before['revision']}, 'PUT')
    after = request('state')
    assert after['revision'] == saved['revision']
    assert after['published_id'] == before['published_id']
    assert after['config'] == config
    print(f"Draft revision {after['revision']}; published version {after['published_id']} unchanged")


if __name__ == '__main__':
    main()
