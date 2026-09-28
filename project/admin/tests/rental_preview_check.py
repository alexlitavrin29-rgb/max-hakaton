"""Opt-in acceptance through the actual admin endpoint, real LLM and ReefAPI."""
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import time
import urllib.request

ROOT=Path(__file__).resolve().parents[3]
BASE='http://127.0.0.1:18765/api/'


def buttons(replies):
    return [b for r in replies for a in r.get('attachments',[]) for row in a['payload']['buttons'] for b in row]


def main():
    session=''
    history=[]
    def step(text='',payload=None,reset=False):
        nonlocal session
        request=urllib.request.Request(BASE+'preview',data=json.dumps(dict(session=session,text=text,payload=payload,reset=reset),ensure_ascii=False).encode(),
            headers={'Content-Type':'application/json','X-Editor-Request':'local'})
        start=time.monotonic()
        with urllib.request.urlopen(request,timeout=90) as response:r=json.load(response)
        session=r['session'];state=r.get('rental',{})
        history.append(dict(input=text or payload or 'reset',seconds=round(time.monotonic()-start,2),replies=r['replies'],trace=r['trace'],
            understood={k:v for k,v in state.items() if k not in {'buffer','nonce'}},revision=r['revision']))
        (ROOT/'docs/test-results/housing/live-preview.json').write_text(json.dumps(history,ensure_ascii=False,indent=2),encoding='utf-8')
        print(json.dumps(dict(step=len(history),seconds=history[-1]['seconds'],branch=r['branch'],errors=[t for t in r['trace'] if 'error' in t['kind']])),flush=True)
        assert not any('error' in t['kind'] for t in r['trace']), 'See live-preview.json'
        return r

    def click(r,label):
        return step(payload=next(b['payload'] for b in buttons(r['replies']) if b['text']==label))

    r=step(reset=True)
    r=click(r,'Я ребёнок')
    assert [b['text'] for b in buttons(r['replies'])][:3]==['Ищу работу','Ищу жильё','Ищу наставника']
    r=click(r,'Ищу жильё');r=click(r,'Поиск жилья');assert r['branch']=='rental'
    r=step('Томск');assert r['rental']['awaiting']=='budget' and r['rental']['city']['slug']=='tomsk'
    r=step('до 30 тысяч');assert len(r['rental']['seen'])==3
    shown=set(r['rental']['seen'])
    r=click(r,'Показать ещё');assert len(r['rental']['seen'])==6 and shown.issubset(r['rental']['seen'])
    assert not any(t['kind']=='rental_search' for t in r['trace']), 'Buffered cards must not re-fetch'
    r=click(r,'Изменить условия');assert r['rental']['budget']==30000
    r=step('Нет, до 25 тысяч');assert r['rental']['budget']==25000 and r['rental']['city']['slug']=='tomsk'
    assert r['rental']['page']==2 and len(r['rental']['seen'])==3
    r=click(r,'Ничего не подошло');assert r['rental']['budget']==25000
    r=step('Теперь до 1000 рублей');assert r['rental']['budget']==1000
    assert not r['rental']['seen'] and 'не означает' in r['replies'][0]['text']
    r=click(r,'Напомнить');r=step('Томск')
    r=step((datetime.now(timezone.utc)+timedelta(days=2)).strftime('18:30, %d.%m.%Y'))
    r=click(r,'Сохранить');assert r['sandbox'] and r['rental']['budget']==1000
    print(json.dumps(dict(passed=True,steps=len(history),revision=r['revision'])),flush=True)


if __name__=='__main__':main()
