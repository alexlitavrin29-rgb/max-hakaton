"""Expectations recorded before v3 implementation; no live services here."""
import pytest
import asyncio,json
from types import SimpleNamespace
from project.llm.services.work import FIELDS, field, validate_fields
from project.llm.services.geography import resolve, resolve_legacy
from project.llm.services import money
from project.llm.services import flow
from project.llm.config import LLMConfigurationError
from project.admin.check_work_free import config


def empty_work_result():
    return dict(branch='work',work_fields={name:None for name in FIELDS},work_action=None,work_other=None)


def test_short_profession_and_city_bypass_llm_when_provider_is_unavailable(monkeypatch):
    async def forbidden_llm(*args, **kwargs):
        raise AssertionError('A simple "profession, city" request must not depend on LLM availability')

    monkeypatch.setattr(flow, 'call_llm', forbidden_llm)
    async def empty_search(*args, **kwargs):
        return SimpleNamespace(items=[],next_offset=None)
    monkeypatch.setattr(flow, 'search_vacancies', empty_search)

    async def run():
        bot = flow.FlowDialogue(flow.PreviewReminders(), config())
        await bot.handle(1, payload='jump:work')
        replies = await bot.handle(1, 'водитель, чита')
        state = bot.session(1).work
        assert state['fields']['query']['value'] == 'водитель'
        assert state['place']['name'] == 'Чита'
        assert state['awaiting'] is None
        assert all(bot.text('work_parse_error') not in item['text'] for item in replies)
        recognition = [item for item in bot.session(1).trace if item.get('kind') == 'condition_recognition']
        assert len(recognition) == 1
        assert recognition[0]['method'] == 'deterministic'
        assert recognition[0]['llm_call_count'] == 0

    asyncio.run(run())


WORK_FAST_PATH_CASES = [
    ('работа в Омске, мне 19 лет, зарплата от 60 тысяч рублей в месяц', 'Омск', 19, 60000),
    ('Мне 20 лет, город Томск, зарплата минимум 55 000 руб./мес.', 'Томск', 20, 55000),
    ('КАЗАНЬ, РЕСПУБЛИКА ТАТАРСТАН; ВОЗРАСТ 18; ЗАРПЛАТА ОТ 45 ТЫСЯЧ В МЕСЯЦ', 'Казань', 18, 45000),
    ('зарплата от 70 тысяч, мне 25, работа в Екатеринбурге', 'Екатеринбург', 25, 70000),
    ('Работа в Новосибирске — мне 21 год — зарплата не ниже 50 000 ежемесячно', 'Новосибирск', 21, 50000),
    ('Пермь, Пермский край, 22 года, зарплата от 65 тысяч в месяц', 'Пермь', 22, 65000),
    ('Мне 30. Ищу работу в Ижевске. Зарплата от 50 тыс. рублей в месяц', 'Ижевск', 30, 50000),
    ('Владивосток; возраст 24; минимальная зарплата 80 000 руб./мес.', 'Владивосток', 24, 80000),
    ('Калининград, мне 27, хочу зарплату от 75 тысяч ежемесячно', 'Калининград', 27, 75000),
    ('Мне 19, зарплата от 55 тысяч в месяц, город Челябинск', 'Челябинск', 19, 55000),
    ('Ростов-на-Дону — 23 года — зарплата минимум 60 000 в месяц', 'Ростов-на-Дону', 23, 60000),
    ('Нижний Новгород, мне 28 лет, зарплата от 70 тыс./мес.', 'Нижний Новгород', 28, 70000),
    ('Санкт-Петербург. Возраст 26. Зарплата не ниже 85 тысяч в месяц', 'Санкт-Петербург', 26, 85000),
    ('Сочи, Краснодарский край, мне 20, зарплата от 50 тысяч ежемесячно', 'Сочи', 20, 50000),
    ('Уфа, Республика Башкортостан, 31 год, зарплата минимум 65 000 рублей в месяц', 'Уфа', 31, 65000),
    ('Самара, Самарская область — мне 22 — зарплата от 58 тысяч в месяц', 'Самара', 22, 58000),
    ('Воронеж, мне 18 лет, зарплата от 40 тысяч рублей ежемесячно', 'Воронеж', 18, 40000),
    ('Рязань — возраст 25 лет — зарплата минимум 52 тыс. в месяц', 'Рязань', 25, 52000),
    ('Барнаул, Алтайский край, мне 23, зарплата от 48 000 рублей в месяц', 'Барнаул', 23, 48000),
    ('зарплата минимум 60000 в месяц; возраст 19; Омск', 'Омск', 19, 60000),
    ('19 лет, от 60 тысяч рублей в месяц, работа в Омске', 'Омск', 19, 60000),
    ('  оМсК !!! МНЕ 19 ; ЗАРПЛАТА ОТ 60 ТЫС. / МЕС. ', 'Омск', 19, 60000),
    ('Город: Томск. Возраст: 20. Минимальная зарплата: 55 000 рублей в месяц', 'Томск', 20, 55000),
    ('Ищу работу. Мне 24 года. Место — Владивосток. Зарплата — от 80 тысяч ежемесячно', 'Владивосток', 24, 80000),
]


