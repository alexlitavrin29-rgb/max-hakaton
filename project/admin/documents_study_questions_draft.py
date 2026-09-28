"""Add approved document and study answers to the local draft only."""

from copy import deepcopy
import json
import re

from project.admin.documents_reference_draft import ROOT, request
from project.llm.services.scenario import validate


PROPOSAL = ROOT / 'docs/documents-study-human-copy-2026-09-25.md'
OUTPUT = ROOT / 'docs/test-results/documents-study-questions'

CARD_IDS = {
    'Д1': 'doc_passport_q1', 'Д2': 'doc_passport_q2', 'Д3': 'doc_passport_q3',
    'Д4': 'doc_birth_q3', 'Д5': 'doc_snils_lookup', 'Д6': 'doc_snils_proof',
    'Д7': 'doc_inn_lookup', 'Д8': 'doc_inn_proof', 'Д9': 'doc_reg_home',
    'Д10': 'doc_reg_stay', 'Д11': 'doc_reg_dorm', 'Д12': 'exit_docs',
    'Д13': 'doc_edu_school', 'Д14': 'doc_edu_college', 'Д15': 'doc_edu_university',
    'У1': 'college9', 'У2': 'college11', 'У3': 'university', 'У4': 'dorm',
    'У5': 'social_scholarship', 'У6': 'academic_scholarship', 'У7': 'study_provision',
}

ALIASES = {
    'passport_first': 'doc_passport_q1',
    'passport_replace': 'doc_passport_q2',
    'passport_lost': 'doc_passport_q3',
    'snils': 'doc_snils_lookup',
    'snils_proof': 'doc_snils_proof',
    'inn': 'doc_inn_lookup',
    'inn_proof': 'doc_inn_proof',
    'reg_home': 'doc_reg_home',
    'reg_stay': 'doc_reg_stay',
    'reg_dorm': 'doc_reg_dorm',
}

SOURCE_URLS = {
    'Д1': 'https://publication.pravo.gov.ru/document/0001202312290097',
    'Д2': 'https://publication.pravo.gov.ru/document/0001202312290097',
    'Д3': 'https://publication.pravo.gov.ru/document/0001202312290097',
    'Д4': 'https://www.gosuslugi.ru/600408/1/form',
    'Д5': 'https://sfr.gov.ru/grazhdanam/personificirovannyj_uchet/registraciya_i_oformlenie_snils/',
    'Д6': 'https://sfr.gov.ru/grazhdanam/personificirovannyj_uchet/registraciya_i_oformlenie_snils/',
    'Д7': 'https://service.nalog.ru/inn.do',
    'Д8': 'https://www.nalog.gov.ru/rn77/related_activities/accounting/egrn/',
    'Д9': 'https://publication.pravo.gov.ru/document/0001202604170025',
    'Д10': 'https://publication.pravo.gov.ru/document/0001202604170025',
    'Д11': 'https://publication.pravo.gov.ru/document/0001202604170025',
    'Д12': 'https://www.consultant.ru/document/cons_doc_LAW_88016/cee1d5d730bbdba05cc0fb2b086314fff78664cc/',
    'Д13': 'https://docs.edu.gov.ru/document/cb801fa9f228b82d91e6e2455511c046/',
    'Д14': 'https://publication.pravo.gov.ru/Document/View/0001202211250010',
    'Д15': 'https://publication.pravo.gov.ru/Document/View/0001202108250039',
    'У1': 'https://docs.edu.gov.ru/document/f188a6329bb376dfc57e887a04feb6ad/',
    'У2': 'https://docs.edu.gov.ru/document/f188a6329bb376dfc57e887a04feb6ad/',
    'У3': 'https://publication.pravo.gov.ru/document/0001202411290031',
    'У4': 'https://publication.pravo.gov.ru/document/0001202602200055',
    'У5': 'https://publication.pravo.gov.ru/document/0001202608040018',
    'У6': 'https://www.consultant.ru/document/cons_doc_LAW_140174/fc74ef70a4fc3107df5b2b18636ac5f74e3d0a73/',
    'У7': 'https://publication.pravo.gov.ru/document/0001202608040018',
}


