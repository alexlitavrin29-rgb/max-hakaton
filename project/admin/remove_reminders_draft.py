"""Remove reminder entry points and copy from the draft scenario."""

from copy import deepcopy

from project.admin.documents_reference_draft import request
from project.llm.services.scenario import validate


REMINDER_NODES = {'reminders', 'reminder_new', 'reminder_tasks'}
REMINDER_UI = {
    'no_tasks', 'reminders', 'no_reminders', 'notification', 'reminder_row',
    'button_snooze', 'reminder_city', 'reminder_date', 'reminder_text',
    'reminder_saved', 'reminder_tasks', 'reminder_confirm', 'reminder_deleted',
    'reminder_invalid', 'work_remind_offer', 'reminder_cancelled',
    'button_new_reminder', 'button_reminder_list', 'button_rental_remind',
}


def configure(config):
    result = deepcopy(config)
    result['reminders_enabled'] = False
    result['branches'] = [branch for branch in result['branches'] if branch['id'] != 'reminders']
    for branch in result['branches']:
        if branch['id'] == 'rental':
            branch['prompt'] = branch['prompt'].replace('search, more, change, remind или null',
                                                       'search, more, change или null')
    result['nodes'] = [node for node in result['nodes'] if node['id'] not in REMINDER_NODES]
    for node in result['nodes']:
        node['buttons'] = [button for button in node.get('buttons', [])
                           if button.get('target') not in REMINDER_NODES
                           and 'напом' not in button['label'].lower()]
        for field in ('text', 'adult_text'):
            if field not in node:
                continue
            value = node.get(field, '')
            if node['id'] == 'emergency_home':
                value = value.replace('Не откладывайте срочные действия до напоминания. ', '')
            if node['id'] == 'threat':
                value = value.replace('напоминание не заменяет звонок.',
                                      'при опасности позвони по номеру 112.')
            node[field] = value
    result['bindings'] = {key: value for key, value in result.get('bindings', {}).items()
                          if value not in REMINDER_NODES}
    result['ui'] = {key: value for key, value in result['ui'].items() if key not in REMINDER_UI}
    result['ui']['work_controls']['text'] = (
        'Как продолжим? Можно посмотреть ещё вакансии или изменить условия поиска.')
    for material in result['materials']:
        if material['id'] == 'prepared_emergency_home':
            material['text'] = material['text'].replace('Не откладывайте срочные действия до напоминания. ', '')
        if material['id'] == 'prepared_threat':
            material['text'] = material['text'].replace('напоминание не заменяет звонок.',
                                                       'при опасности позвони по номеру 112.')
    return validate(result)


def main():
    before = request('state')
    config = configure(before['config'])
    assert configure(config) == config
    assert not any('напом' in node.get(field, '').lower() for node in config['nodes']
                   for field in ('text', 'adult_text'))
    assert not any(button.get('target') in REMINDER_NODES for node in config['nodes']
                   for button in node.get('buttons', []))
    saved = request('draft', {'config': config, 'revision': before['revision']}, 'PUT')
    after = request('state')
    assert after['revision'] == saved['revision']
    assert after['published_id'] == before['published_id']
    assert after['config'] == config
    print(f"Draft revision {after['revision']}; published version {after['published_id']} unchanged")


if __name__ == '__main__':
    main()
