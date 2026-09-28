"""Prepared content and both search entrances through the actual preview engine."""
import json
from project.admin.documents_reference_draft import ROOT, request
from project.admin.housing_questions_draft import configure


def buttons(reply):
    return [b for r in reply['replies'] for a in r.get('attachments',[]) for row in a['payload']['buttons'] for b in row]


def main():
    state=request('state');config=state['config'];_,added=configure(config)
    checks=[]
    def chat(session='',payload=None,reset=False):
        result=request('preview',dict(session=session,payload=payload,reset=reset))
        assert not any(t['kind'].startswith('llm') or t['kind'].endswith('error') for t in result['trace']),result['trace']
        return result
    for role,label in [('child','Я ребёнок'),('parent','Я родитель / опекун'),('candidate','Хочу принять ребёнка в семью')]:
        r=chat(reset=True);sid=r['session']
        r=chat(sid,next(b['payload'] for b in buttons(r) if b['text']==label))
        r=chat(sid,'jump:'+config['menu'])
        work=next(b['payload'] for b in buttons(r) if b['text']=='Ищу работу')
        housing=next(b['payload'] for b in buttons(r) if b['text']=='Ищу жильё')
        r=chat(sid,work)
        assert [b['text'] for b in buttons(r)][:2]==['Пройти тест на профориентацию','Поиск работы']
        assert buttons(r)[0]['url']==config['sources']['career']['url']
        r=chat(sid,buttons(r)[1]['payload']);assert r['branch']=='work' and r['node']=='work'
        checks.append(dict(role=role,check='work_menu_search_and_test_link',passed=True))
        r=chat(sid,housing)
        assert [b['text'] for b in buttons(r)][:7]==['Поиск жилья','Квартира от государства','Когда можно продать жильё?',
            'Как снять жильё?','Коммунальные платежи','Бытовуха','Что делать, если тебя выселяют?']
        r=chat(sid,buttons(r)[0]['payload']);assert r['branch']=='rental'
        checks.append(dict(role=role,check='housing_menu_and_search',passed=True))
        # Traverse each actual parent button, rather than opening answers by id alone.
        for key,node in added.items():
            parent=next((n for n in added.values() if any(b.get('target')==key for b in n['buttons'])),None)
            if key=='housing':continue
            assert parent,key
            r=chat(sid,'jump:'+parent['id'])
            index=next(i for i,b in enumerate(parent['buttons']) if b.get('target')==key)
            r=chat(sid,f"g:{state['revision']}:{parent['id']}:{index}")
            assert r['node']==key and node['text'] in r['replies'][0]['text'],key
            back=next(b['payload'] for b in buttons(r) if b['text']=='Назад')
            back_result=chat(sid,back)
            assert back_result['node']==next(b['target'] for b in node['buttons'] if b['label']=='Назад')
            checks.append(dict(role=role,node=key,passed=True))
        r=chat(sid,'jump:hq_household_gas')
        r=chat(sid,next(b['payload'] for b in buttons(r) if b['text']=='Напомнить'))
        assert any('город' in m['text'].lower() for m in r['replies']) and r['sandbox']
        checks.append(dict(role=role,check='reminder_city_prompt',passed=True))
    result=dict(revision=state['revision'],published_id=state['published_id'],checks=checks)
    out=ROOT/'docs/test-results/housing-questions';out.mkdir(parents=True,exist_ok=True)
    (out/'preview.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps(dict(passed=len(checks),revision=state['revision'],published_id=state['published_id'])))


if __name__=='__main__':main()