def read_cards():
    content = PROPOSAL.read_text(encoding='utf-8')
    headings = list(re.finditer(r'(?m)^### ([ДУ]\d+)\. (.+)$', content))
    cards = {}
    for i, match in enumerate(headings):
        end = headings[i + 1].start() if i + 1 < len(headings) else content.find('\n## ', match.end())
        block = content[match.end():end if end >= 0 else None]
        answer = re.search(r'(?m)^> (.+)$', block)
        checklist = re.search(r'(?m)^Чек-лист: (.+?) \*\*Дополнение:\*\* (.+)$', block)
        if not answer or not checklist:
            raise ValueError(f'Card {match.group(1)} is missing answer or checklist')
        cards[match.group(1)] = dict(
            title=match.group(2),
            answer=answer.group(1),
            tasks=[task_copy(item.strip().rstrip('.')) for item in checklist.group(1).split(';')],
            extra=checklist.group(2)[0].upper() + checklist.group(2)[1:],
        )
    if set(cards) != set(CARD_IDS):
        raise ValueError(f'Expected {set(CARD_IDS)}, got {set(cards)}')
    return cards


def task_copy(value):
    return value[0].upper() + value[1:]


def adult_copy(value):
    for child, parent in [
        ('Если собирать всё одному трудно, попроси взрослого помочь.',
         'Если ребёнку трудно, помогите ему собрать документы.'),
        ('Если ты ребёнок-сирота', 'Если ребёнок — сирота'),
        ('Если это про тебя', 'Если это относится к ребёнку'),
        ('Если у тебя есть', 'Если у ребёнка есть'),
        ('у тебя', 'у ребёнка'),
        ('тебе передают твои', 'ребёнку передают его'),
        ('ИНН — твой номер', 'ИНН — номер ребёнка'),
        ('твою программу', 'программу ребёнка'),
        ('твои документы', 'документы ребёнка'),
        ('вместе с тобой', 'вместе с ребёнком'),
        ('для твоего случая', 'для вашей ситуации'),
        ('на сайте своего колледжа или вуза', 'на сайте учебной организации ребёнка'),
    ]:
        value = value.replace(child, parent)
    replacements = [
        ('ты', 'ребёнок'),
        ('твоего правового статуса', 'правового статуса ребёнка'),
        ('твоего колледжа или вуза', 'колледжа или вуза ребёнка'),
        ('своего колледжа или вуза', 'колледжа или вуза ребёнка'),
        ('твоём случае', 'случае ребёнка'),
        ('твоё основание', 'основание ребёнка'),
        ('своё основание', 'основание ребёнка'),
        ('тебе', 'ребёнку'),
        ('Если подаёшь', 'Если подаёте'),
        ('ты ею пользуешься', 'ребёнок ею пользуется'),
        ('проверь', 'проверьте'),
        ('подготовь', 'подготовьте'),
        ('обратись', 'обратитесь'),
        ('уточни', 'уточните'),
        ('подай', 'подайте'),
        ('посмотри', 'посмотрите'),
        ('начни', 'начните'),
        ('опиши', 'опишите'),
        ('приложи', 'приложите'),
        ('выбери', 'выберите'),
        ('сохрани', 'сохраните'),
        ('открой', 'откройте'),
        ('введи', 'введите'),
        ('получи', 'получите'),
        ('найди', 'найдите'),
        ('спроси', 'спросите'),
        ('попроси', 'попросите'),
        ('запиши', 'запишите'),
        ('выпиши', 'выпишите'),
        ('узнай', 'узнайте'),
        ('напиши', 'напишите'),
        ('расскажи', 'расскажите'),
        ('отметь', 'отметьте'),
        ('укажи', 'укажите'),
        ('ищи', 'ищите'),
        ('сверь', 'сверьте'),
        ('начинай', 'начинайте'),
    ]
    for old, new in replacements:
        value = re.sub(r'\b' + re.escape(old) + r'\b',
                       lambda match: new.capitalize() if match.group().istitle() else new,
                       value, flags=re.IGNORECASE)
    return value


def button(label, target):
    return dict(label=label, target=target)


