"""Critical regressions specified before implementation; no live paid calls."""
import asyncio
import json
from types import SimpleNamespace

import pytest

from project.admin.check_work_free import config
from project.admin.tests.test_rental import engine, fields, page, row, CITY
from project.llm.services import flow, rental
from project.llm.services.work import assess, field, validate_fields
from project.llm.services.geography import resolve


def job(**changes):
    return SimpleNamespace(**(dict(id='test',title='сварщик',company='Fixture',city='Томск',address='Томск',
        region='Томская область',salary_from=70000,salary_to=80000,salary_period='month',salary_tax=None,
        experience='0',education='Среднее профессиональное',employment='Частичная занятость',
        schedule='2/2 сменный',accommodation=True,requirements='Возраст от 18 лет',responsibilities=None,
        url='https://example.invalid/test')|changes))


def test_exact_shift_conflict():
    assert assess(job(schedule='3/3 сменный'),dict(schedule='2/2 сменный'),None)[0]


def test_city_does_not_hide_conflicting_address():
    assert assess(job(address='Москва'),{},resolve('Томск')[0])[0]


def test_age_requirement_is_compared_and_missing_is_named():
    b=flow.FlowDialogue(flow.PreviewReminders(),config());s=b.session(1)
    assert b.work.assess_job(s,job(requirements='Возраст от 21 года'),dict(age=20),None)[0]
    assert 'возраст' in ' '.join(b.work.assess_job(s,job(requirements=None),dict(age=20),None)[1]).lower()


@pytest.mark.parametrize('text',['не высшее образование','высшего образования нет','нет высшего образования'])
def test_negated_education_never_becomes_degree(text):
    result=validate_fields({'education_level':field('known','higher',text)},text,'child')
    assert result['education_level']['status']=='clarify'


def test_parent_negated_self_target():
    b=flow.FlowDialogue(flow.PreviewReminders(),config());s=b.session(1);s.values['role']='parent'
    text='Ищу работу для дочери, а для себя не ищу. Дочери 19, Томск'
    b.work.apply(s,dict(age=field('known',19,'Дочери 19'),city=field('known','Томск','Томск')),text)
    assert s.work.get('applicant')=='child'
    assert s.values['age']==19


def test_omitted_other_still_blocks_rental_search(monkeypatch):
    calls=[]
    async def llm(*a,**k):return json.dumps(fields('Томск','до 30 тысяч'))
    async def locations(q):return [CITY]
    async def search(*args):calls.append(args);return page([row()])
    monkeypatch.setattr(rental,'call_llm',llm)
    monkeypatch.setattr(rental.reefapi,'locations',locations)
    monkeypatch.setattr(rental.reefapi,'search',search)
    async def run():
        b=engine();await b.handle(1,payload='jump:rental')
        replies=await b.handle(1,'Томск до 30 тысяч без залога')
        assert not calls
        assert 'без залога' in b.session(1).rental['other']
        assert 'без залога' in '\n'.join(r['text'] for r in replies)
    asyncio.run(run())


def test_unrelated_title_is_excluded():
    b=flow.FlowDialogue(flow.PreviewReminders(),config());s=b.session(1)
    assert b.work.assess_job(s,job(title='повар'),dict(query='сварщик'),None)[0]


def test_missing_salary_period_never_confirms_amount():
    from project.llm.services.money import assess as assess_pay
    from project.admin.tests.quality_oracles import pay
    assert assess_pay(job(salary_period=None),pay(70000))[1]


def test_scale_only_answer_uses_pending_number():
    b=flow.FlowDialogue(flow.PreviewReminders(),config());s=b.session(1)
    b.work.apply(s,{'salary':field('known',None,'хочу 70')},'хочу 70')
    b.work.apply(s,{'salary':field('known',None,'Тысяч за месяц, минимум')},'Тысяч за месяц, минимум')
    assert s.work['fields']['salary']['value']['lower']==70000


