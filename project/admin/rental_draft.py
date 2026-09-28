"""Enable the city/budget rental pilot only in the editor draft."""
from copy import deepcopy
import json
from project.admin.documents_reference_draft import ROOT, request
from project.llm.services.scenario import validate
from project.llm.services.rental import CONTRACT

TEXT = {
    'city': 'Подскажи, пожалуйста, в каком городе будем искать квартиру?',
    'budget': 'Какую сумму в месяц готов выделить на аренду? Например: «до 30 тысяч». Залог, комиссия и коммунальные платежи могут оплачиваться отдельно.',
    'budget_invalid': 'Подскажи, пожалуйста, максимальную сумму аренды за месяц в рублях. Например: «до 25 тысяч рублей». Если была опечатка, напиши сумму ещё раз.',
    'city_choice': 'Нашлось несколько мест или похожих названий. Выбери нужное ниже. Если его нет, напиши город и регион через запятую.',
    'city_unknown': 'Не получилось однозначно определить место в справочнике объявлений. Напиши, пожалуйста, город и регион через запятую.',
    'other': 'Ты указал дополнительное условие: «{condition}». Пока я проверяю только город и максимальную месячную плату за квартиру. Искать без этого дополнительного условия?',
    'unrecognized': 'Помогу с долгосрочной арендой квартиры. Напиши город и максимальную плату за месяц — вместе или по частям. Уже названные условия сохранены.',
    'parse_error': 'Сейчас не получилось разобрать сообщение. Попробуй отправить его ещё раз — прежние условия поиска сохранились.',
    'api_error': 'Сейчас не удалось получить данные объявлений. Условия и место в выдаче сохранились. Можно попробовать ещё раз.',
    'empty': 'На просмотренной странице подходящих новых вариантов не нашлось. Это не означает, что их нет во всём источнике. Можно продолжить просмотр или выбрать другие условия.',
    'summary': 'Квартиры в долгосрочную аренду: {city}, до {budget} ₽ в месяц. Это плата за аренду; залог, комиссия и коммунальные платежи могут быть отдельно.',
    'card': '{title}\nЦена в объявлении: {price}\n{terms}\n\n{status}',
    'next': 'Объявления могут измениться или стать недоступными. Подробности и дополнительные платежи проверь по ссылке. Что сделаем дальше?',
    'change': 'Что хочешь поменять — город или бюджет? Напиши новые условия. Остальные я сохраню.',
}


def configure(config):
    c = deepcopy(config)
    c['rental'] = dict(enabled=True, transaction='rent_long', property_type='apartment')
    nodes = {n['id']: n for n in c['nodes']}
    for button in nodes[c['menu']]['buttons']:
        if button['label'] == 'Ищу жильё':
            button['target'] = 'rental'
    text = ('Помогу найти квартиру в долгосрочную аренду. Подскажи, пожалуйста, город и максимальную плату за месяц. '
            'Можно написать всё сразу: «Томск, до 30 тысяч», или добавлять условия по частям.\n\n'
            'Пока поиск учитывает только город и бюджет. Залог, комиссия и коммунальные платежи могут быть отдельно.')
    nodes['rental'] = dict(id='rental', branch='rental', title='Поиск жилья', kind='message', text=text,
                          adult_text=text.replace('Подскажи', 'Подскажите'), next='', tasks=[], conditions=[], routes=[], sources=[],
                          buttons=[dict(label='Памятки о жилье', target='housing'), dict(label='На главную', target=c['menu'])])
    if not any(b['id'] == 'rental' for b in c['branches']):
        c['branches'].append(dict(id='rental', title='Поиск жилья', entry='rental', roles=['child','parent','candidate'],
                                 status='draft', prompt=CONTRACT, keywords=[]))
    for key, text in TEXT.items():
        c['ui']['rental_' + key] = dict(text=text, category='message', hidden=False, target='')
    for key, text in dict(more='Показать ещё', change='Изменить условия', miss='Ничего не подошло',
                          remind='Напомнить', search='Повторить', ignore_other='Искать только по городу и бюджету').items():
        c['ui']['button_rental_' + key] = dict(text=text, category='button', hidden=False, target='')
    c['nodes'] = list(nodes.values())
    return validate(c)


def main():
    before = request('state')
    config = configure(before['config'])
    output = ROOT / 'docs/test-results/housing'
    output.mkdir(parents=True, exist_ok=True)
    (output / 'draft-before.json').write_text(json.dumps(before['config'],ensure_ascii=False,indent=2),encoding='utf-8')
    saved = request('draft', dict(config=config, revision=before['revision']), 'PUT')
    after = request('state')
    assert after['config'] == config and after['revision'] == saved['revision']
    assert after['published_id'] == before['published_id']
    result = dict(revision_before=before['revision'], revision=after['revision'], published_id=after['published_id'])
    (output / 'draft-update.json').write_text(json.dumps(result,indent=2),encoding='utf-8')
    print(json.dumps(result))


if __name__ == '__main__':
    main()
