import asyncio
from types import SimpleNamespace

import httpx
import pytest

from project.llm.integrations import headhunter, trudvsem
from project.llm.services import job_search, money
from project.llm.services.work import assess


PLACE = dict(name='Томск', region='Томская область', kind='city', code='7000000000000')
AREAS = [dict(id='113', name='Россия', areas=[
    dict(id='1202', name='Томская область', areas=[dict(id='90', name='Томск', areas=[])]),
    dict(id='1', name='Москва', areas=[])])]


def raw(**changes):
    return dict(id='42', name='Сварщик', area=dict(id='90', name='Томск'),
        employer=dict(name='Завод'), address=dict(raw='Томск, улица Ленина, 1'),
        salary_range=dict(**{'from':60000, 'to':80000}, currency='RUR', gross=False, mode=dict(id='MONTH')),
        experience=dict(id='noExperience', name='Нет опыта'),
        snippet=dict(requirement='<highlighttext>Без опыта</highlighttext>', responsibility='Сварка'),
        employment_form=dict(id='FULL', name='Полная'),
        work_format=[dict(id='REMOTE', name='Удалённо')], **changes)


def client(handler):
    return headhunter.HeadHunterClient('test-secret', transport=httpx.MockTransport(handler))


def test_403_never_refreshes_token_and_areas_are_public():
    calls = []
    def handler(request):
        calls.append((request.url.path, request.headers.get('Authorization', '')))
        if request.url.path == '/areas':
            return httpx.Response(200, json=AREAS)
        return httpx.Response(403, json={'errors': [{'type': 'oauth', 'value': 'bad_authorization'}]})
    async def run():
        service = headhunter.HeadHunterClient('test-secret', transport=httpx.MockTransport(handler))
        try:
            with pytest.raises(trudvsem.TrudvsemError, match='hh_api_error'):
                await service.search('сварщик', place=PLACE, limit=5)
            assert calls == [('/areas', ''), ('/vacancies', 'Bearer test-secret')]
        finally:
            await service.close()
    asyncio.run(run())


def test_403_on_areas_does_not_refresh_or_expose_token():
    calls = []
    def handler(request):
        calls.append(request.url.path)
        return httpx.Response(403, json={'errors': [{'type': 'oauth', 'value': 'bad_authorization'}]})
    async def run():
        service = headhunter.HeadHunterClient('expired', transport=httpx.MockTransport(handler))
        try:
            with pytest.raises(trudvsem.TrudvsemError, match='hh_api_error'):
                await service.search('сварщик', place=PLACE, limit=5)
            assert calls == ['/areas']
        finally:
            await service.close()
    asyncio.run(run())


def test_real_schema_mapping_search_parameters_and_lifecycle():
    requests=[]
    def handler(request):
        requests.append(request)
        assert request.headers.get('Authorization', '')==('' if request.url.path=='/areas' else 'Bearer test-secret')
        assert request.headers['HH-User-Agent'].startswith('TochkaOpory/')
        return httpx.Response(200,json=AREAS if request.url.path=='/areas' else
            dict(items=[raw()],found=201,pages=3))
    async def run():
        service=client(handler)
        try:
            page=await service.search('сварщик',place=PLACE,limit=100)
            job=page.items[0]
            assert (job.source,job.id,job.region,job.city)==('hh','42','Томская область','Томск')
            assert (job.salary_from,job.salary_period,job.salary_tax)==(60000,'month','net')
            assert job.experience=='Нет опыта' and job.requirements=='Без опыта'
            assert job.accommodation is None and job.education is None
            assert job.url=='https://hh.ru/vacancy/42'
            assert page.next_offset==1
            assert money.assess(job,dict(kind='min',lower=50000,period='month',tax='net'))==(None,None)
            assert assess(job,{'experience':0,'employment':'full_time','schedule':'удалённо'},PLACE)[0] is None
            assert dict(requests[-1].url.params)==dict(area='90',per_page='100',page='0',
                no_magic='true',order_by='publication_time',text='сварщик',search_field='name')
            await service.search(place=PLACE,offset=2)
            assert sum(r.url.path=='/areas' for r in requests)==1
        finally:await service.close()
        assert service.client.is_closed
    asyncio.run(run())


