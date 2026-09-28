"""Three starting choices and user-supplied mentoring links. Never publishes."""
from copy import deepcopy
import json

from project.admin.documents_reference_draft import ROOT, request
from project.llm.services.scenario import validate


def configure(config):
    c = deepcopy(config)
    nodes = {n['id']: n for n in c['nodes']}
    menu = nodes[c['menu']]
    menu['buttons'] = [dict(label='Ищу работу', target='work'),
                       dict(label='Ищу жильё', target='housing'),
                       dict(label='Ищу наставника', target='mentor')] + [
        b for b in menu['buttons'] if b.get('target') not in {'work', 'housing', 'mentor'}
    ]
    menu['text'] = 'С чем тебе помочь? Выбери подходящую тему.'
    menu['adult_text'] = 'С чем вам помочь? Выберите подходящую тему.'
    if not any(b['id'] == 'mentor' for b in c['branches']):
        c['branches'].append(dict(id='mentor', title='Наставник', entry='mentor',
                                 roles=['child', 'parent', 'candidate'], status='draft', prompt='', keywords=[]))
    nodes['mentor'] = dict(
        id='mentor', branch='mentor', title='Ищу наставника', kind='message',
        text='Ищешь наставника?\n\nВот две организации, о которых можно узнать подробнее. '
             'Открой сайт — там можно посмотреть информацию и способы обращения.\n\n'
             '• БФ «Жёлтый Аист» — программа «Наставничество».\n'
             '• Наставнический центр Александра Гезалова — запись на консультацию.\n\n'
             'Выбери, какую страницу открыть.',
        adult_text='Ищете наставника для ребёнка?\n\nВот две организации, о которых можно узнать подробнее. '
                   'Откройте сайт — там можно посмотреть информацию и способы обращения.\n\n'
                   '• БФ «Жёлтый Аист» — программа «Наставничество».\n'
                   '• Наставнический центр Александра Гезалова — запись на консультацию.\n\n'
                   'Выберите, какую страницу открыть.',
        buttons=[dict(label='БФ «Жёлтый Аист»', url='https://krasbezsirot.ru/needhelp'),
                 dict(label='Центр Александра Гезалова', url='https://nastavnik-gezalov.ru/zapis-na-konsultaciu/'),
                 dict(label='На главную', target=c['menu'])],
        next='', tasks=[], sources=[], routes=[], conditions=[],
        editorial_note='Ссылки предоставлены владельцем. Автоматическое чтение страниц не удалось; '
                       'условия участия, география и доступность записи не проверены. Заявки бот не отправляет.',
    )
    c['nodes'] = list(nodes.values())
    return validate(c)


def main():
    before = request('state')
    config = configure(before['config'])
    assert configure(config) == config
    out = ROOT / 'docs/test-results/mentor-entry'
    out.mkdir(parents=True, exist_ok=True)
    (out / 'before.json').write_text(json.dumps(before['config'], ensure_ascii=False, indent=2), encoding='utf-8')
    saved = request('draft', dict(config=config, revision=before['revision']), 'PUT')
    after = request('state')
    assert after['revision'] == saved['revision'] and after['config'] == config
    assert after['published_id'] == before['published_id']
    result = dict(revision_before=before['revision'], revision=after['revision'], published_id=after['published_id'])
    (out / 'update.json').write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps(result))


if __name__ == '__main__':
    main()
