"""Shared engine guarantees with controlled model/source; these are NOT live quality scores."""
import asyncio
import itertools
import json
from copy import deepcopy
from types import SimpleNamespace
import pytest
from project.admin.check_work_free import config
from project.llm.services import flow
from project.llm.services.work import field
from project.llm.integrations.trudvsem import TrudvsemError

PARTS={'city':('в Томске','Томск'),'query':('электрогазосварщик','электрогазосварщик'),
       'salary':('от 73500 рублей в месяц',{})}

@pytest.mark.parametrize('order',list(itertools.permutations(PARTS)))
@pytest.mark.parametrize('split',[False,True])
def test_all_orders_keep_conditions_and_only_ask_missing(monkeypatch,order,split):
    queue=[];calls=[]
    async def llm(*a,**kw):return json.dumps(dict(branch='work',work_fields=queue.pop(0)),ensure_ascii=False)
    async def source(q,**kw):calls.append((q,kw));return SimpleNamespace(items=[],next_offset=None)
    monkeypatch.setattr(flow,'call_llm',llm);monkeypatch.setattr(flow,'search_vacancies',source)
    async def run():
        b=flow.FlowDialogue(flow.PreviewReminders(),config());await b.handle(1,payload='jump:work')
        groups=[[k] for k in order] if split else [order]
        known={}
        for i,group in enumerate(groups):
            raw={k:field('known',PARTS[k][1],PARTS[k][0]) for k in group}
            text=', '.join(PARTS[k][0] for k in group)+', это важно'
            if i==0:raw['age']=field('known',23,'23');text+=' мне 23'
            queue.append(raw);await b.handle(1,text)
            s=b.session(1)
            for k,v in known.items():assert s.values[k]==v
            known=deepcopy({k:s.values[k] for k in PARTS if k in s.values})
            if 'city' not in known:assert not calls
            assert s.work['awaiting'] not in known
        assert calls[-1][0]=='электрогазосварщик'
        assert calls[-1][1]['region_code']=='7000000000000'
        assert b.session(1).values['salary']['lower']==73500
    asyncio.run(run())


@pytest.mark.parametrize('failure',['llm','api'])
def test_separate_failures_preserve_conditions_and_retry(monkeypatch,failure):
    calls=[];broken=False
    async def llm(*a,**kw):
        if broken:raise flow.LLMError('timeout')
        return json.dumps(dict(branch='work',work_fields={'city':field('known','Томск','Томск'),
            'query':field('known','сварщик','сварщик'),'age':field('known',24,'24')}))
    async def source(q,**kw):
        calls.append(kw['offset'])
        if failure=='api':raise TrudvsemError('timeout')
        return SimpleNamespace(items=[],next_offset=None)
    monkeypatch.setattr(flow,'call_llm',llm);monkeypatch.setattr(flow,'search_vacancies',source)
    async def run():
        nonlocal broken
        b=flow.FlowDialogue(flow.PreviewReminders(),config());await b.handle(1,'Томск сварщик мне 24')
        s=b.session(1);before=deepcopy(s.work['fields'])
        if failure=='llm':
            broken=True;replies=await b.handle(1,'теперь хочу от 95000 рублей в месяц, без опыта')
            assert any(t['kind']=='llm_error' for t in s.trace)
            assert s.work['fields']['salary']['value']['lower']==95000
            assert {k:v for k,v in s.work['fields'].items() if k!='salary'}=={k:v for k,v in before.items() if k!='salary'}
            assert any(t.get('kind')=='condition_recognition' and t.get('method')=='deterministic_fallback' for t in s.trace)
        else:
            replies=await b.handle(1,payload='jump:work_more')
            assert calls==[0,0] and s.offset==0
            assert s.work['fields']==before
            assert not any('подходящих вариантов не нашлось' in r['text'].lower() for r in replies)
    asyncio.run(run())


def test_cached_vacancies_are_disclosed_and_recorded(monkeypatch):
    async def llm(*a,**kw):return json.dumps(dict(branch='work',work_fields={
        'city':field('known','Томск','Томск'),'query':field('known','сварщик','сварщик'),
        'age':field('known',24,'24')}),ensure_ascii=False)
    async def source(*a,**kw):return SimpleNamespace(
        items=[],next_offset=None,attempt_count=2,stale_age_seconds=90)
    monkeypatch.setattr(flow,'call_llm',llm);monkeypatch.setattr(flow,'search_vacancies',source)
    async def run():
        b=flow.FlowDialogue(flow.PreviewReminders(),config())
        replies=await b.handle(1,'Томск, сварщик, 24 года')
        assert any('сохранённые результаты' in item['text'].lower() for item in replies)
        kinds=[item['kind'] for item in b.session(1).trace]
        assert 'source_retry' in kinds and 'source_cache_fallback' in kinds
    asyncio.run(run())


