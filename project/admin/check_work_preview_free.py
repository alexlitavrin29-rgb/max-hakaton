"""Synthetic requests to the real local draft API, with stale-revision protection."""
import json,urllib.request,urllib.error
from pathlib import Path

BASE='http://127.0.0.1:18765'
def request(path,data=None,method=None):
    req=urllib.request.Request(BASE+path,data=json.dumps(data,ensure_ascii=False).encode() if data else None,
        headers={'Content-Type':'application/json','X-Editor-Request':'local'},method=method)
    with urllib.request.urlopen(req,timeout=55) as r:return json.load(r)

def main():
    before=request('/api/state');revision=before['revision']
    try:
        request('/api/draft',dict(config=before['config'],revision=revision-1),'PUT')
        raise AssertionError('Stale revision accepted')
    except urllib.error.HTTPError as e:assert e.code==409
    rows=[]
    for i,text in enumerate(['томск сварщик','мне 20, после колледжа, подработка от 60 тысяч','ищу любую работу с жильём']):
        first=request('/api/preview',dict(payload='jump:work'))
        result=request('/api/preview',dict(session=first['session'],text=text))
        rows.append(dict(input=text,result=result))
        print(json.dumps(dict(case=i+1,values=result['values'],awaiting=result['work'].get('awaiting'),
            errors=[t.get('code') for t in result['trace'] if t['kind']=='llm_error']),ensure_ascii=True),flush=True)
    after=request('/api/state')
    assert after['revision']==revision and after['published_id']==before['published_id']
    Path('docs/test-results/work-free-input/admin-preview.json').write_text(json.dumps(dict(revision=revision,published_id=after['published_id'],stale_revision_status=409,synthetic_cases=rows),ensure_ascii=False,indent=2),encoding='utf-8')

if __name__=='__main__':main()