@pytest.mark.parametrize('text,city,age,salary', WORK_FAST_PATH_CASES)
def test_unambiguous_work_input_bypasses_llm(monkeypatch, text, city, age, salary):
    calls = dict(llm=0, source=0)

    async def forbidden_llm(*args, **kwargs):
        calls['llm'] += 1
        raise AssertionError('LLM must not be called for deterministic work input')

    async def source(query, **kwargs):
        calls['source'] += 1
        assert kwargs['region_code'] == resolve_legacy(city)[0]['code']
        return SimpleNamespace(items=[], next_offset=None)

    monkeypatch.setattr(flow, 'call_llm', forbidden_llm)
    monkeypatch.setattr(flow, 'search_vacancies', source)

    async def run():
        bot = flow.FlowDialogue(flow.PreviewReminders(), config())
        session = bot.session(1)
        session.values['query'] = 'сварщик'
        bot.work.state(session)['fields']['query'] = field('known', 'сварщик', 'сварщик')
        await bot.handle(1, payload='jump:work')
        await bot.handle(1, text)
        state = bot.session(1).work
        assert calls == dict(llm=0, source=1)
        assert state['place']['name'] == city
        assert state['fields']['age']['value'] == age
        assert state['fields']['salary']['value']['lower'] == salary
        assert state['fields']['query']['value'] == 'сварщик'
        recognition = [item for item in bot.session(1).trace if item.get('kind') == 'condition_recognition']
        assert len(recognition) == 1 and recognition[0]['method'] == 'deterministic'
        metrics=recognition[0]
        assert metrics['branch']=='work' and metrics['recognition_method']=='deterministic'
        assert metrics['llm_call_count']==0 and metrics['location_call_count']==1 and metrics['source_call_count']==1
        assert metrics['source_pages']==1 and metrics['fetched_count']==0 and metrics['shown_count']==0
        assert all(metrics[name]>=0 for name in ('deterministic_ms','llm_ms','location_lookup_ms','source_search_ms','filter_render_ms'))
        assert set(metrics) >= {
            'channel','request_total_ms','queue_wait_ms','slot_wait_ms','llm_wait_ms',
            'location_wait_ms','source_wait_ms','delivery_queue_ms','delivery_http_ms',
            'delivery_total_ms','reply_count','concurrent_requests','cache_hit','cache_miss',
            'cache_eviction','safe_error_code',
        }
        assert metrics['reply_count'] >= 1 and metrics['safe_error_code'] is None

    asyncio.run(run())


@pytest.mark.parametrize('failure', ['empty', 'error', 'configuration'])
def test_simple_work_conditions_survive_empty_or_failed_llm(monkeypatch, failure):
    async def llm(*a,**k):
        if failure=='error':raise flow.LLMError('timeout')
        if failure=='configuration':raise LLMConfigurationError('missing key')
        return json.dumps(empty_work_result(),ensure_ascii=False)
    calls=[]
    async def source(q,**kw):calls.append((q,kw));return SimpleNamespace(items=[],next_offset=None)
    monkeypatch.setattr(flow,'call_llm',llm);monkeypatch.setattr(flow,'search_vacancies',source)
    async def run():
        bot=flow.FlowDialogue(flow.PreviewReminders(),config());await bot.handle(1,payload='jump:work')
        reply=await bot.handle(1,'работа в Омске, мне 19 лет, зарплата от 60 тысяч рублей в месяц, без опыта')
        state=bot.session(1).work
        assert state['place']['name']=='Омск'
        assert state['fields']['age']['value']==19
        assert state['fields']['salary']['value']['lower']==60000
        assert any(item.get('kind')=='condition_recognition' and item.get('method')=='deterministic_fallback'
                   for item in bot.session(1).trace)
        assert all('не получилось понять' not in item['text'].casefold() for item in reply)
    asyncio.run(run())


