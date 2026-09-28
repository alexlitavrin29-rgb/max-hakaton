"""New OPEN development dialogues. Never use these as acceptance observations."""
import argparse
import asyncio
import json
from pathlib import Path

from project.admin import check_quality as runner
from project.admin.tests.quality_oracles import w, h, hr, request, pay, TOMSK


def corpus():
    base = dict(age=24, city='Томск', query='пекарь')
    first = lambda: w('Мне 24, ищу пекарем в Томске', base, requests=[request('пекарь')])
    work = [
        ('D-W01', [first(), w('У меня два года опыта', base | dict(experience=2), requests=[request('пекарь', exp=2)]),
                   w('Теперь стаж не важен', base, any_fields=['experience'], requests=[request('пекарь')])]),
        ('D-W02', [first(), w('Требуется подработка', base | dict(employment='part_time'), requests=[request('пекарь')]),
                   w('Занятость больше не важна', base, any_fields=['employment'], requests=[request('пекарь')])]),
        ('D-W03', [first(), w('От 2200 рублей за смену до налогов', base | dict(salary=pay(2200, period='shift', tax='gross')), requests=[request('пекарь')])]),
        ('D-W04', [first(), w('С жильём', base | dict(housing=True), requests=[request('пекарь', housing=True)]),
                   w('Жильё больше не нужно', base, any_fields=['housing'], requests=[request('пекарь')])]),
        ('D-W05', [w('В Томске работа нужна сыну, ему 24. У меня высшее образование и мне 48.',
                     dict(city='Томск', age=24), requests=[request()])]),
        ('D-W06', [first(), w('Полная занятость, но не график 2/2', base | dict(employment='full_time'), pending=['schedule'], question='schedule')]),
        ('D-W07', [w('Мне 24, пекарь. Раньше жил в Омске, теперь ищу работу в Томске', base, requests=[request('пекарь')])]),
        ('D-W08', [first(), w('От 60 до 80 тысяч рублей в месяц на руки', base | dict(salary=pay(60000, kind='range', upper=80000, tax='net')), requests=[request('пекарь')])]),
        ('D-W09', [first(), w('Только официальное оформление', base, question='other')]),
        ('D-W10', [w('Мне 24, пекарь в Томске, учусь в колледже', base, pending=['education_level'], question='education_level')]),
    ]
    start = lambda: h('Томск, максимум 30 тысяч за месяц', TOMSK, 30000, requests=[hr()])
    housing = [
        ('D-H01', [start(), h('Ограничение по бюджету снимаю', TOMSK, question='budget'),
                   h('Новый максимум 32 тысячи рублей в месяц', TOMSK, 32000, requests=[hr(budget=32000)])]),
        ('D-H02', [start(), h('Город больше не важен', budget=30000, question='city'),
                   h('Всё-таки Томск', TOMSK, 30000, requests=[hr()])]),
        ('D-H03', [start(), h('Плата за месяц максимум 28 тысяч', TOMSK, 28000, requests=[hr(budget=28000)])]),
        ('D-H04', [h('Квартиру в Томске, до 29 тысяч рублей в месяц, с собакой', TOMSK, 29000, other='с собакой', question='other')]),
        ('D-H05', [h('Томск, хочу снять комнату за 15 тысяч в месяц', TOMSK, other='комнату', question='other')]),
        ('D-H06', [start(), h('Нет, хочу купить квартиру за 4 миллиона', TOMSK, 30000, other='купить', question='other')]),
        ('D-H07', [h('В месяц максимум тридцать две тысячи рублей', budget=32000, question='city'),
                   h('Томск', TOMSK, 32000, requests=[hr(budget=32000)])]),
        ('D-H08', [start(), h('Только без комиссии', TOMSK, 30000, other='без комиссии', question='other')]),
        ('D-H09', [h('Томск, не более 27 тысяч рублей в месяц', TOMSK, 27000, requests=[hr(budget=27000)])]),
        ('D-H10', [h('Томск, до 5000 рублей за неделю', TOMSK, other='за неделю', question='other')]),
        ('D-H11', [h('Томск, квартира на Ленина, дом 12. До 30 тысяч в месяц', TOMSK, 30000,
                    other='дом 12', question='other'),
                   h('', TOMSK, 30000, other='дом 12', consent=True, requests=[hr()],
                     button='Искать только по городу и бюджету')]),
    ]
    return [dict(id=i, branch=b, role='parent' if i == 'D-W05' else 'child', turns=turns)
            for b, cases in [('work', work), ('rental', housing)] for i, turns in cases]


if __name__ == '__main__':
    p = argparse.ArgumentParser()
    p.add_argument('--live', action='store_true')
    p.add_argument('--replay')
    p.add_argument('--output', required=True)
    p.add_argument('--ids')
    p.add_argument('--freeze', action='store_true')
    args = p.parse_args()
    if args.freeze:
        Path(args.output).write_text(json.dumps(corpus(), ensure_ascii=False, indent=2), encoding='utf-8')
    else:
        if not args.live and not args.replay:
            p.error('choose --live or --replay')
        runner.corpus = corpus
        asyncio.run(runner.run(args))
