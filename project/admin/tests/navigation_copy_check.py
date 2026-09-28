"""Check the current draft with the same flow engine used by MAX."""

import asyncio
import re

from project.admin.documents_reference_draft import request
from project.admin.navigation_copy_draft import NAVIGATION, UPWARD, MORE, configure
from project.llm.services.flow import FlowDialogue, PreviewReminders


def buttons(replies):
    return [button for reply in replies for attachment in reply.get('attachments', [])
            for row in attachment['payload']['buttons'] for button in row]


async def check_path(bot, labels, expected_nodes):
    replies = await bot.handle(1, payload='reset')
    for label, node in zip(labels, expected_nodes):
        selected = next(button for button in buttons(replies) if button['text'] == label)
        replies = await bot.handle(1, payload=selected['payload'])
        assert bot.session(1).node == node, (label, bot.session(1).node)
    return replies


async def main():
    state = request('state')
    config = state['config']
    assert state['published_id'] == 1
    assert config['rules']['navigation_back']
    assert configure(config)[0] == config
    nodes = {node['id']: node for node in config['nodes']}
    for node in config['nodes']:
        labels = [button['label'] for button in node['buttons']]
        assert not any(label in NAVIGATION | UPWARD | {MORE} for label in labels if label != 'Назад'), node['id']
        assert not any(button.get('url') for button in node['buttons']), node['id']
        if node['id'] != config['start']:
            assert labels.count('Назад') == 1, node['id']
        assert not any(re.search(r'[\U0001F000-\U0001FAFF]', value)
                       for value in [node.get('text', ''), node.get('title', '')] + labels), node['id']

    assert 'Если в записи о рождении ошибка' in nodes['doc_birth_q3']['text']
    assert '90 дней' in nodes['doc_passport_q1']['text']
    assert '1. Обратись' in nodes['doc_lost_police']['text']
    assert '2. Заполни' in nodes['doc_lost_passport_q1']['text']
    assert nodes['doc_lost_photos']['text'].startswith('НЕ ХРАНИ')
    assert nodes['doc_lost_fraud']['text'].startswith('БУДЬ ВНИМАТЕЛЕН')
    assert '[Заявление на жилищный сертификат](https://' in nodes['bq_certificate_steps']['text']
    assert nodes['bq_certificate_steps']['format'] == 'markdown'

    bot = FlowDialogue(PreviewReminders(), config, state['revision'])
    replies = await check_path(bot,
        ['Я ребёнок', 'Документы', 'Паспорт и свидетельство о рождении',
         'Паспорт РФ', 'Первый паспорт в 14 лет'],
        ['menu', 'documents', 'identity', 'doc_passport', 'doc_passport_q1'])
    assert replies[0]['format'] == 'markdown'
    for expected in ['doc_passport', 'identity', 'documents', 'menu']:
        back = next(button for button in buttons(replies) if button['text'] == 'Назад')
        replies = await bot.handle(1, payload=back['payload'])
        assert bot.session(1).node == expected, (expected, bot.session(1).node)

    replies = await check_path(bot, ['Я родитель / опекун', 'Документы ребёнка'],
                               ['pa_home', 'pa_group_documents'])
    back = next(button for button in buttons(replies) if button['text'] == 'Назад')
    await bot.handle(1, payload=back['payload'])
    assert bot.session(1).node == 'pa_home'

    replies = await check_path(bot, ['Хочу принять ребёнка в семью',
                                     'Понять, какой вариант мне подходит'],
                               ['family', 'fq_section_forms'])
    back = next(button for button in buttons(replies) if button['text'] == 'Назад')
    await bot.handle(1, payload=back['payload'])
    assert bot.session(1).node == 'family'

    parent = FlowDialogue(PreviewReminders(), config, state['revision'])
    replies = await check_path(parent, ['Я родитель / опекун', 'Документы ребёнка'],
                               ['pa_home', 'pa_group_documents'])
    replies = await parent.handle(1, payload='jump:pa_report_where')
    assert not any('напом' in button['text'].lower() for button in buttons(replies))
    await parent.handle(1, payload='newrem')
    assert parent.session(1).pending is None
    assert not config['reminders_enabled']
    assert all(branch['id'] != 'reminders' for branch in config['branches'])
    assert all(button.get('target') not in {'reminder_new', 'reminders', 'reminder_tasks'}
               for node in config['nodes'] for button in node['buttons'])
    menu = nodes[config['menu']]
    assert [button['target'] for button in menu['buttons'][:4]] == [
        'work_entry', 'housing', 'mentor', 'help_points']

    print(f"draft {state['revision']}: navigation, content and links checked")


if __name__ == '__main__': asyncio.run(main())
