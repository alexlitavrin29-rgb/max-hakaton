"""Synthetic end-to-end conversations through the running editor API."""

import json
import argparse
from pathlib import Path
import time
import urllib.request
import uuid


BASE="http://127.0.0.1:18765"


def request(path,body):
    req=urllib.request.Request(BASE+path,data=json.dumps(body,ensure_ascii=False).encode(),headers={"Content-Type":"application/json","X-Editor-Request":"local"})
    with urllib.request.urlopen(req,timeout=120) as response: return json.load(response)


def main(args):
    cases=[
        ("tomsk_welder",["томск сварщик","20"],{"city":"Томск","query":"сварщик","age":20}),
        ("college",["мне 20, после колледжа, подработка от 60 тысяч","Томск"],{"age":20,"education_level":"vocational","employment":"part_time","salary":60000,"city":"Томск"}),
        ("housing",["ищу любую работу с жильём","Томск","не хочу указывать"],{"query":None,"housing":True,"age":None,"city":"Томск"}),
        ("parent",["Мне 40, сыну 17, ищем ему работу в Туле"],{"role":"parent","age":17,"city":"Тула"}),
        ("correction",["Томск, мне 20, сварщик","Теперь ищем в Туле, от 60000"],{"city":"Тула","query":"сварщик","age":20,"salary":60000}),
        ("ambiguous",["Советск, мне 20, любая работа","Тульская область"],{"city":"Советск","region_code":"7100000000000","age":20}),
        ("all_conditions",["Мне 20, окончил колледж. Томск, сварщик, опыт 1 год, подработка, сменный график, от 60000, с проживанием"],{"age":20,"education_level":"vocational","city":"Томск","query":"сварщик","experience":1,"employment":"part_time","schedule":"сменный график","salary":60000,"housing":True}),
        ("any_profession",["профессия не важна","Томск","20"],{"query":None,"city":"Томск","age":20}),
    ]
    results=[]
    for name,messages,expected in cases:
        if args.only and name not in args.only.split(","): continue
        session="synthetic-work-"+uuid.uuid4().hex
        request("/api/preview",dict(session=session,payload="jump:work"))
        turns=[]
        for text in messages:
            response=request("/api/preview",dict(session=session,text=text))
            turns.append(dict(text=text,node=response["node"],replies=response["replies"],values=response["values"],work=response["work"],trace=response["trace"]))
            print(json.dumps(dict(case=name,node=response["node"],errors=[t.get("code") for t in response["trace"] if t["kind"] in {"llm_error","search_error"}])),flush=True)
            time.sleep(10)
        passed=all(response["values"].get(k)==v for k,v in expected.items())
        expected_nodes={"tomsk_welder":["work_age","work_search"],"college":["work_city","work_search"],
                        "housing":["work_city","work_age","work_search"],"parent":["work_search"],
                        "correction":["work_search","work_search"],"ambiguous":["work_city","work_search"],
                        "all_conditions":["work_search"],"any_profession":["work_city","work_age","work_search"]}
        passed=passed and [t["node"] for t in turns]==expected_nodes[name] and not any(t["work"].get("parse_issue") for t in turns)
        searched=any(t["kind"]=="search" for turn in turns for t in turn["trace"])
        passed=passed and searched
        results.append(dict(name=name,passed=passed,searched=searched,expected=expected,turns=turns))
        Path(args.output).write_text(json.dumps(results,ensure_ascii=False,indent=2),encoding="utf-8")
        print(json.dumps(dict(case=name,passed=passed,searched=searched)),flush=True)
    print(json.dumps(dict(passed=sum(r["passed"] for r in results),searched=sum(r["searched"] for r in results),total=len(results))))


if __name__=="__main__":
    parser=argparse.ArgumentParser();parser.add_argument("--only",default="");parser.add_argument("--output",default="tmp/work-preview-results.json")
    main(parser.parse_args())