def test_ungrounded_city_is_pending_not_silently_lost():
    b=flow.FlowDialogue(flow.PreviewReminders(),config());s=b.session(1)
    b.work.apply(s,{'city':field('known','Тула','Тула')},'в Туле')
    assert 'city' in s.work['pending_fields']


def test_other_person_does_not_inherit_parent_age():
    b=flow.FlowDialogue(flow.PreviewReminders(),config());s=b.session(1);s.values['role']='parent'
    b.work.apply(s,{'age':field('known',43,'Мне 43')},'Для себя ищу работу. Мне 43')
    assert s.values['age']==43
    b.work.apply(s,{},'Теперь ищу для дочери')
    assert s.work['fields']['age']['status']=='unknown'


@pytest.mark.parametrize('name,text',[
    ('age','Мне 23'),('education_level','После колледжа'),('experience','Без опыта'),
    ('employment','Подработка'),('schedule','График 2/2'),('salary','От 80 тысяч в месяц'),('housing','Нужно жильё')])
def test_null_cannot_silently_drop_named_work_condition(name,text):
    b=flow.FlowDialogue(flow.PreviewReminders(),config());s=b.session(1)
    b.work.apply(s,{name:None},text)
    assert name in s.work['pending_fields'] or s.work['fields'][name]['status']=='known'


@pytest.mark.parametrize('period',['hour','shift','week','month'])
@pytest.mark.parametrize('kind',['min','max','range','target'])
def test_salary_bounds_periods_and_taxes_not_assumed(period,kind):
    from project.llm.services.money import assess as assess_pay
    from project.admin.tests.quality_oracles import pay
    desired=pay(1000,kind=kind,period=period,tax='net',upper=2000 if kind=='range' else None)
    assert assess_pay(job(salary_from=1000,salary_to=1000,salary_period=None,salary_tax=None),desired)[1]


def test_new_rental_constraint_requires_new_consent_and_preserves_prior(monkeypatch):
    responses=[fields('Томск','до 30 тысяч'),fields(other='с кошкой')]
    calls=[]
    async def llm(*a,**k):return json.dumps(responses.pop(0))
    async def locations(q):return [CITY]
    async def search(*a):calls.append(a);return page([row()])
    monkeypatch.setattr(rental,'call_llm',llm);monkeypatch.setattr(rental.reefapi,'locations',locations);monkeypatch.setattr(rental.reefapi,'search',search)
    async def run():
        from project.admin.tests.test_flow import buttons
        b=engine();await b.handle(1,payload='jump:rental')
        r=await b.handle(1,'Томск до 30 тысяч без залога')
        assert not calls
        await b.handle(1,payload=next(x['payload'] for x in buttons(r) if 'Искать только' in x['text']))
        assert len(calls)==1
        await b.handle(1,'А теперь с кошкой')
        assert len(calls)==1 and not b.session(1).rental['other_accepted']
        assert 'залога' in b.session(1).rental['other'] and 'кошкой' in b.session(1).rental['other']
    asyncio.run(run())


def test_unsupported_work_condition_blocks_until_explicit_consent(monkeypatch):
    calls=[]
    async def llm(*a,**k):
        return json.dumps(dict(branch='work',work_fields=dict(age=field('known',20,'Мне 20'),city=field('known','Томск','Томск')),work_other=None))
    async def source(*a,**kw):calls.append((a,kw));return SimpleNamespace(items=[],next_offset=None)
    monkeypatch.setattr(flow,'call_llm',llm);monkeypatch.setattr(flow,'search_vacancies',source)
    async def run():
        from project.admin.tests.test_flow import buttons
        b=flow.FlowDialogue(flow.PreviewReminders(),config())
        r=await b.handle(1,'Мне 20, Томск, только официальное оформление')
        assert not calls and 'официальное оформление' in b.session(1).work['other']
        await b.handle(1,payload=next(x['payload'] for x in buttons(r) if 'доступным' in x['text']))
        assert len(calls)==1
        assert b.session(1).work['other_accepted']
    asyncio.run(run())