def test_work_deterministic_correction_preserves_other_conditions(monkeypatch):
    async def llm(*a,**k):return json.dumps(empty_work_result(),ensure_ascii=False)
    async def source(*a,**k):return SimpleNamespace(items=[],next_offset=None)
    monkeypatch.setattr(flow,'call_llm',llm);monkeypatch.setattr(flow,'search_vacancies',source)
    async def run():
        bot=flow.FlowDialogue(flow.PreviewReminders(),config());await bot.handle(1,payload='jump:work')
        await bot.handle(1,'работа в Омске, мне 19 лет, зарплата от 60 тысяч рублей в месяц')
        await bot.handle(1,'теперь мне 20 лет')
        state=bot.session(1).work
        assert state['place']['name']=='Омск' and state['fields']['salary']['value']['lower']==60000
        assert state['fields']['age']['value']==20
    asyncio.run(run())


@pytest.mark.parametrize('text', ['работа в Советске', 'город Советск'])
def test_work_ambiguous_place_requires_choice_with_empty_llm(monkeypatch, text):
    async def llm(*a,**k):return json.dumps(empty_work_result(),ensure_ascii=False)
    monkeypatch.setattr(flow,'call_llm',llm)
    async def run():
        bot=flow.FlowDialogue(flow.PreviewReminders(),config());await bot.handle(1,payload='jump:work')
        await bot.handle(1,text)
        state=bot.session(1).work
        assert state['place'] is None and len(state['choices'])>1
        assert 'city' in state['pending_fields']
    asyncio.run(run())


def test_work_unknown_place_is_not_guessed_with_empty_llm(monkeypatch):
    async def llm(*a,**k):return json.dumps(empty_work_result(),ensure_ascii=False)
    monkeypatch.setattr(flow,'call_llm',llm)
    async def run():
        bot=flow.FlowDialogue(flow.PreviewReminders(),config());await bot.handle(1,payload='jump:work')
        await bot.handle(1,'работа в Несуществующийграде')
        state=bot.session(1).work
        assert state['place'] is None and not state['choices']
        assert 'city' in state['pending_fields']
    asyncio.run(run())


@pytest.mark.parametrize('text,expected',[
    ('зарплата от 70.000 рублей в месяц',70000),
    ('от ста двадцати пяти тысяч рублей в месяц',125000),
    ('от 70 тысяч 500 рублей в месяц',70500),
    ('от 70,5 тысячи рублей в месяц',70500),
])
def test_v3_money_amounts(text,expected):
    raw=field('known',dict(kind='min',lower=expected,upper=None,period='month',tax='unknown'),text)
    parsed=validate_fields({'salary':raw},text,'child')
    assert parsed['salary']['status']=='known'
    assert parsed['salary']['value']['lower']==expected


def test_small_settlement_is_in_directory():
    places=resolve('Мельниково','Томская область')
    assert any(p['name']=='Мельниково' and p.get('source_id') and p.get('type') for p in places)


def test_salary_preposition_is_not_a_thousand_unit():
    value,issue=money.parse('Вернём требование к зарплате: примерно 58707 рублей в месяц')
    assert issue is None
    assert value['kind']=='target' and value['lower']==58707
    value,issue=money.parse('от 70к рублей в месяц')
    assert issue is None and value['lower']==70000


def test_control_oracle_compares_schedule_condition_not_label():
    from project.admin.work_control_oracle import same_value
    assert same_value('schedule','2/2','график 2/2')
    assert not same_value('schedule','2/3','график 2/2')
    assert not same_value('schedule','ночной 2/2','график 2/2')


def test_missing_model_age_value_uses_only_unambiguous_explicit_number():
    b=flow.FlowDialogue(flow.PreviewReminders(),config());s=b.session(1)
    b.work.apply(s,{'age':field('known',None,'Мне 26')},'Мне 26')
    assert s.values['age']==26
    b.work.apply(s,{'age':field('known',None,'26 или 27')},'26 или 27')
    assert s.values['age']==26 and 'age' in s.work['pending_fields']