def menu(node, title, intro, choices):
    node.update(title=title, text=title + '\n\n' + intro,
                adult_text=title + '\n\n' + intro.replace('Выбери', 'Выберите'),
                buttons=[button(label, target) for label, target in choices],
                tasks=[], sources=[], next='', routes=[], conditions=[])


def configure(config):
    cards = read_cards()
    result = deepcopy(config)
    nodes = {node['id']: node for node in result['nodes']}
    created = {}

    for code, key in CARD_IDS.items():
        data = cards[code]
        original = nodes.get(key, {})
        branch = 'housing' if code == 'Д12' else 'education' if code.startswith('У') else 'documents'
        answer = data['answer']
        extra = data['extra']
        text = answer + '\n\nВот с чего можно начать:'
        adult_text = adult_copy(text)
        node = dict(original, id=key, title=data['title'], branch=branch,
                    kind='message', text=text,
                    adult_text=adult_text,
                    format='markdown',
                    tasks=data['tasks'], next='', routes=[], conditions=[], sources=[],
                    buttons=[button('Что ещё может понадобиться', key + '_extra'),
                             dict(label='Напомнить', target='reminder_new',
                                  values={'reminder_text': 'Вернуться к вопросу: ' + data['title']})],
                    content_status='prepared', editorial_sources=['ds_' + code])
        nodes[key] = node
        created[key] = node
        extra_node = dict(id=key + '_extra', title=data['title'] + ': дополнение', branch=branch,
                          kind='message', text=extra, adult_text=adult_copy(extra), tasks=[],
                          format='markdown',
                          buttons=[], next='', routes=[], conditions=[], sources=[],
                          content_status='prepared', editorial_sources=['ds_' + code])
        nodes[key + '_extra'] = extra_node
        created[key + '_extra'] = extra_node
        result['sources']['ds_' + code] = dict(
            title='Проверенная основа: ' + data['title'], url=SOURCE_URLS[code], checked='2026-09-25')

    # Older cards are still linked from other branches. Give them the same
    # checked answer instead of leaving a second, outdated placeholder.
    for old, canonical in ALIASES.items():
        alias = deepcopy(nodes[canonical])
        alias['id'] = old
        alias['buttons'][0]['target'] = old + '_extra'
        nodes[old] = alias
        created[old] = alias
        extra = deepcopy(nodes[canonical + '_extra'])
        extra['id'] = old + '_extra'
        nodes[old + '_extra'] = extra
        created[old + '_extra'] = extra
    menu(nodes['birth'], 'Свидетельство о рождении',
         'Какой вопрос сейчас важен?', [
             ('Получить повторное свидетельство', 'doc_birth_q3'),
             ('Для чего нужно свидетельство?', 'doc_birth_q1'),
             ('К документам', 'documents'),
         ])
    nodes['birth']['content_status'] = 'prepared'
    menu(nodes['diploma_lost'], 'Потерян аттестат или диплом',
         'Выбери документ, чтобы увидеть подходящий порядок.', [
             ('Школьный аттестат', 'doc_edu_school'),
             ('Диплом колледжа', 'doc_edu_college'),
             ('Диплом вуза', 'doc_edu_university'),
             ('К документам', 'documents'),
         ])
    nodes['diploma_lost']['content_status'] = 'prepared'

    docs = nodes['documents']
    docs['text'] = ('Документы\n\nЧто нужно сделать? Выбери тему — подскажу первые шаги '
                    'и куда перейти за порядком для твоего случая.')
    docs['adult_text'] = ('Документы ребёнка\n\nВыберите задачу. Подскажу первые шаги '
                          'и где посмотреть порядок для вашей ситуации.')
    docs['buttons'] = [b for b in docs['buttons'] if b.get('target') != 'exit_docs']
    docs['buttons'].insert(-1, button('Что передадут при выпуске?', 'exit_docs'))
    for item in nodes['numbers']['buttons']:
        if item.get('target') == 'doc_numbers_exit':
            item['target'] = 'exit_docs'

    menu(nodes['doc_passport'], 'Паспорт РФ', 'Какой вопрос сейчас важен?', [
        ('Первый паспорт в 14 лет', 'doc_passport_q1'),
        ('Заменить паспорт', 'doc_passport_q2'),
        ('Потерян паспорт', 'doc_passport_q3'),
        ('Назад', 'identity'), ('К документам', 'documents'), ('На главную', result['menu']),
    ])
    nodes['doc_passport']['content_status'] = 'prepared'
    menu(nodes['doc_birth'], 'Свидетельство о рождении', 'Выбери вопрос о свидетельстве.', [
        ('Зачем оно нужно?', 'doc_birth_q1'),
        ('Как хранить и защищать?', 'doc_birth_q2'),
        ('Получить повторное свидетельство', 'doc_birth_q3'),
        ('Назад', 'identity'), ('К документам', 'documents'), ('На главную', result['menu']),
    ])
    for key, first, second in [
        ('doc_snils', 'doc_snils_lookup', 'doc_snils_proof'),
        ('doc_inn', 'doc_inn_lookup', 'doc_inn_proof'),
    ]:
        title = 'СНИЛС' if key == 'doc_snils' else 'ИНН'
        menu(nodes[key], title, 'С номером и документом о нём помогут разные действия. Что нужно?', [
            ('Узнать номер', first), ('Получить подтверждение', second),
            ('Для чего нужен?', key + '_q1'), ('Назад', 'numbers'),
            ('К документам', 'documents'), ('На главную', result['menu']),
        ])
    loss = nodes['lost']
    loss['buttons'] = [
        button('С чего начать?', 'doc_lost_start'),
        button('Потерян паспорт', 'doc_passport_q3'),
        button('Потеряно свидетельство', 'doc_birth_q3'),
        button('Потерян аттестат или диплом', 'doc_edu_duplicate'),
    ] + [b for b in loss['buttons'] if b.get('target') in {
        'doc_lost_inventory', 'doc_lost_police', 'doc_lost_photos',
        'doc_lost_fraud', 'doc_lost_storage', 'documents', result['menu'],
    }]
    loss['text'] = 'Потеря документов\n\nЭто неприятно, но документы можно восстановить. Выбери, что пропало.'
    loss['adult_text'] = 'Потеря документов\n\nВыберите, какой документ нужно восстановить.'

    registration = nodes['registration']
    registration['buttons'] = [
        button('Постоянная регистрация', 'doc_reg_home'),
        button('Временная регистрация', 'doc_reg_stay'),
        button('Регистрация в общежитии', 'doc_reg_dorm'),
    ] + [b for b in registration['buttons'] if b.get('target') not in {
        'doc_reg_apply', 'doc_reg_home', 'doc_reg_stay', 'doc_reg_dorm',
    }]
    registration['text'] = 'Прописка и регистрация\n\nГде нужно оформить регистрацию? Выбери подходящий вариант.'
    registration['adult_text'] = 'Прописка и регистрация\n\nГде нужно оформить регистрацию ребёнка? Выберите подходящий вариант.'
    menu(nodes['doc_reg_apply'], 'Оформление регистрации', 'Сначала выбери вид регистрации.', [
        ('Постоянная', 'doc_reg_home'), ('Временная', 'doc_reg_stay'),
        ('В общежитии', 'doc_reg_dorm'), ('Назад', 'registration'),
    ])

    def add_menu(key, branch, title, intro, choices):
        item = dict(id=key, branch=branch, title=title, kind='message',
                    text='', adult_text='', buttons=[], tasks=[], sources=[],
                    next='', routes=[], conditions=[], content_status='prepared')
        menu(item, title, intro, choices)
        nodes[key] = item
        created[key] = item

    add_menu('doc_edu_duplicate', 'documents', 'Документ об образовании',
             'Что нужно восстановить? У аттестата и двух видов диплома разные правила.', [
                 ('Школьный аттестат', 'doc_edu_school'),
                 ('Диплом колледжа', 'doc_edu_college'),
                 ('Диплом вуза', 'doc_edu_university'),
                 ('К документам', 'documents'),
             ])
    add_menu('education_document', 'education', 'Документ об образовании',
             'Что нужно восстановить? Выбери документ, чтобы увидеть подходящий порядок.', [
                 ('Школьный аттестат', 'doc_edu_school'),
                 ('Диплом колледжа', 'doc_edu_college'),
                 ('Диплом вуза', 'doc_edu_university'),
                 ('К учёбе', 'education'),
             ])

    education = nodes['education']
    menu(education, 'Учёба', 'Давай разберёмся с ближайшей задачей.', [
        ('Поступление', 'admission'),
        ('Во время учёбы', 'education_support'),
        ('Документ об образовании', 'education_document'),
    ] + [(b['label'], b['target']) for b in education['buttons']
         if b.get('target') not in {'admission', 'education_support', 'education_document', result['menu']}]
      + [('Главное меню', result['menu'])])
    education['adult_text'] = 'Учёба ребёнка\n\nВыберите задачу, с которой хотите помочь.'
    menu(nodes['admission'], 'Поступление', 'После какого класса или на какую программу?', [
        ('Колледж после 9 класса', 'college9'),
        ('Колледж после 11 класса', 'college11'),
        ('Вуз: бакалавриат или специалитет', 'university'),
        ('К учёбе', 'education'),
    ])
    menu(nodes['education_support'], 'Во время учёбы',
         'Общежитие, стипендия и обеспечение оформляются отдельно. Выбери вопрос.', [
             ('Место в общежитии', 'dorm'),
             ('Социальная стипендия', 'social_scholarship'),
             ('Академическая стипендия', 'academic_scholarship'),
             ('Обеспечение во время учёбы', 'study_provision'),
             ('К учёбе', 'education'),
         ])
    menu(nodes['scholarship'], 'Стипендия', 'Выбери вид стипендии.', [
        ('Социальная', 'social_scholarship'),
        ('Академическая', 'academic_scholarship'),
        ('Во время учёбы', 'education_support'),
    ])
    nodes['scholarship']['content_status'] = 'prepared'
    result['nodes'] = list(nodes.values())
    result['materials'] = [m for m in result['materials'] if m['id'] != 'documents_study_prepared_20260925']
    result['materials'].append(dict(
        id='documents_study_prepared_20260925',
        title='Документы и учёба: понятные ответы 25.09.2026',
        text=PROPOSAL.read_text(encoding='utf-8'), branches=['documents', 'education'],
        roles=[], regions=[], min_age=None, max_age=None,
        sources=['ds_' + code for code in CARD_IDS], checked='2026-09-25',
        review_days=30, enabled=False,
    ))
    return validate(result), created


