"""The owner's two choices before entering the existing job search."""
from copy import deepcopy
from project.llm.services.scenario import validate


def configure(config):
    c=deepcopy(config)
    nodes={n['id']:n for n in c['nodes']}
    source=c['sources']['career']
    nodes['work_entry']=dict(id='work_entry',branch='work_choices',title='Ищу работу',kind='message',
        text='С чего хочешь начать?\n\nТест на профориентацию поможет подумать об интересах и направлениях. '
             'Он откроется на сайте «Траектория». Результат — подсказка для твоего выбора.\n\n'
             'Нажми «Поиск работы», чтобы перейти к вакансиям.',
        adult_text='С чего хотите начать?\n\nМожно предложить ребёнку тест на профориентацию на сайте «Траектория» '
                   'или перейти к поиску вакансий. Результат теста — подсказка для выбора профессии.',
        buttons=[dict(label='Пройти тест на профориентацию',url=source['url']),
                 dict(label='Поиск работы',target='work'),dict(label='На главную',target=c['menu'])],
        next='',tasks=[],routes=[],conditions=[],sources=[],editorial_sources=['career'],
        editorial_note='Ссылка взята из существующего источника career. Главная страница открыта 23.09.2026; прохождение и результат теста повторно не проверялись.')
    if not any(b['id']=='work_choices' for b in c['branches']):
        c['branches'].append(dict(id='work_choices',title='Ищу работу — выбор действия',entry='work_entry',
                                 roles=['child','parent','candidate'],status='draft',prompt='',keywords=[]))
    for b in nodes[c['menu']]['buttons']:
        if b['label']=='Ищу работу':b['target']='work_entry'
    c['nodes']=list(nodes.values())
    return validate(c)
