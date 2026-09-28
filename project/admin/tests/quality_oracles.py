"""Development oracles frozen before runtime edits, 2026-09-26. Never a holdout.

All nine fields are explicit after expansion. Expectations come from the owner's
W01-W19/H01-H14, never model output or the engine under test.
"""
from copy import deepcopy

NAMES = 'query city age education_level experience employment schedule salary housing'.split()
TOMSK = dict(name='Томск', region='Томская область', slug='tomsk', location_id=657600)
OMSK = dict(name='Омск', region='Омская область', slug='omsk', location_id=657000)
PLACES = [TOMSK, OMSK,
          dict(name='Советск', region='Калининградская область', slug='sovetsk_kgd', location_id=1001),
          dict(name='Советск', region='Кировская область', slug='sovetsk_kir', location_id=1002)]

def pay(amount, kind='min', period='month', tax='unknown', upper=None):
    return dict(kind=kind, lower=None if kind=='max' else amount,
                upper=amount if kind=='max' else upper, period=period, tax=tax, currency='RUB')

def request(q=None, code='7000000000000', exp=None, housing=None):
    return dict(query=q, region_code=code, experience_to=exp, accommodation=housing, limit=100, offset=0)

def w(text, known, pending=(), question=None, requests=None, any_fields=(), applicant='child'):
    fields={n:dict(status='unknown',value=None) for n in NAMES}
    fields.update({n:dict(status='known',value=v) for n,v in known.items()})
    fields.update({n:dict(status='any',value=None) for n in any_fields})
    return dict(text=text, applicant=applicant, fields=fields, pending=list(pending),
                question=question, requests=requests or [])

BASE=dict(query='сварщик',city='Томск',age=20)
WORK = [
 ('W01',[w('Мне 20. Ищу сварщиком в Томске, после колледжа, без опыта, подработка, график 2/2, от 70 тысяч рублей в месяц, нужно жильё',
     dict(**BASE,education_level='vocational',experience=0,employment='part_time',schedule='2/2',salary=pay(70000),housing=True),requests=[request('сварщик',exp=0,housing=True)])]),
 ('W02',[w('Томск',dict(city='Томск'),question='age'), w('Мне 21',dict(city='Томск',age=21),requests=[request()]),
         w('Повар, без опыта, полная занятость',dict(city='Томск',age=21,query='повар',experience=0,employment='full_time'),requests=[request('повар',exp=0)])]),
 ('W03',[w('Мне 43, дочери 20. Ей нужна работа ветеринарным фельдшером в Томске',dict(city='Томск',age=20,query='ветеринарный фельдшер'),requests=[request('ветеринарный фельдшер')])]),
 ('W04',[w('Ищу работу для дочери, а для себя не ищу. Дочери 19, Томск',dict(city='Томск',age=19),requests=[request()])]),
 ('W05',[w('Мне 22, Томск, повар',dict(city='Томск',age=22,query='повар'),requests=[request('повар')]),w('Не поваром, а пекарем',dict(city='Томск',age=22,query='пекарь'),requests=[request('пекарь')])]),
 ('W06',[w('Мне 22, сварщик в Томске',{**BASE,'age':22},requests=[request('сварщик')]),w('Теперь в Омске',{**BASE,'age':22,'city':'Омск'},requests=[request('сварщик','5500000000000')])]),
 ('W07',[w('Мне 22, Томск, сварщик, от 70 тысяч рублей в месяц',{**BASE,'age':22,'salary':pay(70000)},requests=[request('сварщик')]),w('Зарплата теперь не важна',{**BASE,'age':22},any_fields=['salary'],requests=[request('сварщик')])]),
 ('W08',[w('Мне 22, Томск, сварщик, хочу 70',{**BASE,'age':22},pending=['salary'],question='salary'),w('Тысяч за месяц, минимум',{**BASE,'age':22,'salary':pay(70000)},requests=[request('сварщик')])]),
 ('W09',[w('Мне 22, Томск, курьер, от 1500 рублей за смену на руки',dict(city='Томск',age=22,query='курьер',salary=pay(1500,period='shift',tax='net')),requests=[request('курьер')])]),
 ('W10',[w('Мне 20, Томск, сварщик, высшего образования нет',BASE,pending=['education_level'],question='education_level')]),
 ('W11',[w('Мне 20, сварщик в Туле',dict(age=20,query='сварщик'),pending=['city'],question='city'),w('Именно город Тула в Тульской области',{**BASE,'city':'Тула'},requests=[request('сварщик','7100000000000')])]),
 ('W12',[w('Мне 20, сварщик в Томске или Омске',dict(age=20,query='сварщик'),pending=['city'],question='city'),w('В обоих городах',{**BASE,'city':['Томск','Омск']},requests=[request('сварщик'),request('сварщик','5500000000000')])]),
 ('W13',[w('Мне 20, Томск, сварщик или токарь',dict(age=20,city='Томск'),pending=['query'],question='query'),w('Только токарь',{**BASE,'query':'токарь'},requests=[request('токарь')])]),
 ('W14',[w('Мне 20, Томск, любая работа с жильём',dict(age=20,city='Томск',housing=True),any_fields=['query'],requests=[request(housing=True)])]),
 ('W15',[w('Мне 20, Томск, сварщик, только не 2/2',BASE,pending=['schedule'],question='schedule')]),
 ('W16',[w('Мне 20, в Томске нужна подработка',dict(age=20,city='Томск',employment='part_time'),requests=[request()])]),
 ('W17',[w('Раньше получал 50 тысяч, теперь ищу сварщиком в Томске. Мне 20',BASE,requests=[request('сварщик')])]),
 ('W18',[w('Мне 20, сварщик в Томске',BASE,requests=[request('сварщик')])]),
 ('W19',[w('Мне 20, сварщик в Томске',BASE,requests=[request('сварщик')])]),
]

