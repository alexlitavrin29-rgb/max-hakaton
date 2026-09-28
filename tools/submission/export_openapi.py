"""Export the actual public routes and document runtime authentication/responses."""
import json
from pathlib import Path

from project.miniapp.app import app


def contract():
    document = app.openapi()
    document['info'] = {
        'title': 'Точка опоры — API мини-приложения и каталога помощи',
        'version': 'submission-2026-09-28',
        'description': 'Публичный каталог помощи и отдельные сеансы MAX. '
        'Редактор не входит в публичный API. Тексты и доступность услуг требуют '
        'уточнения у организации. Источники вакансий и жилья могут быть недоступны.',
    }
    document['servers'] = [{'url': 'https://135-106-229-210.sslip.io'}]
    document['paths'].pop('/', None)
    document['components']['securitySchemes'] = {
        'MaxSession': {'type': 'http', 'scheme': 'bearer', 'description':
            'Токен из POST /api/session с подписанным MAX init_data. '
            'До 1 часа бездействия; исчезает при перезапуске. Пароль MAX и токен бота не передаются.'}}
    schemas = document['components']['schemas']
    obj = lambda required, properties: dict(type='object', required=required, properties=properties)
    string = {'type': 'string'}
    array = lambda items: dict(type='array', items=items)
    schemas['Error'] = obj(['detail'], {'detail': {}})
    schemas['Place'] = obj(['name', 'code'], {'name': string, 'code': string, 'kind': string})
    ref = lambda name: {'$ref': '#/components/schemas/' + name}
    schemas['HelpPoint'] = obj(['id', 'name', 'city', 'categories', 'availability'], {
        'id': string, 'name': string, 'city': string, 'address': string,
        'region_code': string, 'categories': array(string), 'target_group': string,
        'conditions': string, 'cost': string, 'source_url': string,
        'availability': {'type': 'string', 'enum': ['contact_required']}, 'cost_status': string})
    schemas['Card'] = obj(['text'], {'text': string, 'title': string, 'section': string,
        'favorite_id': string, 'node_id': string, 'actions': array({'type': 'object'}),
        'saved_at': string, 'unavailable': {'type': 'boolean'}})
    schemas['View'] = obj(['section', 'cards', 'notices', 'controls', 'summary'], {
        'section': string, 'summary': string, 'cards': array(ref('Card')),
        'notices': array({'type': 'object'}), 'controls': array({'type': 'object'})})
    responses = {
        ('/api/health', 'get'): obj(['status'], {'status': {'const': 'ok'}}),
        ('/api/info', 'get'): obj(['preview', 'bot_name'], {'preview': {'type': 'boolean'}, 'bot_name': string}),
        ('/api/session', 'post'): obj(['session'], {'session': string}),
        ('/api/action', 'post'): ref('View'),
        ('/api/favorites', 'get'): obj(['cards'], {'cards': array(ref('Card'))}),
        ('/api/favorites', 'post'): obj(['cards'], {'cards': array(ref('Card'))}),
        ('/api/material', 'post'): obj(['card', 'saved'], {'card': ref('Card'), 'saved': {'type': 'boolean'}}),
        ('/api/help/search', 'get'): obj(['choices', 'total'], {'choices': array(ref('Place')), 'total': {'type': 'integer'}}),
        ('/api/help/summary', 'get'): obj(['place', 'city_total', 'region_total', 'categories'], {
            'place': ref('Place'), 'city_total': {'type': 'integer'}, 'region_total': {'type': 'integer'},
            'categories': array({'type': 'object'})}),
        ('/api/help/points', 'get'): obj(['place', 'scope', 'total', 'offset', 'items', 'next_offset'], {
            'place': ref('Place'), 'scope': string, 'total': {'type': 'integer'}, 'offset': {'type': 'integer'},
            'items': array(ref('HelpPoint')), 'next_offset': {'type': ['integer', 'null']}}),
        ('/api/help/points/{identifier}', 'get'): ref('HelpPoint'),
    }
    errors = {
        '/api/session': {401: 'Неверные или просроченные init_data', 429: 'Повторный запуск раньше 5 секунд', 503: 'Лимит 200 сеансов'},
        '/api/action': {400: 'Нужно ровно одно действие и выбранный раздел', 401: 'Сеанс завершён', 409: 'Устаревшая кнопка или тот же Idempotency-Key с другим действием', 429: 'Действие уже идёт или интервал меньше 0,4 с', 503: 'Очередь, тайм-аут или ошибка обработки'},
        '/api/favorites': {401: 'Сеанс завершён', 404: 'Материал снят', 409: 'Карточка не открывалась в этом сеансе'},
        '/api/material': {401: 'Сеанс завершён', 404: 'Материал недоступен'},
        '/api/help/summary': {404: 'Место не найдено', 409: 'Нужны город и регион'},
        '/api/help/points': {404: 'Место не найдено', 409: 'Нужно уточнение', 422: 'Недопустимый параметр'},
        '/api/help/points/{identifier}': {404: 'Карточка не найдена'},
    }
    for (path, method), schema in responses.items():
        operation = document['paths'][path][method]
        operation['responses']['200']['content'] = {'application/json': {'schema': schema}}
        operation['security'] = [{'MaxSession': []}] if path in ('/api/action', '/api/favorites', '/api/material') else []
        for code, description in errors.get(path, {}).items():
            if path == '/api/favorites' and method == 'get' and code != 401:
                continue
            operation['responses'][str(code)] = {'description': description,
                'content': {'application/json': {'schema': ref('Error')}}}
        if method == 'post':
            for code, description in ((403, 'Чужой Origin'), (413, 'Тело больше 24000 байт')):
                operation['responses'][str(code)] = {'description': description,
                    'content': {'application/json': {'schema': ref('Error')}}}
    document['paths']['/api/action']['post']['description'] = (
        'Ровно одно: section, непустой text или payload из последнего ответа. '
        'Минимум 0,4 с между вызовами. Очередь до 5 с, обработка до 120 с. '
        'Ошибка внешнего источника может возвращаться как уведомление внутри HTTP 200.')
    document['paths']['/api/material']['post']['description'] = (
        'save=false только открывает ответ. save=true сохраняет в Моё. '
        'Разрешены только материалы явного реестра saved_answers.json.')
    return document


if __name__ == '__main__':
    # JSON is also valid YAML 1.2 and avoids a YAML dependency in the app image.
    Path('openapi.yaml').write_text(json.dumps(contract(), ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
