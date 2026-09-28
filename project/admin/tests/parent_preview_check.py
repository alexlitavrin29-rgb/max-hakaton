"""Verify saved parent draft and unaffected role menus through running preview API."""
from datetime import datetime,timedelta
import json
from project.admin.documents_reference_draft import ROOT,request
from project.admin.parent_questions_draft import CATEGORIES


def buttons(r):return [b for msg in r['replies'] for a in msg.get('attachments',[]) for row in a['payload']['buttons'] for b in row]


def main():
    state=request('state');c=state['config'];checks=[]
    def chat(sid='',**kw):
        r=request('preview',dict(session=sid,**kw))
        assert not any(t['kind'].startswith('llm') or t['kind'].endswith('error') for t in r['trace']),r['trace']
        return r
    r=chat(reset=True);sid=r['session']
    parent=next(b for b in buttons(r) if b['text']=='Я родитель / опекун')
    r=chat(sid,payload=parent['payload'])
    assert r['node']=='pa_home'
    assert [b['text'] for b in buttons(r)][:7]==[title for key,title in CATEGORIES]
    checks.append('parent_menu_seven_categories')
    for n in c['nodes']:
        if n['branch']!='parent':continue
        r=chat(sid,payload='jump:'+n['id']);assert r['node']==n['id'] and r['replies'][0]['text']==n['text']
        checks.append('screen:'+n['id'])
        for button in buttons(r):
            if button.get('url'):continue
            index=int(button['payload'].split(':')[-1]);target=n['buttons'][index]['target']
            if not target.startswith('pa_'):continue
            next_reply=chat(sid,payload=button['payload']);assert next_reply['node']==target
            checks.append('transition:'+n['id']+'->'+target)
    r=chat(sid,payload='jump:pa_report_where')
    remind=next(b for b in buttons(r) if b['text']=='Напомнить')
    chat(sid,payload=remind['payload']);chat(sid,text='Тула, Тульская область')
    r=chat(sid,text=(datetime.now()+timedelta(days=2)).strftime('18:30, %d.%m.%Y'))
    save=next(b for b in buttons(r) if b.get('payload','').startswith('save:'))
    r=chat(sid,payload=save['payload']);assert r['sandbox']
    r=chat(sid,payload='jump:reminders');assert any(b.get('payload','').startswith('delete:') for b in buttons(r))
    r=chat(sid,text='меню');assert r['node']=='pa_home'
    checks.append('reminder_confirm_save_preview_only_and_menu_return')
    r=chat(sid,payload='jump:pa_employment')
    search=next(b for b in buttons(r) if b['text']=='Поиск работы')
    r=chat(sid,payload=search['payload']);assert r['branch']=='work' and r['values']['role']=='parent'
    checks.append('existing_job_search_parent_role')
    before=json.loads((ROOT/'docs/test-results/parent-questions/before.json').read_text(encoding='utf-8'))['config']
    from project.llm.services.flow import FlowDialogue,PreviewReminders
    import asyncio
    async def expected(role):
        bot=FlowDialogue(PreviewReminders(),before);bot.session(1).values['role']=role
        replies=await bot.handle(1,text='меню');return [b['text'] for b in buttons({'replies':replies})]
    for label,role in [('Я ребёнок','child'),('Хочу принять ребёнка в семью','candidate')]:
        r=chat(reset=True);sid=r['session']
        r=chat(sid,payload=next(b['payload'] for b in buttons(r) if b['text']==label))
        r=chat(sid,text='меню');assert [b['text'] for b in buttons(r)]==asyncio.run(expected(role))
        for n in c['nodes']:
            if n['branch']=='parent':
                r=chat(sid,payload='jump:'+n['id']);assert r['branch']!='parent'
                checks.append('role_guard:'+role+':'+n['id'])
    assert request('state')['published_id']==state['published_id']==1
    result=dict(revision=state['revision'],published_id=state['published_id'],checks=len(checks),passed=True,details=checks)
    (ROOT/'docs/test-results/parent-questions/preview.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps({k:v for k,v in result.items() if k!='details'}))


if __name__=='__main__':main()
