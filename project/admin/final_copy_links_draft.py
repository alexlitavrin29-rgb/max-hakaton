"""Polish remaining raw links and repetitive status wording in the draft."""

from copy import deepcopy

from project.admin.documents_reference_draft import request
from project.llm.services.scenario import validate


def configure(config):
    result = deepcopy(config)
    nodes = {node['id']: node for node in result['nodes']}
    for node_id, old, new in (
        ('doc_foreign_q2', 'на https://www.gosuslugi.ru/ .',
         'на [Госуслугах](https://www.gosuslugi.ru/).'),
        ('gosuslugi_q2', 'сайт https://www.gosuslugi.ru/',
         '[сайт Госуслуг](https://www.gosuslugi.ru/)'),
        ('hq_utilities_tariffs', 'в ГИС ЖКХ: https://dom.gosuslugi.ru/',
         'в [ГИС ЖКХ](https://dom.gosuslugi.ru/)'),
    ):
        node = nodes[node_id]
        for field in ('text', 'adult_text'):
            assert old in node[field], (node_id, field)
            node[field] = node[field].replace(old, new)
        node['format'] = 'markdown'
    node = nodes['bq_unemployment_who']
    node['text'] = node['text'].replace(
        'наличие статуса сироты ещё не означает назначения пособия.',
        'одного статуса для назначения пособия недостаточно.')
    node['adult_text'] = node['adult_text'].replace(
        'наличие статуса сироты ещё не означает назначения пособия.',
        'одного статуса для назначения пособия недостаточно.')
    node = nodes['social_scholarship']
    node['text'] = node['text'].replace(' Если это про тебя, найди', ' Найди')
    node['adult_text'] = node['adult_text'].replace(' Если это относится к ребёнку, найдите', ' Найдите')
    return validate(result)


def main():
    before = request('state')
    config = configure(before['config'])
    saved = request('draft', {'config': config, 'revision': before['revision']}, 'PUT')
    after = request('state')
    assert after['revision'] == saved['revision'] and after['published_id'] == before['published_id']
    assert after['config'] == config
    print(f"Draft revision {after['revision']}; published version {after['published_id']} unchanged")


if __name__ == '__main__':
    main()