@pytest.mark.parametrize('name,evidence',[('query','реставратор тканей'),('city','Весьегонск, Тверская область')])
def test_named_condition_with_missing_model_value_requires_clarification(name,evidence):
    b=flow.FlowDialogue(flow.PreviewReminders(),config());s=b.session(1)
    b.work.apply(s,{name:field('known',None,evidence)},evidence)
    assert name in s.work['pending_fields']
    assert s.work['pending_fields'][name]['evidence']==evidence


def test_explicit_settlement_type_disambiguates_and_typos_require_confirmation():
    assert len(resolve('город Москва'))==1
    assert len(resolve('г.Москва'))==1
    assert all(p['matched']=='suggestion' for p in resolve('Томкс'))


def test_profession_case_and_specialization_are_preserved():
    text='хочу работать медицинской сестрой-анестезистом'
    parsed=validate_fields({'query':field('known','медицинская сестра-анестезист','медицинской сестрой-анестезистом')},text,'child')
    assert parsed['query']['status']=='known'

@pytest.mark.parametrize('text,amount,issue',[
 ('от полутора тысяч рублей за смену',1500,None),
 ('минус 5000 рублей в месяц',-5000,'invalid'),
 ('от семидесяти пяти целых пяти десятых тысячи рублей в месяц',75500,None),
 ('ноль рублей в месяц',0,'invalid'),
 ('от 70000000 рублей в месяц',70000000,'confirm'),
 ('70 рублей',70,'period'),
 ('70',70,'scale'),
])
def test_money_never_silently_changes_unusual_amount(text,amount,issue):
    value,actual_issue=money.parse(text)
    assert actual_issue==issue
    if value:assert value['lower']==amount


def test_partial_money_preserves_other_fields_and_validates_model_uncertainty(monkeypatch):
    responses=[{'city':field('known','Томск','Томск'),'age':field('known',26,'26'),'query':field('known','сварщик','сварщик'),
                'salary':field('known',70,'хочу 70')},
               {'salary':field('clarify',{'lower':70},'70 тысяч')},{'salary':field('known',{},'в месяц')},
               {'salary':field('known',{},'это минимум')}]
    async def llm(*a,**k):return json.dumps(dict(branch='work',work_fields=responses.pop(0)))
    calls=[]
    async def source(q,**kw):calls.append((q,kw));return SimpleNamespace(items=[],next_offset=None)
    monkeypatch.setattr(flow,'call_llm',llm);monkeypatch.setattr(flow,'search_vacancies',source)
    async def run():
        b=flow.FlowDialogue(flow.PreviewReminders(),config());await b.handle(1,'Мне 26, Томск, сварщик, хочу 70')
        assert b.session(1).values['age']==26 and not calls
        await b.handle(1,'70 тысяч');assert not calls
        assert b.session(1).work['pending_fields']['salary']['value']['lower']==70000
        await b.handle(1,'в месяц')
        assert not calls
        await b.handle(1,'это минимум')
        assert b.session(1).values['salary']['lower']==70000 and calls[-1][0]=='сварщик'
    asyncio.run(run())


def test_multiple_places_and_professions_are_separate_source_queries(monkeypatch):
    async def llm(*a,**k):return json.dumps(dict(branch='work',work_fields={
        'city':field('known',['Томск','Омск'],'и в Томске, и в Омске'),
        'query':field('known',['повар','пекарь'],'и поваром, и пекарем'),
        'age':field('known',26,'26')}))
    calls=[]
    async def source(q,**kw):calls.append((q,kw['region_code']));return SimpleNamespace(items=[],next_offset=None)
    monkeypatch.setattr(flow,'call_llm',llm);monkeypatch.setattr(flow,'search_vacancies',source)
    async def run():
        b=flow.FlowDialogue(flow.PreviewReminders(),config())
        await b.handle(1,'Мне 26, ищу и поваром, и пекарем, и в Томске, и в Омске')
        for _ in range(3):
            await b.handle(1,payload='jump:work_more')
        assert set(calls)=={(q,c) for q in ['повар','пекарь'] for c in ['7000000000000','5500000000000']}
    asyncio.run(run())


