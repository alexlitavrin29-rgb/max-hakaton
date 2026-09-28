"""Real preview ASGI endpoint and MAX receiver on the same frozen scenario.

MAX transport is memory-only; this never polls or sends to MAX.
"""
import asyncio
import copy
import json
import re
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest

from project.admin.app import app
from project.admin.tests.test_rental import fields, page, row, CITY
from project.llm.bot import Runner
from project.llm.services import flow, rental
from project.llm.services.work import field


@pytest.mark.parametrize('branch',['work','rental'])
def test_preview_endpoint_and_max_receiver_share_snapshot(monkeypatch,branch):
    cfg=json.loads(Path('docs/test-results/quality-2026-09-26/scenario.json').read_text(encoding='utf-8'))
    class Store:
        events=[]
        async def read(self,published=False):return dict(config=copy.deepcopy(cfg),revision=49,published_id=1)
        async def event(self,*args):self.events.append(args)
    sent=[]
    from project.llm.tests.fake_max import RecordingMax
    class Transport(RecordingMax):
        def __init__(self): super().__init__(sent)
    async def llm(messages,**kwargs):
        if branch=='rental':return json.dumps(fields())
        return json.dumps(dict(branch='work',work_fields={},work_action=None,work_other=None))
    async def search(*args,**kwargs):return SimpleNamespace(items=[],next_offset=None)
    async def locations(q):return [CITY]
    async def rentals(*args):return page([row()])
    monkeypatch.setattr(flow,'call_llm',llm);monkeypatch.setattr(rental,'call_llm',llm)
    monkeypatch.setattr(flow,'search_vacancies',search);monkeypatch.setattr(rental.reefapi,'locations',locations);monkeypatch.setattr(rental.reefapi,'search',rentals)
    monkeypatch.setattr(app.state,'store',Store(),raising=False);monkeypatch.setattr(app.state,'previews',{},raising=False)
    def normalize(obj):
        if isinstance(obj,list):return [normalize(x) for x in obj]
        if isinstance(obj,dict):return {k:normalize(v) for k,v in obj.items()}
        if isinstance(obj,str):return re.sub(r'([wr]:49:)[0-9a-f]+:',r'\1NONCE:',obj)
        return obj
    async def run():
        published=flow.PublishedDialogue(flow.PreviewReminders(),Store())
        runner=Runner(Transport(),published,flow.PreviewReminders(),'fixture')
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='http://testserver',headers={'x-editor-request':'local'}) as client:
            session=''
            steps=[dict(payload='jump:'+branch),dict(text='работа в Омске, мне 19 лет, зарплата от 60 тысяч рублей в месяц' if branch=='work' else 'Томск, до 30 тысяч')]
            for i,step in enumerate(steps):
                response=await client.post('/api/preview',json=dict(session=session,**step));assert response.status_code==200
                preview=response.json();session=preview['session'];sent.clear()
                event=dict(update_type='message_callback' if step.get('payload') else 'message_created',timestamp=runner.started_ms+1,
                    message=dict(recipient=dict(chat_type='dialog'),sender=dict(user_id=99001),body=dict(text=step.get('text',''),mid='test-'+str(i))))
                if step.get('payload'):event['callback']=dict(user=dict(user_id=99001),payload=step['payload'],callback_id='c-'+str(i))
                await runner.process(event)
                assert normalize(sent)==normalize(preview['replies'])
                state=published.engine.session(99001)
                if branch=='work':assert state.work['fields']==preview['work']['fields']
                else:
                    assert state.rental['city']==preview['rental']['city']
                    assert state.rental['other']==preview['rental']['other']
                    assert state.rental['awaiting']==(None if i else 'city_budget')
                assert published.engine.config==cfg
            assert ('condition_recognition_deterministic',branch) in published.store.events
    asyncio.run(run())
