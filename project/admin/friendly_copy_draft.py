"""Soften repeated scenario copy in the draft without changing legal conditions."""

from collections import Counter
from copy import deepcopy

from project.admin.documents_reference_draft import request
from project.llm.services.scenario import validate


COMMON = {
    'Ссылки:': 'Полезные ссылки:',
    'Вот с чего можно начать:': 'Для начала можно сделать вот что:',
    'Памятка обращена к ребёнку или выпускнику.': 'Этот ответ написан для ребёнка или выпускника.',
    'Памятка для ребёнка или выпускника.': 'Этот ответ написан для ребёнка или выпускника.',
    'Если помогаешь ребёнку или выпускнику, учитывай его обстоятельства.':
        'Если вы помогаете ребёнку или выпускнику, учитывайте его ситуацию.',
    'Памятка для подготовки. Правовые условия конкретного случая здесь не устанавливаются.':
        'Здесь можно подготовиться к следующему шагу. Условия для вашей ситуации нужно уточнить отдельно.',
    'Для этой задачи у меня пока нет проверенного списка документов. Ниже — подготовительные действия, а не требования к заявлению.':
        'Проверенного списка документов для этой задачи у меня пока нет. Ниже — шаги для подготовки; это не требования к заявлению.',
    'Подготовленный план действий.': 'Вот план, с которого можно начать.',
    'ЛЬГОТЫ И ВЫПЛАТЫ': 'Льготы и выплаты',
    'ХОЧУ ПРИНЯТЬ РЕБЁНКА В СЕМЬЮ': 'Хочу принять ребёнка в семью',
    'ГОСУСЛУГИ И ДРУГИЕ ГОСУДАРСТВЕННЫЕ СЕРВИСЫ': 'Госуслуги и другие государственные сервисы',
    'СНИЛС И ИНН': 'СНИЛС и ИНН',
}

CHILD = {
    'Выберите тему.': 'Выбери, о чём хочешь узнать.',
    'Выберите вопрос.': 'Выбери вопрос, который сейчас важен.',
    'Выберите подходящий вопрос или этап.': 'Выбери, что сейчас ближе к твоей ситуации.',
    'Памятка по выбранной ситуации.': 'Давай разберём эту ситуацию по шагам.',
    'Выбери нужный вопрос в памятке.': 'Что хочешь узнать? Выбери вопрос ниже.',
}

ADULT = {
    'Выберите тему.': 'Выберите, о чём хотите узнать.',
    'Выберите вопрос.': 'Выберите вопрос, который сейчас важен.',
    'Выберите подходящий вопрос или этап.': 'Выберите, что сейчас ближе к вашей ситуации.',
    'Памятка по выбранной ситуации.': 'Давайте разберём эту ситуацию по шагам.',
}

ADULT_BRANCHES = {'family', 'parent'}


def configure(config):
    result = deepcopy(config)
    changes = Counter()
    for node in result['nodes']:
        for field in ('text', 'adult_text'):
            value = node.get(field)
            if not value:
                continue
            replacements = COMMON | (ADULT if field == 'adult_text' or node.get('branch') in ADULT_BRANCHES else CHILD)
            for old, new in replacements.items():
                if old in value:
                    changes[old] += value.count(old)
                    value = value.replace(old, new)
            node[field] = value
    return validate(result), changes


def main():
    before = request('state')
    config, changes = configure(before['config'])
    again, extra = configure(config)
    assert again == config and not extra
    saved = request('draft', {'config': config, 'revision': before['revision']}, 'PUT')
    after = request('state')
    assert after['revision'] == saved['revision']
    assert after['published_id'] == before['published_id']
    assert after['config'] == config
    print(f"Draft revision {after['revision']}; {sum(changes.values())} phrase edits; "
          f"published version {after['published_id']} unchanged")


if __name__ == '__main__':
    main()