def test_foreign_currency_is_never_assumed_to_be_rubles():
    value,issue=money.parse('от 2300 долларов в месяц')
    assert issue=='currency' and value is None


def test_evidence_cannot_remove_minus_sign_and_release_confirmed_condition():
    b=flow.FlowDialogue(flow.PreviewReminders(),config());s=b.session(1)
    b.work.apply(s,{'salary':field('known',{},'от 51000 рублей в месяц')},'от 51000 рублей в месяц')
    before=s.values['salary'].copy()
    b.work.apply(s,{'salary':field('known',{},'минус 65000 рублей в месяц')},'минус 65000 рублей в месяц')
    assert s.values['salary']==before
    b.work.apply(s,{'salary':field('any',None,'65000')},'65000')
    assert s.values['salary']==before
    b.work.apply(s,{'salary':field('known',{},'от 75000 рублей в месяц')},'от -75000 рублей в месяц')
    assert s.values['salary']==before


def test_parent_can_explicitly_search_for_self_without_changing_role():
    b=flow.FlowDialogue(flow.PreviewReminders(),config());s=b.session(1);s.values['role']='parent'
    b.work.apply(s,{'age':field('known',41,'мне 41')},'Ищу работу для себя, мне 41')
    assert s.values['age']==41 and s.values['role']=='parent'


def test_parent_searching_for_self_skips_age_question():
    b=flow.FlowDialogue(flow.PreviewReminders(),config());s=b.session(1);s.values['role']='parent'
    b.work.apply(s,{},'Ищу работу для себя')
    node=next(n for n in b.config['nodes'] if n['id']=='work_age')
    reply=b.work.question(s,node)
    assert reply == node['next']


def test_multiple_places_cannot_use_model_invented_regions():
    b=flow.FlowDialogue(flow.PreviewReminders(),config());s=b.session(1)
    b.work.apply(s,{'city':field('known',['Советск, Тульская область','Советск, Кировская область'],'оба Советска')},'оба Советска')
    assert not s.work.get('selected_places') and 'city' in s.work['pending_fields']


def test_confirming_both_professions_uses_previous_explicit_alternatives():
    b=flow.FlowDialogue(flow.PreviewReminders(),config());s=b.session(1)
    first={**field('clarify',None,'повар или пекарь'),'alternatives':['повар','пекарь']}
    b.work.apply(s,{'query':first},'повар или пекарь')
    b.work.apply(s,{'query':field('known',['повар','пекарь'],'оба')},'оба')
    assert s.values['query']==['повар','пекарь']


def test_model_cannot_disambiguate_city_by_inventing_settlement_type():
    b=flow.FlowDialogue(flow.PreviewReminders(),config());s=b.session(1)
    b.work.apply(s,{'city':field('known','город Москва','Москва')},'Москва')
    assert 'city' in s.work['pending_fields'] and len(s.work['choices'])>1


@pytest.mark.parametrize('place_type,expected_type', [('город','Город'),('посёлок','Поселок')])
@pytest.mark.parametrize('evidence_prefix',['','type','whole'])
def test_explicit_place_type_survives_truncated_model_evidence(place_type,expected_type,evidence_prefix):
    b=flow.FlowDialogue(flow.PreviewReminders(),config());s=b.session(1)
    value='Березовский, Свердловская область'
    text=f'Выбираю {place_type} {value}'
    evidence=text if evidence_prefix=='whole' else f'{place_type} {value}' if evidence_prefix=='type' else value
    b.work.apply(s,{'city':field('known',value,evidence)},text)
    assert not s.work['pending_fields']
    assert s.work['place']['type']==expected_type
    assert s.values['region_code']=='6600000000000'


def test_specialization_cannot_disappear_during_normalization():
    from project.llm.services.occupations import normalize
    assert normalize('слесарь','слесарем по ремонту автомобилей') is None
    assert normalize('медицинская сестра','медицинской сестрой-анестезистом') is None
    assert normalize('слесарь по ремонту автомобилей','хочу работать слесарем по ремонту автомобилей') is not None


def test_profession_request_heading_is_not_a_qualification():
    from project.llm.services.occupations import normalize
    assert normalize('техник по метрологии','Новая желаемая должность: техник по метрологии')=='техник по метрологии'
    assert normalize('техник','Новая желаемая должность: техник по метрологии') is None