@pytest.mark.parametrize('mode,period',[('MONTH','month'),('HOUR','hour'),('SHIFT','shift'),('FLY_IN_FLY_OUT','rotation'),('SERVICE',None)])
def test_salary_period_is_never_assumed_monthly(mode,period):
    async def run():
        service=client(lambda r: httpx.Response(200))
        service.areas={'90':dict(name='Томск',region='Томская область')}
        row=raw();row['salary_range']['mode']['id']=mode
        job=service.normalize(row)
        assert job.salary_period==period
        if period!='month':
            assert money.assess(job,dict(kind='min',lower=50000,period='month',tax='net'))[1]
            assert 'месячная' in assess(job,{'salary':50000},PLACE)[1][0]
        row['salary_range']['currency']='USD'
        foreign=service.normalize(row)
        assert foreign.salary_from is None and foreign.salary_to is None
        assert money.assess(foreign,dict(kind='min',lower=50000,period='month',tax='net'))[1]
        await service.close()
    asyncio.run(run())


def test_experience_upper_bucket_and_missing_fields_remain_unverified():
    async def run():
        service=client(lambda r:httpx.Response(200));service.areas={}
        row=raw();row['experience']={'id':'moreThan6','name':'Более 6 лет'}
        job=service.normalize(row)
        assert assess(job,{'experience':6},None)[0]=='Требуется больший опыт'
        assert assess(job,{'housing':True},None)[1]==['проживание в карточке не подтверждено']
        await service.close()
    asyncio.run(run())


@pytest.mark.parametrize('place',[
    {**PLACE,'name':'Неизвестный город'},
    {**PLACE,'region':'Московская область'},
])
def test_unknown_or_conflicting_place_does_not_broaden_search(place):
    paths=[]
    def handler(request):
        paths.append(request.url.path);return httpx.Response(200,json=AREAS)
    async def run():
        service=client(handler)
        try:
            with pytest.raises(trudvsem.TrudvsemError,match='hh_area_unavailable'):
                await service.search(place=place)
            assert paths==['/areas']
        finally:await service.close()
    asyncio.run(run())


@pytest.mark.parametrize('status,code',[(401,'hh_api_error'),(403,'hh_api_error'),(429,'hh_rate_limit'),(500,'hh_api_error')])
def test_upstream_errors_do_not_leak_tokens_or_bodies(status,code):
    async def run():
        service=client(lambda r:httpx.Response(status,text='test-secret sensitive-body'))
        try:
            with pytest.raises(trudvsem.TrudvsemError) as caught:await service.search(place=PLACE)
            assert caught.value.code==code
            assert 'test-secret' not in str(caught.value) and 'sensitive-body' not in str(caught.value)
        finally:await service.close()
    asyncio.run(run())


def test_timeout_and_search_depth_limit():
    async def handler(request):
        await asyncio.sleep(.1)
        return httpx.Response(200,json=AREAS)
    async def run():
        service=client(handler);service.timeout_seconds=.01
        try:
            with pytest.raises(trudvsem.TrudvsemError,match='hh_timeout'):await service.search(place=PLACE)
            page=await service.search(offset=20,limit=100)
            assert not page.items and page.next_offset is None
        finally:await service.close()
    asyncio.run(run())


def test_concurrent_sources_independent_pages_failure_recovery_and_exhaustion(monkeypatch):
    monkeypatch.setattr(headhunter,'enabled',lambda:True)
    calls=[];first=True;started=set();ready=None
    def page(source,offset,more):
        return SimpleNamespace(items=[(source,offset)],next_offset=offset+1 if more else None)
    async def rr(text,**kw):
        started.add('rr');calls.append(('rr',kw['offset']))
        if len(started)==2:ready.set()
        await asyncio.wait_for(ready.wait(),1)
        return page('rr',kw['offset'],kw['offset']==0)
    async def hh(text,**kw):
        nonlocal first
        started.add('hh');calls.append(('hh',kw['offset']))
        if len(started)==2:ready.set()
        await asyncio.wait_for(ready.wait(),1)
        if first:first=False;raise trudvsem.TrudvsemError('hh_api_error', 403)
        return page('hh',kw['offset'],kw['offset']==0)
    monkeypatch.setattr(trudvsem,'search_vacancies',rr)
    monkeypatch.setattr(headhunter,'search_vacancies',hh)
    async def run():
        nonlocal ready
        ready=asyncio.Event();cursors={}
        a=await job_search.search_vacancies('сварщик',place=PLACE,source_offsets=cursors,offset=0)
        assert a.failed_sources==('hh',) and cursors=={'trudvsem':1,'hh':0}
        b=await job_search.search_vacancies('сварщик',place=PLACE,source_offsets=cursors,offset=1)
        assert b.items==( ('rr',1),('hh',0)) and not b.failed_sources
        c=await job_search.search_vacancies('сварщик',place=PLACE,source_offsets=cursors,offset=2)
        assert c.items==( ('hh',1),) and c.next_offset is None
        assert calls==[('rr',0),('hh',0),('rr',1),('hh',0),('hh',1)]
    asyncio.run(run())