@pytest.mark.parametrize('name',['query','city','age','education_level','experience','employment','schedule','salary','housing'])
def test_release_and_restore_one_of_nine_fields_preserves_others(name):
    from copy import deepcopy
    values=dict(query=('сварщик','сварщик'),city=('Томск','Томск'),age=(20,'Мне 20'),
                education_level=('vocational','после колледжа'),experience=(0,'без опыта'),
                employment=('part_time','подработка'),schedule=('2/2','2/2'),
                salary=(None,'от 70 тысяч рублей в месяц'),housing=(True,'нужно жильё'))
    b=flow.FlowDialogue(flow.PreviewReminders(),config());s=b.session(1)
    text=', '.join(e for v,e in values.values())
    b.work.apply(s,{n:field('known',v,e) for n,(v,e) in values.items()},text)
    before=deepcopy(s.work['fields'])
    removal='по всей России' if name=='city' else 'Не хочу указывать возраст' if name=='age' else 'Это условие неважно'
    b.work.apply(s,{name:field('declined' if name=='age' else 'any',None,removal)},removal)
    assert s.work['fields'][name]['status'] in {'any','declined'}
    assert {k:v for k,v in s.work['fields'].items() if k!=name}=={k:v for k,v in before.items() if k!=name}
    value,evidence=values[name]
    b.work.apply(s,{name:field('known',value,evidence)},evidence)
    assert s.work['fields']==before and not s.work['pending_fields']


def test_truncated_evidence_does_not_remove_negation():
    result=validate_fields({'education_level':field('known','higher','высшее образование')},'не высшее образование','child')
    assert result['education_level']['status']=='clarify'
    result=validate_fields({'schedule':field('known','2/2','2/2')},'только не 2/2','child')
    assert result['schedule']['status']=='clarify'


def test_salary_evidence_must_include_explicit_tax_and_period():
    b=flow.FlowDialogue(flow.PreviewReminders(),config());s=b.session(1)
    b.work.apply(s,{'salary':field('known',None,'от 1500 рублей')},'от 1500 рублей за смену на руки')
    item=s.work['fields']['salary']
    # Either preserve all stated details or block for clarification; no monthly default.
    assert (item['status']=='known' and item['value']['period']=='shift' and item['value']['tax']=='net') or 'salary' in s.work['pending_fields']


def test_housing_rejection_does_not_become_requested_housing():
    result=validate_fields({'housing':field('known',True,'с жильём')},'не с жильём','child')
    assert result.get('housing',{}).get('status')!='known'


def test_exclusive_profession_selection_is_not_a_specialization():
    from project.llm.services.occupations import normalize
    assert normalize('токарь','Только токарь')=='токарь'


def test_noncontiguous_other_cannot_erase_grounded_rental_city(monkeypatch):
    async def llm(*a,**k):return json.dumps(dict(city=dict(value='Томск',evidence='в Томске'),budget=None,other='купить комнату до 3 миллионов',action=None))
    async def locations(q):return [CITY]
    async def search(*a):raise AssertionError('must not search rentals for purchase')
    monkeypatch.setattr(rental,'call_llm',llm);monkeypatch.setattr(rental.reefapi,'locations',locations);monkeypatch.setattr(rental.reefapi,'search',search)
    async def run():
        b=engine();await b.handle(1,payload='jump:rental')
        await b.handle(1,'Хочу купить комнату в Томске до 3 миллионов')
        assert b.session(1).rental['city']==CITY
        assert b.session(1).rental['budget'] is None
        assert 'купить' in b.session(1).rental['other']
    asyncio.run(run())


@pytest.mark.parametrize('bad',[[],{},False,123])
def test_schema_rejects_malformed_subject_without_crashing(bad):
    from project.llm.services.work_schema import valid_structure
    assert not valid_structure(dict(work_fields=dict(city={**field('known','Томск','Томск'),'subject':bad})))