def test_same_name_without_confirmed_region_is_not_verified():
    b=flow.FlowDialogue(flow.PreviewReminders(),config());s=b.session(1)
    place=resolve('Томск')[0]
    job=SimpleNamespace(city='Томск',address='Томск',region=None)
    reason,missing=b.work.assess_job(s,job,{},place)
    assert reason is None and missing


def test_source_full_text_match_does_not_prove_profession_from_different_title():
    b=flow.FlowDialogue(flow.PreviewReminders(),config());s=b.session(1)
    job=SimpleNamespace(title='Уборщик производственных помещений',city=None,address=None,region=None)
    reason,missing=b.work.assess_job(s,job,{'query':'электрогазосварщик'},None)
    assert reason and 'професси' in reason


def test_bare_amount_does_not_silently_become_minimum():
    value,issue=money.parse('73500 рублей в месяц')
    assert issue=='kind' and value['kind']=='unspecified'
    clarified,issue=money.parse('это максимум',value)
    assert issue is None and clarified['kind']=='max' and clarified['upper']==73500 and clarified['lower'] is None


def test_model_cannot_drop_comparison_before_salary_evidence():
    b=flow.FlowDialogue(flow.PreviewReminders(),config());s=b.session(1)
    b.work.apply(s,{'salary':field('known',{},'75000 рублей в месяц')},'хочу до 75000 рублей в месяц')
    assert s.values['salary']['kind']=='max' and s.values['salary']['upper']==75000


def test_salary_correction_keeps_bound_and_tax_only_reply_keeps_amount():
    previous,_=money.parse('от 53000 рублей в месяц')
    changed,issue=money.parse('не 53, а 67 тысяч',previous)
    assert issue is None and changed['kind']=='min' and changed['lower']==67000
    changed,issue=money.parse('на руки',changed)
    assert issue is None and changed['lower']==67000 and changed['tax']=='net'


@pytest.mark.parametrize('phrase,expected',[
    ('подработка','part_time'),('частичная занятость','part_time'),
    ('полная занятость','full_time'),('временная работа','temporary'),
])
def test_employment_cannot_become_profession(phrase,expected):
    b=flow.FlowDialogue(flow.PreviewReminders(),config());s=b.session(1)
    b.work.apply(s,{'query':field('known','сварщик','сварщик')},'сварщик')
    b.work.apply(s,{'query':field('known',phrase,phrase)},phrase)
    assert s.values['query']=='сварщик'
    assert s.values['employment']==expected


def test_contextual_salary_reply_uses_only_current_text_and_validated_prior():
    b=flow.FlowDialogue(flow.PreviewReminders(),config());s=b.session(1)
    b.work.apply(s,{'salary':field('known',{},'от 63000 рублей')},'от 63000 рублей')
    b.work.problem(s)
    # Real failure class: the model joins old and new text into invented evidence/value.
    b.work.apply(s,{'salary':field('known',{'lower':99000},'от 99000 рублей в месяц')},'в месяц')
    assert s.values['salary']['lower']==63000 and s.values['salary']['period']=='month'
    assert not s.work['pending_fields']


@pytest.mark.parametrize('text',['не в месяц','через месяц','Томск','отпуск на неделю'])
def test_unrelated_reply_cannot_resolve_salary_period(text):
    b=flow.FlowDialogue(flow.PreviewReminders(),config());s=b.session(1)
    b.work.apply(s,{'salary':field('known',{},'от 63000 рублей')},'от 63000 рублей')
    b.work.problem(s)
    b.work.apply(s,{'salary':field('known',{},'от 63000 рублей в месяц')},text)
    assert s.work['pending_fields']['salary']['value']['period'] is None


@pytest.mark.parametrize('name,value,evidence',[('age','23','мне 23'),('experience','2','опыт 2 года')])
def test_integer_serialized_as_string_is_still_grounded(name,value,evidence):
    b=flow.FlowDialogue(flow.PreviewReminders(),config());s=b.session(1)
    b.work.apply(s,{name:field('known',value,evidence)},evidence)
    assert s.values[name]==int(value) and not s.work['pending_fields']
    b.work.apply(s,{name:field('known','99',evidence)},evidence)
    assert s.values[name]==int(value) and name in s.work['pending_fields']


