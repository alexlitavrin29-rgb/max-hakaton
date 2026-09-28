"""Audit regressions: retain source meaning and do not starve other searches."""
import asyncio
from types import SimpleNamespace

import httpx
import pytest

from project.llm.integrations import headhunter, trudvsem
from project.llm.services import flow
from project.llm.services.work import assess
from project.llm.tests.test_headhunter import raw, PLACE
from project.admin.tests.test_work_contract import config, job


@pytest.mark.parametrize('formats',[
    [{'id':'ON_SITE','name':'На месте работодателя'}],
    [{'id':'HYBRID','name':'Гибрид'}],
    [{'id':'FIELD_WORK','name':'Разъездной'}],
])
def test_explicit_nonremote_hh_vacancy_is_rejected_for_remote_request(formats):
    async def run():
        client=headhunter.HeadHunterClient('test',transport=httpx.MockTransport(lambda r:httpx.Response(200)))
        try:
            client.areas={}
            row=raw();row['work_format']=formats
            vacancy=client.normalize(row)
            reason,_=assess(vacancy,{'schedule':'удалённо'},None)
            assert reason is not None, 'Explicit nonremote format must not be presented as merely unknown'
        finally:await client.close()
    asyncio.run(run())


@pytest.mark.parametrize('formats',[
    [], [{'id':'REMOTE','name':'Удалённо'}],
    [{'id':'ON_SITE','name':'На месте работодателя'},{'id':'REMOTE','name':'Удалённо'}],
])
def test_unknown_or_optional_remote_format_is_not_rejected(formats):
    async def run():
        client=headhunter.HeadHunterClient('test',transport=httpx.MockTransport(lambda r:httpx.Response(200)))
        try:
            client.areas={}
            row=raw();row['work_format']=formats
            reason,missing=assess(client.normalize(row),{'schedule':'удалённо'},None)
            assert reason is None
            if not formats:assert missing
        finally:await client.close()
    asyncio.run(run())


@pytest.mark.parametrize('identifier,label,years,rejected',[
    ('noExperience','Нет опыта',0,False),
    ('moreThan6','Более 6 лет',6,True),
    ('moreThan6','Более 6 лет',7,False),
])
def test_hh_experience_text_is_preserved_while_boundary_is_checked(identifier,label,years,rejected):
    async def run():
        client=headhunter.HeadHunterClient('test',transport=httpx.MockTransport(lambda r:httpx.Response(200)))
        try:
            client.areas={}
            row=raw();row['experience']={'id':identifier,'name':label}
            vacancy=client.normalize(row)
            assert vacancy.experience==label
            reason,missing=assess(vacancy,{'experience':years},None)
            assert bool(reason)==rejected
            if not rejected:assert not missing
        finally:await client.close()
    asyncio.run(run())


@pytest.mark.parametrize('partial',[True,False])
def test_failed_first_city_does_not_block_other_selected_city(monkeypatch,partial):
    monkeypatch.setattr(headhunter,'enabled',lambda:True)
    async def rr(query,**kw):
        if kw['region_code']=='7000000000000':
            if not partial:raise trudvsem.TrudvsemError('timeout')
            return SimpleNamespace(items=[],next_offset=None)
        return SimpleNamespace(items=[job('tula',city='Тула',source='trudvsem')],next_offset=None)
    async def hh(query,**kw):
        if kw['place']['name']=='Томск':raise trudvsem.TrudvsemError('hh_timeout')
        return SimpleNamespace(items=[],next_offset=None)
    monkeypatch.setattr(trudvsem,'search_vacancies',rr)
    monkeypatch.setattr(headhunter,'search_vacancies',hh)
    async def run():
        cfg=config();cfg['search']['contract_version']=3
        engine=flow.FlowDialogue(flow.PreviewReminders(),cfg,3);session=engine.session(1)
        state=engine.work.state(session)
        state['selected_places']=[PLACE,dict(name='Тула',region='Тульская область',kind='city',code='7100000000000')]
        try:await engine.work.fetch(session,'сварщик',offset=0,limit=100)
        except trudvsem.TrudvsemError:pass
        try:page=await engine.work.fetch(session,'сварщик',offset=1,limit=100)
        except trudvsem.TrudvsemError:pytest.fail('A failing first city blocked the healthy second city')
        assert any(v.city=='Тула' for v in page.items)
        # The failed city remains pending for a later retry.
        assert next(s for s in state['streams'] if s['place']['name']=='Томск')['offset'] is not None
    asyncio.run(run())
