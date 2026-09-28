"""Give short document menus a useful, friendly introduction in the draft."""

from copy import deepcopy

from project.admin.documents_reference_draft import request
from project.llm.services.scenario import validate


COPY = {
    'doc_services': (
        'Другие полезные сервисы\n\nЗдесь есть несколько сервисов для разных задач. Выбери тот, о котором хочешь узнать, — расскажу, для чего он нужен.',
        'Другие полезные сервисы\n\nЗдесь есть несколько сервисов для разных задач. Выберите тот, о котором хотите узнать.'),
    'doc_lost_others': (
        'Восстановление других документов\n\nЕсли потерялся не паспорт, выбери документ ниже. Подскажу, с чего начать восстановление.',
        'Восстановление других документов\n\nЕсли потерялся не паспорт, выберите документ ниже. Здесь собраны первые шаги для его восстановления.'),
}


def configure(config):
    result = deepcopy(config)
    nodes = {node['id']: node for node in result['nodes']}
    for node_id, (child, adult) in COPY.items():
        nodes[node_id]['text'] = child
        nodes[node_id]['adult_text'] = adult
    node = nodes['tax_notice']
    node['text'] = node['text'].replace('Если пришло налоговое уведомление, начни с ',
                                      'Если пришло налоговое уведомление, начните с ')
    node['text'] = node['text'].replace('Я не вижу твоё начисление.',
                                      'Я не вижу ваше начисление.')
    return validate(result)


def main():
    before = request('state')
    config = configure(before['config'])
    assert configure(config) == config
    saved = request('draft', {'config': config, 'revision': before['revision']}, 'PUT')
    after = request('state')
    assert after['revision'] == saved['revision'] and after['published_id'] == before['published_id']
    assert after['config'] == config
    print(f"Draft revision {after['revision']}; published version {after['published_id']} unchanged")


if __name__ == '__main__':
    main()