def test_dictionary_normalizes_inflected_profession_without_losing_specialization():
    from project.llm.services.occupations import normalize
    assert normalize('ветеринарным фельдшером','ветеринарным фельдшером')=='ветеринарный фельдшер'


def test_occupation_correction_distinguishes_old_and_new_qualifications():
    from project.llm.services.occupations import normalize
    assert normalize('пекарем','Не поваром, а пекарем')=='пекарь'
    assert normalize('слесарь','не поваром, а слесарем по ремонту автомобилей') is None


@pytest.mark.parametrize('name,status,text',[('age','declined','Не хочу указывать возраст'),('salary','any','Зарплата неважна')])
def test_explicit_refusal_or_release_is_not_an_ambiguous_exclusion(name,status,text):
    b=flow.FlowDialogue(flow.PreviewReminders(),config());s=b.session(1)
    b.work.apply(s,{name:{**field(status,None,text),'intent':'excluded'}},text)
    assert s.work['fields'][name]['status']==status and not s.work['pending_fields']


def test_previous_occupations_do_not_imply_a_degree_or_duration():
    b=flow.FlowDialogue(flow.PreviewReminders(),config());s=b.session(1)
    b.work.apply(s,{
        'education_level':{**field('known','vocational','учился на повара'),'intent':'previous'},
        'experience':{**field('known',None,'работал продавцом'),'intent':'previous'},
    },'Раньше работал продавцом, учился на повара')
    assert not s.work['pending_fields']
    assert s.work['fields']['education_level']['status']==s.work['fields']['experience']['status']=='unknown'


def test_explicit_but_unclear_experience_duration_still_needs_clarification():
    b=flow.FlowDialogue(flow.PreviewReminders(),config());s=b.session(1)
    b.work.apply(s,{'experience':field('known',None,'стаж несколько лет')},'стаж несколько лет')
    assert 'experience' in s.work['pending_fields']


def test_source_experience_greater_than_applicant_age_cannot_be_confirmed():
    b=flow.FlowDialogue(flow.PreviewReminders(),config());s=b.session(1)
    job=SimpleNamespace(experience='25')
    reason,missing=b.work.assess_job(s,job,{'age':20},None)
    assert reason or missing


def test_local_address_with_countrywide_work_does_not_confirm_selected_place():
    b=flow.FlowDialogue(flow.PreviewReminders(),config());s=b.session(1)
    place=resolve('Томск')[0]
    job=SimpleNamespace(city=None,address='Томская область, г Томск. Работа вахтовым методом по России!',region='Томская область')
    reason,missing=b.work.assess_job(s,job,{},place)
    assert reason is None and any('мест' in m for m in missing)


def test_age_experience_conflict_preserves_previous_experience_until_clarified():
    b=flow.FlowDialogue(flow.PreviewReminders(),config());s=b.session(1)
    b.work.apply(s,{'age':field('known',50,'50'),'experience':field('known',30,'30')},'мне 50, стаж 30')
    b.work.apply(s,{'age':field('known',20,'20')},'исправлю возраст: 20')
    assert b.work.problem(s)
    assert s.values['experience']==30 and 'experience' in s.work['pending_fields']


def test_profession_evidence_can_include_separately_extracted_city():
    b=flow.FlowDialogue(flow.PreviewReminders(),config());s=b.session(1)
    b.work.apply(s,{'query':field('known','сварщик','томск сварщик'),
                   'city':field('known','Томск','томск')},'томск сварщик')
    assert s.values['query']=='сварщик' and not s.work['pending_fields']


@pytest.mark.parametrize('text,lower,upper,issue',[
 ('от двух до трех миллионов рублей в месяц',2000000,3000000,'confirm'),
 ('минус пять тысяч рублей в месяц',-5000,None,'invalid'),
 ('от 47тыс до 69тыс рублей в месяц',47000,69000,None),
 ('от 2,75 тысячи рублей за смену',2750,None,None),
 ('от восьмидесяти трех косарей в месяц',83000,None,None),
 ('от 0 тысяч рублей в месяц',0,None,'invalid'),
])
def test_money_grammar_scale_sign_and_decimal(text,lower,upper,issue):
    value,error=money.parse(text)
    assert error==issue
    assert (value['lower'],value['upper'])==(lower,upper)