def h(text, city=None, budget=None, pending=(), question=None, requests=None, other=None, consent=False, button=None):
    return dict(text=text,city=city,budget=budget,pending=list(pending),question=question,
                requests=requests or [],other=other,consent=consent,button=button)

def hr(city='tomsk',budget=30000):
    return dict(location=city,budget=budget,page=1)

HBASE=h('Томск до 30 тысяч',TOMSK,30000,requests=[hr()])
RENTAL = [
 ('H01',[h('Томск, до 30 тысяч рублей в месяц',TOMSK,30000,requests=[hr()])]),
 ('H02',[h('Томск',TOMSK,question='budget'),h('До 25 тысяч рублей в месяц',TOMSK,25000,requests=[hr(budget=25000)])]),
 ('H03',[h('Максимум 30 тысяч за месяц',budget=30000,question='city'),h('В Томске',TOMSK,30000,requests=[hr()])]),
 ('H04',[deepcopy(HBASE),h('Не 30, а 25 тысяч',TOMSK,25000,requests=[hr(budget=25000)])]),
 ('H05',[h('Томск, максимум 30',TOMSK,pending=['budget'],question='budget'),h('Тысяч в месяц',TOMSK,30000,requests=[hr()])]),
 ('H06',[h('Советск, до 30 тысяч',budget=30000,pending=['city'],question='city'),h('',PLACES[2],30000,requests=[hr('sovetsk_kgd')],button='Советск — Калининградская область')]),
 ('H07',[h('Томск до 30 тысяч, без залога',TOMSK,30000,other='без залога',question='other'),h('',TOMSK,30000,other='без залога',consent=True,requests=[hr()],button='Искать только по городу и бюджету')]),
 ('H08',[h('Томск, всего до 30 тысяч вместе с коммуналкой',TOMSK,pending=['budget'],other='коммунал',question='other')]),
 ('H09',[h('Томск, до 3000 рублей в сутки',TOMSK,other='сутки',question='other')]),
 ('H10',[h('Хочу купить комнату в Томске до 3 миллионов',TOMSK,other='купить',question='other')]),
 ('H11',[h('Живу в Туле, квартиру ищу в Томске до 30 тысяч',TOMSK,30000,requests=[hr()])]),
 ('H12',[deepcopy(HBASE)]),
 ('H13',[deepcopy(HBASE),h('А теперь только с кошкой',TOMSK,30000,other='кошк',question='other')]),
 ('H14',[deepcopy(HBASE),h('Нет, нужен Омск',OMSK,30000,requests=[hr('omsk')])]),
]

# Fixture expectations are explicit and independent of production matching.
RENTAL_CARDS={'H-good':'confirmed','H-over':'excluded','H-daily':'excluded','H-other-city':'excluded',
              'H-unknown-period':'unverified','H-unknown-type':'unverified','H-from':'unverified'}
WORK_CARDS={'W-city-conflict':'excluded','W-shift-conflict':'unverified','W-sparse':'unverified',
            'W-wrong-title':'excluded','W-age-conflict':'excluded','W-confirmed':'confirmed'}

def corpus():
    return [dict(id=i,branch=b,turns=deepcopy(turns),role='parent' if i in {'W03','W04'} else 'child')
            for b,rows in [('work',WORK),('rental',RENTAL)] for i,turns in rows]
