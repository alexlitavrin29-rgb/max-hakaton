"""Split the existing reference into question buttons; update only the draft."""
from copy import deepcopy
import json
import re

from project.admin.documents_reference_draft import ROOT, request
from project.llm.services.scenario import validate


def split(text, headings):
    """Keep all text, including headings, when separating prepared answers."""
    positions = []
    for heading in headings:
        match = re.search(r'^' + re.escape(heading) + r'(?=\n|$)', text, re.M)
        if not match:
            raise ValueError('Текст изменился: не найден заголовок «' + heading + '». Нужна сверка перед обновлением.')
        positions.append(match.start())
    assert positions == sorted(positions) and len(set(positions)) == len(positions)
    parts = [text[:positions[0]].strip()]
    parts += [text[start:end].strip() for start, end in zip(positions, positions[1:] + [len(text)])]
    assert '\n\n'.join(parts).strip() == text.strip()
    return parts


def configure(config):
    c = deepcopy(config)
    nodes = {n['id']: n for n in c['nodes']}
    added = {}

    def source(key):
        node = nodes[key]
        assert node.get('content_status') == 'unverified_reference', key
        text = node['text']
        if ' из ' in text.split('\n', 1)[0]:
            text = text.split('\n\n', 1)[1]
        return text

    def node(key, title, text, parent, choices=()):
        controls = [dict(label=label, target=target) for label, target in choices]
        controls.append(dict(label='Назад', target=parent))
        if parent != 'documents':
            controls.append(dict(label='К оглавлению', target='documents'))
        controls.append(dict(label='На главную', target=c['menu']))
        if not choices:
            controls.append(dict(label='Напомнить', target='reminder_new',
                                 values={'reminder_text': 'Вернуться к памятке о документах.'}))
        added[key] = dict(id=key, branch='documents', title=title, kind='message',
                          text=text, adult_text='Памятка обращена к ребёнку или выпускнику.\n\n' + text,
                          buttons=controls, next='', tasks=[], conditions=[], routes=[], sources=[],
                          content_status='unverified_reference', editorial_sources=['documents_owner_reference'])

    def questions(key, title, text, parent, headings, labels=None):
        intro, *answers = split(text, headings)
        ids = [key + '_q' + str(i + 1) for i in range(len(answers))]
        labels = labels or headings
        node(key, title, intro or title, parent, list(zip(labels, ids)))
        for answer_id, label, answer in zip(ids, labels, answers):
            node(answer_id, title + ' — ' + label, title + '\n\n' + answer, key)
        return ids

    node('identity', 'Паспорт и свидетельство о рождении',
         'Паспорт и свидетельство о рождении\n\nО каком документе хочешь узнать?', 'documents',
         [('Свидетельство о рождении', 'doc_birth'), ('Паспорт РФ', 'doc_passport'), ('Загранпаспорт', 'doc_foreign')])
    questions('doc_birth', 'Свидетельство о рождении', source('identity'), 'identity',
              ['Зачем оно нужно?', 'Как хранить и защищать?', 'Что делать при потере?'])
    questions('doc_passport', 'Паспорт РФ', source('identity_reference_2'), 'identity',
              ['Как получить первый паспорт?', 'Когда менять паспорт?', 'Что делать при потере паспорта?'])
    questions('doc_foreign', 'Загранпаспорт', source('identity_reference_3'), 'identity',
              ['Какие бывают виды?', 'Как оформить?', 'Какие документы понадобятся?', 'Несколько советов'],
              ['Какие бывают виды?', 'Как оформить?', 'Какие документы понадобятся?', 'Что ещё учесть перед поездкой?'])

    intro, snils, inn = split(source('numbers'), ['Что такое СНИЛС?', 'Что такое ИНН?'])
    inn, at_exit = inn.rsplit('\n\n', 1)
    node('numbers', 'СНИЛС и ИНН', intro, 'documents',
         [('СНИЛС', 'doc_snils'), ('ИНН', 'doc_inn'), ('Что выдадут при выпуске?', 'doc_numbers_exit')])
    questions('doc_snils', 'СНИЛС', snils, 'numbers',
              ['Для чего он нужен?', 'Раньше СНИЛС выдавали в виде зелёной карточки. Сейчас он доступен в электронном виде. Подтверждение можно распечатать, но достаточно знать свой номер.'],
              ['Для чего он нужен?', 'Как выглядит подтверждение?'])
    questions('doc_inn', 'ИНН', inn, 'numbers',
              ['Для чего он нужен?', 'ИНН существует в электронном виде. При необходимости можно распечатать свидетельство.'],
              ['Для чего он нужен?', 'Нужно ли бумажное свидетельство?'])
    node('doc_numbers_exit', 'СНИЛС и ИНН при выпуске', at_exit, 'numbers')

    questions('gosuslugi', 'Госуслуги', source('gosuslugi'), 'documents',
              ['Зачем нужны Госуслуги?', 'Как зарегистрироваться?', 'Как подтвердить личность?', 'Как пользоваться?'])
    added['gosuslugi']['buttons'][4:4] = [dict(label='Госключ', target='doc_goskey'),
                                       dict(label='Другие государственные сервисы', target='doc_services')]
    goskey, services = split(source('gosuslugi_reference_2'), ['ДРУГИЕ ПОЛЕЗНЫЕ СЕРВИСЫ'])
    intro, use, where, setup = split(goskey, ['Госключ — бесплатное приложение Минцифры РФ. Оно нужно, чтобы:',
                                            'С его помощью можно войти:', 'Как получить Госключ?'])
    node('doc_goskey', 'Госключ', intro + '\n\nЧто хочешь узнать о приложении?', 'gosuslugi',
         [('Зачем нужен Госключ?', 'doc_goskey_use'), ('Где можно использовать?', 'doc_goskey_where'),
          ('Как получить Госключ?', 'doc_goskey_setup')])
    for key, title, text in [('use', 'Зачем нужен Госключ?', use), ('where', 'Где можно использовать Госключ?', where),
                             ('setup', 'Как получить Госключ?', setup)]:
        node('doc_goskey_' + key, title, text, 'doc_goskey')
    questions('doc_services', 'Другие государственные сервисы', services, 'gosuslugi',
              ['ЕСИА', 'Мос.ру', 'Госуслуги Авто'])

    loss_intro, calm, inventory, police, passport = split(source('lost'),
        ['1. Постарайся успокоиться', '2. Запиши, какие документы потерялись', '3. Сообщи о потере в полицию', '4. Восстанови паспорт'])
    other, photos, fraud, storage = split(source('lost_reference_2'),
        ['6. НЕ ХРАНИ ФОТО ДОКУМЕНТОВ В ТЕЛЕФОНЕ', '7. БУДЬ ВНИМАТЕЛЕН К МОШЕННИКАМ', 'Совет по хранению'])
    node('lost', 'Что делать при потере документов?',
         'Что делать при потере документов?\n\nВыбери вопрос — открою нужную часть памятки.', 'documents',
         [('С чего начать?', 'doc_lost_start'), ('Какие документы потерялись?', 'doc_lost_inventory'),
          ('Как сообщить о потере?', 'doc_lost_police'), ('Как восстановить паспорт?', 'doc_lost_passport'),
          ('Как восстановить другие документы?', 'doc_lost_others'), ('Можно ли хранить фото в телефоне?', 'doc_lost_photos'),
          ('Как защититься от мошенников?', 'doc_lost_fraud'), ('Как подготовиться и хранить документы?', 'doc_lost_storage')])
    for key, title, text in [('start', 'С чего начать?', calm), ('inventory', 'Какие документы потерялись?', inventory),
                            ('police', 'Как сообщить о потере?', police), ('photos', 'Можно ли хранить фото в телефоне?', photos),
                            ('fraud', 'Как защититься от мошенников?', fraud),
                            ('storage', 'Как подготовиться и хранить документы?', loss_intro + '\n\n' + storage)]:
        node('doc_lost_' + key, title, text, 'lost')
    questions('doc_lost_passport', 'Восстановление паспорта', passport, 'lost',
              ['Как восстановить паспорт?'], ['Как восстановить паспорт?'])
    questions('doc_lost_others', 'Восстановление других документов', other, 'lost',
              ['СНИЛС', 'ИНН', 'Медицинские документы', 'Водительские права'])

    questions('registration', 'Прописка и регистрация', source('registration'), 'documents',
              ['Что это и зачем нужно?', 'Какие бывают виды?', 'Если переезжаешь в другой город', 'Если надолго уезжаешь за границу'],
              ['Что это и зачем нужно?', 'Какие бывают виды?', 'Что делать при переезде в другой город?', 'Что делать при переезде за границу?'])
    added['registration']['buttons'][4:4] = [dict(label='Как оформить регистрацию?', target='doc_reg_apply'),
                                          dict(label='Что делать при смене фамилии?', target='doc_reg_name'),
                                          dict(label='Можно ли жить без регистрации?', target='doc_reg_without')]
    apply, name, without = split(source('registration_reference_2'), ['ЕСЛИ ИЗМЕНИЛАСЬ ФАМИЛИЯ', 'МОЖНО ЛИ ЖИТЬ БЕЗ РЕГИСТРАЦИИ?'])
    questions('doc_reg_apply', 'Оформление регистрации', apply, 'registration', ['Что понадобится?'])
    node('doc_reg_name', 'Что делать при смене фамилии?', name, 'registration')
    node('doc_reg_without', 'Можно ли жить без регистрации?', without, 'registration')

    # Old sequential pages may still have incoming links. Keep them as short
    # pointers to the new question menus, without a second copy of the article.
    redirects = {'identity_reference_2': 'doc_passport', 'identity_reference_3': 'doc_foreign',
                 'gosuslugi_reference_2': 'doc_goskey', 'lost_reference_2': 'doc_lost_others',
                 'registration_reference_2': 'doc_reg_apply'}
    for key, target in redirects.items():
        node(key, nodes[key]['title'], 'Выбери нужный вопрос в памятке.', 'documents',
             [('Открыть вопросы', target)])
    nodes.update(added)
    c['nodes'] = list(nodes.values())
    return validate(c), added


def main():
    before = request('state')
    config, added = configure(before['config'])
    output = ROOT / 'docs/test-results/documents-questions'
    output.mkdir(parents=True, exist_ok=True)
    (output / 'before.json').write_text(json.dumps(before['config'], ensure_ascii=False, indent=2), encoding='utf-8')
    saved = request('draft', dict(config=config, revision=before['revision']), 'PUT')
    after = request('state')
    assert after['config'] == config and after['revision'] == saved['revision']
    assert after['published_id'] == before['published_id']
    result = dict(revision_before=before['revision'], revision=after['revision'], published_id=after['published_id'],
                  nodes=list(added), saved_matches=True)
    (output / 'update.json').write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps(dict(revision=result['revision'], published_id=result['published_id'], nodes=len(added))))


if __name__ == '__main__':
    main()
