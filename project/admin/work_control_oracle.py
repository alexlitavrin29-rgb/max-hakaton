"""Deterministic turn-level oracle for the frozen v1 dialogue templates.

This is additional checking, not evidence that unrun dialogues succeeded.
The oracle uses declared case expectations, never model answers.
"""
from copy import deepcopy
import re


def same_value(name,actual,expected):
    if isinstance(actual,str) and isinstance(expected,str):
        actual=actual.casefold().replace('ё','е').strip()
        expected=expected.casefold().replace('ё','е').strip()
        if name=='schedule':
            actual=re.sub(r'^график\s+','',actual)
            expected=re.sub(r'^график\s+','',expected)
    return actual==expected

def expected_steps(case):
    if case.get('steps'):return [step['values'] for step in case['steps']]
    known={};steps=[];expected=case['expected']
    for i,text in enumerate(case['messages']):
        if 'Мне 26' in text:known['age']=26
        if expected['city'] in text:
            known.update(city=expected['city'],region_code=expected['region_code'])
        if expected['query'] in text:known['query']=expected['query']
        if case['group']=='correction':
            known['salary']=dict(kind='min',lower=38000,upper=None,period='month',tax='unknown',currency='RUB') if i==0 else None if i==1 else expected['salary']
        elif 'По оплате:' in text or case['group']=='salary':known['salary']=expected['salary']
        steps.append(deepcopy(known))
    return steps

def check_turns(case,turns):
    problems=[];request_count=0
    for i,(expected,turn) in enumerate(zip(expected_steps(case),turns)):
        spec=case.get('steps',[{} for _ in turns])[i]
        for name,value in expected.items():
            actual=turn['values'].get(name)
            equal=same_value(name,actual,value)
            if not equal:problems.append(dict(turn=i+1,field=name,expected=value,actual=actual))
        fields=turn['work'].get('fields',{})
        for name,status in spec.get('statuses',{}).items():
            if fields.get(name,{}).get('status')!=status:problems.append(dict(turn=i+1,field=name,expected_status=status,actual_status=fields.get(name,{}).get('status')))
        for name,item in fields.items():
            if name not in expected and item.get('status')=='known':problems.append(dict(turn=i+1,invented_field=name))
        if set(turn['work'].get('pending_fields',{}))!=set(spec.get('pending',[])):problems.append(dict(turn=i+1,unexpected_pending=True))
        awaiting=turn['work'].get('awaiting')
        if 'awaiting' in spec:
            if awaiting!=spec['awaiting']:problems.append(dict(turn=i+1,wrong_question=awaiting))
        elif awaiting in expected:problems.append(dict(turn=i+1,repeated_question=awaiting))
        new_requests=turn['requests'][request_count:];request_count=len(turn['requests'])
        for request in new_requests:
            if 'city' not in expected or request['region_code']!=expected['region_code'] or request['query']!=expected.get('query'):
                problems.append(dict(turn=i+1,wrong_source_request=request))
            if request.get('experience_to')!=expected.get('experience') or request.get('accommodation')!=(True if expected.get('housing') else None):
                problems.append(dict(turn=i+1,wrong_source_filters=request))
        search_expected=spec.get('search','city' in expected)
        if search_expected and not new_requests:problems.append(dict(turn=i+1,missing_search=True))
        if not search_expected and new_requests:problems.append(dict(turn=i+1,premature_search=True))
    if len(turns)!=len(case['messages']):problems.append(dict(incomplete_dialogue=True))
    return problems