def test_both_fail_keep_cursors_and_single_source_without_token(monkeypatch):
    monkeypatch.setattr(headhunter,'enabled',lambda:True)
    async def failed(*a,**kw):raise trudvsem.TrudvsemError('timeout')
    monkeypatch.setattr(trudvsem,'search_vacancies',failed);monkeypatch.setattr(headhunter,'search_vacancies',failed)
    async def run():
        cursors={}
        with pytest.raises(trudvsem.TrudvsemError,match='sources_unavailable'):
            await job_search.search_vacancies(source_offsets=cursors,offset=0)
        assert cursors=={'trudvsem':0,'hh':0}
        monkeypatch.setattr(headhunter,'enabled',lambda:False)
        async def one(*a,**kw):
            assert 'place' not in kw and 'source_offsets' not in kw
            return 'rr-only'
        monkeypatch.setattr(trudvsem,'search_vacancies',one)
        assert await job_search.search_vacancies(place=PLACE,source_offsets={})=='rr-only'
    asyncio.run(run())


@pytest.mark.parametrize('contract',[2,3])
def test_flow_keeps_equal_ids_from_both_sources_and_resets_cursors(monkeypatch,contract):
    from project.admin.tests.test_work_contract import config, job, output, buttons
    from project.llm.services import flow
    from project.llm.services.work import field
    monkeypatch.setattr(headhunter,'enabled',lambda:True)
    calls=[]
    async def rr(query,**kw):
        calls.append(('rr',kw['offset']))
        return SimpleNamespace(items=[job('42',source='trudvsem')],next_offset=None)
    async def hh(query,**kw):
        calls.append(('hh',kw['offset']))
        return SimpleNamespace(items=[job('42',source='hh',url='https://hh.ru/vacancy/42',
            salary_period='rotation',salary_tax='net')],next_offset=None)
    monkeypatch.setattr(trudvsem,'search_vacancies',rr);monkeypatch.setattr(headhunter,'search_vacancies',hh)
    async def run():
        cfg=config();cfg['search']['contract_version']=contract
        engine=flow.FlowDialogue(flow.PreviewReminders(),cfg,3);session=engine.session(1)
        state=engine.work.state(session)
        state['fields']['age']=field('known',20,'20')
        result=await engine.work.search(session)
        links=[b['url'] for b in buttons(result) if 'url' in b]
        assert 'https://hh.ru/vacancy/42' in links and len(links)==2
        assert 'Источник: HeadHunter' in output(result) and 'Источник: Работа России' in output(result)
        assert 'за вахту, на руки' in output(result)
        assert session.offset is None
        engine.work.reset_search(session)
        await engine.work.search(session)
        assert calls==[('rr',0),('hh',0),('rr',0),('hh',0)]
    asyncio.run(run())


def test_hh_403_keeps_other_source_and_explains_partial_results(monkeypatch):
    from project.admin.tests.test_work_contract import config, job, output
    from project.llm.services import flow
    from project.llm.services.work import field

    monkeypatch.setattr(headhunter, 'enabled', lambda: True)
    async def rr(query, **kwargs):
        return SimpleNamespace(items=[job('42', source='trudvsem')], next_offset=None)
    async def denied(query, **kwargs):
        raise trudvsem.TrudvsemError('hh_api_error', 403)
    monkeypatch.setattr(trudvsem, 'search_vacancies', rr)
    monkeypatch.setattr(headhunter, 'search_vacancies', denied)
    async def run():
        cfg = config()
        engine = flow.FlowDialogue(flow.PreviewReminders(), cfg, 3)
        session = engine.session(1)
        engine.work.state(session)['fields']['age'] = field('known', 20, '20')
        result = await engine.work.search(session)
        visible = output(result)
        assert 'Источник: Работа России' in visible
        assert 'HeadHunter' in visible and 'Показываю доступные результаты' in visible
        assert '403' not in visible and 'hh_api_error' not in visible
    asyncio.run(run())