def test_max_receiver_and_preview_share_v3_result(monkeypatch):
    from project.llm.bot import Runner
    sent=[]
    class Scenarios:
        async def read(self,published=False):return dict(config=config(),revision=19)
        async def event(self,*a):pass
    from project.llm.tests.fake_max import RecordingMax
    class Receiver(RecordingMax):
        def __init__(self): super().__init__(sent)
    async def llm(*a,**kw):return json.dumps(dict(branch='work',work_fields={
        'city':field('known','Томск','томск'),'query':field('known','сварщик','сварщик')}))
    async def source(*a,**kw):return SimpleNamespace(items=[],next_offset=None)
    monkeypatch.setattr(flow,'call_llm',llm);monkeypatch.setattr(flow,'search_vacancies',source)
    async def run():
        max_engine=flow.PublishedDialogue(flow.PreviewReminders(),Scenarios())
        runner=Runner(Receiver(),max_engine,max_engine.reminders,'synthetic-v3')
        preview=flow.FlowDialogue(flow.PreviewReminders(),config(),19)
        for i,text in enumerate(['томск сварщик']):
            await runner.process(dict(update_type='message_created',timestamp=runner.started_ms+1,
                message=dict(recipient=dict(chat_type='dialog'),sender=dict(user_id=9901),body=dict(text=text,mid=f'synthetic-v3-{i}'))))
            await preview.handle(1,text)
        assert max_engine.sessions[9901].values==preview.session(1).values
        assert max_engine.sessions[9901].work['fields']==preview.session(1).work['fields']
        assert sent and preview.session(1).work['fields']['age']['value'] is None
    asyncio.run(run())


def test_reminder_is_separate_from_search_place_and_needs_confirmation():
    async def run():
        b=flow.FlowDialogue(flow.PreviewReminders(),config());s=b.session(1)
        b.work.apply(s,{'city':field('known','Томск','Томск')},'Томск')
        b.new_reminder(s,'Посмотреть вакансии')
        await b.handle(1,'Омск')
        assert s.pending['zone']=='Etc/GMT-6' and s.values['city']=='Томск'
        from datetime import datetime,timedelta,timezone
        await b.handle(1,(datetime.now(timezone.utc)+timedelta(days=3)).strftime('18:30, %d.%m.%Y'))
        assert not b.reminders.rows and s.pending['stage']=='confirm'
    asyncio.run(run())


def test_nested_model_fields_are_an_error_not_a_partially_saved_search(monkeypatch):
    async def llm(*a,**kw):
        age=field('known',28,'Мне 28');age['city']=field('known','Томск','Томск')
        return json.dumps(dict(branch='work',work_fields={'age':age}))
    monkeypatch.setattr(flow,'call_llm',llm)
    async def run():
        b=flow.FlowDialogue(flow.PreviewReminders(),config());await b.handle(1,payload='jump:work')
        before=deepcopy(b.session(1).work['fields'])
        await b.handle(1,'Мне 28, Томск, это важно')
        assert b.session(1).work['fields']==before
        assert any(t['kind']=='llm_error' for t in b.session(1).trace)
    asyncio.run(run())


def test_model_gets_salary_meaning_without_mutating_validated_pending(monkeypatch):
    captured=[]
    async def llm(messages,**kw):
        captured.append(json.loads(messages[1]['content']))
        return json.dumps(dict(branch='work',work_fields={}))
    monkeypatch.setattr(flow,'call_llm',llm)
    async def run():
        b=flow.FlowDialogue(flow.PreviewReminders(),config());s=b.session(1)
        b.work.apply(s,{'salary':field('known',{},'от 63000 рублей')},'от 63000 рублей')
        before=deepcopy(s.work['pending_fields'])
        await b.route(s,'пока думаю',{})
        assert s.work['pending_fields']==before
        assert isinstance(captured[0]['pending']['salary']['value'],str)
        assert captured[0]['applicant'] is None
    asyncio.run(run())