def main():
    before = request('state')
    config, created = configure(before['config'])
    OUTPUT.mkdir(parents=True, exist_ok=True)
    (OUTPUT / 'before.json').write_text(
        json.dumps(before, ensure_ascii=False, indent=2), encoding='utf-8')
    saved = request('draft', dict(config=config, revision=before['revision']), 'PUT')
    after = request('state')
    assert after['config'] == config
    assert after['revision'] == saved['revision']
    assert after['published_id'] == before['published_id']
    summary = dict(revision_before=before['revision'], revision=after['revision'],
                   published_id=after['published_id'], cards=len(CARD_IDS),
                   old_cards_updated=len(ALIASES) + 2,
                   created_nodes=len(created))
    (OUTPUT / 'update.json').write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding='utf-8')
    lines = ['# Проверенные ответы «Документы» и «Учёба» в черновике', '',
             f"Черновик {after['revision']}; опубликованная версия {after['published_id']} не менялась.", '']
    for code, key in CARD_IDS.items():
        node = next(n for n in config['nodes'] if n['id'] == key)
        lines.extend(['## ' + code + ' · ' + node['title'], '', node['text'], ''])
        lines.extend('- ☐ ' + task for task in node['tasks'])
        lines.extend(['', '**Что ещё может понадобиться:** ' + created[key + '_extra']['text'], ''])
    (ROOT / 'docs/documents-study-question-copy.md').write_text('\n'.join(lines), encoding='utf-8')
    print(json.dumps(summary, ensure_ascii=False))


if __name__ == '__main__':
    main()
