"""Fresh control supplement; preserve v1 and its actual results unchanged."""
import json,hashlib
from pathlib import Path

def main():
    root=Path('docs/test-results/work-free-input');path=root/'control-v2.json'
    if path.exists():raise SystemExit('Frozen file exists')
    cases=json.loads((root/'control-v1.json').read_text(encoding='utf-8'))
    for case in cases:
        # A shared development occupation invalidated novelty of these cases.
        if case['expected']['query']=='ветеринарный фельдшер':
            case['replaces']=case['id'];case['id']='replacement-'+case['id']
            case['messages']=[t.replace('ветеринарный фельдшер','инженер по лазерным системам') for t in case['messages']]
            case['expected']['query']='инженер по лазерным системам'
    salary_cases=[r for r in cases if r['group']=='salary']
    samples=[
      ('от 68.240 рублей в месяц',68240,'month','unknown'),
      ('от 91 375 рублей в месяц',91375,'month','unknown'),
      ('от 74к в месяц',74000,'month','unknown'),
      ('от девяноста двух тысяч восьмисот рублей в месяц',92800,'month','unknown'),
      ('от 83 тысяч 225 рублей в месяц',83225,'month','unknown'),
      ('от 82,75 тысячи рублей в месяц',82750,'month','unknown'),
      ('от 4,25 тысячи рублей за смену',4250,'shift','unknown'),
      ('от 385 рублей в час',385,'hour','unknown'),
      ('от 22750 рублей в неделю',22750,'week','unknown'),
      ('от 3150 рублей в день',3150,'day','unknown'),
      ('от 94 косарей в месяц',94000,'month','unknown'),
      ('от 81 штуки в месяц',81000,'month','unknown'),
      ('от 88550 рублей в месяц на руки',88550,'month','net'),
      ('от 96320 рублей в месяц до вычета налогов',96320,'month','gross'),
      ('от ста сорока трех тысяч рублей в месяц',143000,'month','unknown'),
      ('от 2,35 тысячи рублей за смену',2350,'shift','unknown'),
      ('от 58тысяч рублей в месяц',58000,'month','unknown'),
      ('от шестидесяти семи целых трех десятых тысячи рублей в месяц',67300,'month','unknown'),
      ('от 76400 руб. в месяц',76400,'month','unknown'),
      ('от 79250 ₽ в месяц',79250,'month','unknown'),
    ]
    for case,(phrase,amount,period,tax) in zip(salary_cases,samples):
        case['replaces']=case.get('replaces',case['id']);case['id']='fresh-'+case['id']
        case['messages'][0]=case['messages'][0].split('теперь хочу ')[0]+'теперь хочу '+phrase
        case['expected']['salary'].update(lower=amount,period=period,tax=tax)
    edges=[
      ('Кировск','Мурманская область','51','маркшейдер'),
      ('Березовский','Свердловская область','66','техник-протезист'),
      ('Железногорск','Курская область','46','техник по наладке испытательного оборудования'),
      ('Мирный','Саха /Якутия/ Республика','14','инженер по автоматизации производства'),
    ]
    for i,(city,region,code,query) in enumerate(edges):
        salary=dict(kind='min',lower=101200+i*1230,upper=None,period='month',tax='unknown',currency='RUB')
        known=dict(query=query,age=26,salary=salary)
        expected={**known,'city':city,'region_code':code+'0'*11}
        cases.append(dict(id=f'fresh-ambiguity-{i+1}',group='ambiguity',
            messages=[f'Мне 26. Ищу работу: {query}. Место: {city}. Зарплата от {salary["lower"]} рублей в месяц',
                      f'Выбираю город {city}, {region}'],expected=expected,
            steps=[dict(values=known,pending=['city'],awaiting='city',search=False),dict(values=expected,pending=[],awaiting=None,search=True)]))
    path.write_text(json.dumps(cases,ensure_ascii=False,indent=2),encoding='utf-8')
    path.with_suffix('.sha256').write_text(hashlib.sha256(path.read_bytes()).hexdigest(),encoding='ascii')
    print(json.dumps(dict(cases=len(cases),replaced=sum('replaces' in c for c in cases),live_executed=0)))

if __name__=='__main__':main()
