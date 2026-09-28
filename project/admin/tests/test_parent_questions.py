import asyncio
from copy import deepcopy
import pytest
from project.admin.seed import initial_config
from project.admin.prepared_draft import configure as prepared
from project.admin.parent_questions_draft import configure,TOPICS,CATEGORIES
from project.admin.tests.test_flow import buttons
from project.llm.services import flow
from project.llm.services.scenario import validate,ScenarioError


def test_parent_update_is_surgical_and_repeatable():
    before=prepared(initial_config());snapshot=deepcopy(before)
    after,added=configure(before)
    assert before==snapshot
    assert configure(after)[0]==after
    old={n['id']:n for n in before['nodes']}
    for n in after['nodes']:
        if n['id'] in old and n['id']!=before['menu']:assert n==old[n['id']]
    assert after['search']==before['search']
    assert len(added)==8+len(TOPICS)+sum(len(t[3]) for t in TOPICS)
    assert after['nodes'][next(i for i,n in enumerate(after['nodes']) if n['id']==before['menu'])]['buttons']==old[before['menu']]['buttons']


def test_parent_all_questions_and_back_routes_without_llm(monkeypatch):
    async def forbidden(*args,**kwargs):raise AssertionError('Parent content must not call LLM')
    monkeypatch.setattr(flow,'call_llm',forbidden)
    async def run():
        c,added=configure(prepared(initial_config()));bot=flow.FlowDialogue(flow.PreviewReminders(),c,27)
        s=bot.session(1);s.values['role']='parent'
        menu=await bot.handle(1,text='меню')
        assert s.node=='pa_home'
        assert [b['text'] for b in buttons(menu)][:7]==[t for k,t in CATEGORIES]
        for n in added.values():
            reply=await bot.handle(1,payload='jump:'+n['id'])
            assert s.node==n['id'] and reply[0]['text']==n['text']
            assert not any(x['kind'].startswith('llm') for x in s.trace)
            for b in buttons(reply):
                if b.get('url'): assert b['url'].startswith('https://');continue
                target=n['buttons'][int(b['payload'].split(':')[-1])]['target']
                if target in {'work','reminder_new','reminders',c['start']}:continue
                await bot.handle(1,payload=b['payload'])
                assert s.node==target
        await bot.handle(1,payload='jump:pa_report_where')
        reply=await bot.handle(1,text='Придумай документы для отчёта')
        assert 'подготовленные' in reply[0]['text']
        reminder=next(b for b in buttons(await bot.handle(1,payload='jump:pa_report_where')) if b['text']=='Напомнить')
        await bot.handle(1,payload=reminder['payload']);assert s.pending
    asyncio.run(run())


def test_other_roles_cannot_open_parent_content():
    async def run():
        c,added=configure(prepared(initial_config()));bot=flow.FlowDialogue(None,c)
        for role in ['child','candidate']:
            bot.session(1).values['role']=role
            for key in added:
                await bot.handle(1,payload='jump:'+key)
                assert bot.session(1).node==c['menu']
    asyncio.run(run())


def test_capacity_allows_parent_map_but_is_bounded():
    c=initial_config();template=c['nodes'][0]
    for i in range(1000-len(c['nodes'])):c['nodes'].append({**template,'id':'capacity_'+str(i)})
    validate(c)
    c['nodes'].append({**template,'id':'capacity_overflow'})
    with pytest.raises(ScenarioError,match='1000'):validate(c)
