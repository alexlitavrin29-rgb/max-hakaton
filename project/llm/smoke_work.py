"""Synthetic live checks. Never contacts MAX or stores real conversations."""

import argparse
import asyncio
import json
from pathlib import Path

from project.admin.seed import initial_config
from project.admin.work_draft import configure_work
from project.llm.services.flow import FlowDialogue, PreviewReminders, LLMError
from project.llm.services.work import validate_fields
from project.llm.services.geography import key
from project.llm.integrations.trudvsem import search_vacancies, TrudvsemError


def cases():
    rows=[]
    def add(text,expected,awaiting=None,role="child"):
        rows.append(dict(id=len(rows)+1,text=text,expected=expected,awaiting=awaiting,role=role))
    for text in ["сварщик","ищу работу сварщиком","хочу работать сварщиком"]: add(text,{"query":"сварщик"})
    for text in ["Томск","в Томске","работу в Томске"]: add(text,{"city":"Томск"})
    for text in ["мне 20","20 лет","Мне двадцать лет"]: add(text,{"age":20},"age")
    for text in ["после колледжа","окончил техникум","среднее профессиональное образование"]: add(text,{"education_level":"vocational"})
    for text in ["без профессионального образования","нет профессионального образования","без образования"]: add(text,{"education_level":"no_professional"})
    for text in ["без опыта","опыта нет","у меня 2 года опыта"]: add(text,{"experience":2 if "2" in text else 0})
    for text in ["подработка","нужна частичная занятость","полная занятость"]: add(text,{"employment":"full_time" if text.startswith("полная") else "part_time"})
    for text in ["сменный график","только вечером","удалённо"]: add(text,{"schedule":text})
    for text in ["зарплата от 60000","от 60 тысяч","хочу от 60 тыс рублей"]: add(text,{"salary":60000})
    for text in ["нужно жильё","с проживанием","нужно общежитие"]: add(text,{"housing":True})
    for text in ["профессия не важна","любая работа","мне всё равно кем работать"]: add(text,{"query":"@any"})
    for text in ["зарплата неважна","жильё не нужно","занятость любая"]: add(text,{"salary" if "зарплата" in text else "housing" if "жильё" in text else "employment":"@any"})
    for text in ["не хочу указывать возраст","возраст не скажу","не знаю"]: add(text,{"age":"@unknown" if text=="не знаю" else "@declined"},"age")
    add("томск сварщик",{"city":"Томск","query":"сварщик"})
    add("мне 20, после колледжа, подработка от 60 тысяч",{"age":20,"education_level":"vocational","employment":"part_time","salary":60000})
    add("ищу любую работу с жильём",{"query":"@any","housing":True})
    add("Мне 40, сыну 17, ищем ему работу в Туле",{"age":17,"city":"Тула"},role="parent")
    add("Дочери 20, мне 45. Ищем ей подработку в Томске",{"age":20,"city":"Томск","employment":"part_time"},role="parent")
    add("Теперь в Туле вместо Томска",{"city":"Тула"})
    add("Зарплату меняем на 70000",{"salary":70000})
    add("Вместо сварщика хочу работать поваром",{"query":"повар"})
    add("Советск",{"city":"Советск"})
    add("Советск, Тульская область",{"city":"Советск, Тульская область"})
    add("по всей России",{"city":"@any"})
    add("Мне 19, ищу грузчиком в Туле, без опыта, от 50000 и с жильём",{"age":19,"query":"грузчик","city":"Тула","experience":0,"salary":50000,"housing":True})
    add("Мне 20, окончил колледж. Томск, сварщик, опыт 1 год, подработка, сменный график, от 60000, с проживанием",{"age":20,"education_level":"vocational","city":"Томск","query":"сварщик","experience":1,"employment":"part_time","schedule":"сменный график","salary":60000,"housing":True})
    add("Я родитель, у меня высшее образование. Сын окончил колледж, ищем ему работу",{"education_level":"vocational"},role="parent")
    add("Работу ребёнку. Мне 40",{},role="parent")
    add("Не знаю, кем работать",{"query":"@unknown"})
    add("Теперь опыт 3 года",{"experience":3})
    add("Больше не нужно жильё",{"housing":"@any"})
    add("Не важен график",{"schedule":"@any"})
    add("Полный рабочий день",{"schedule":"Полный рабочий день"})
    add("Временная работа, мне 19",{"employment":"temporary","age":19})
    assert len(rows)==60
    return rows


async def extraction_checks(limit,delay,output_path,ids):
    results=[]
    for case in cases()[:limit]:
        if ids and case["id"] not in ids: continue
        engine=FlowDialogue(PreviewReminders(),configure_work(initial_config()))
        s=engine.session(1);s.branch="work";s.node="work";s.values["role"]=case["role"]
        engine.work.state(s)["awaiting"]=case["awaiting"]
        try:
            for attempt in range(3):
                try:
                    routed=await engine.route(s,case["text"],engine.nodes["work"])
                    break
                except LLMError as error:
                    if error.code!="rate_limit" or attempt==2: raise
                    print(json.dumps(dict(id=case["id"],waiting_for_rate_limit=True)),flush=True)
                    await asyncio.sleep(30)
            actual=validate_fields(routed.get("work_fields"),case["text"],case["role"])
            simple={k:v["value"] if v["status"]=="known" else "@"+v["status"] for k,v in actual.items()}
            compared=dict(simple);expected=dict(case["expected"])
            for values in (compared,expected):
                if isinstance(values.get("schedule"),str) and not values["schedule"].startswith("@"):
                    values["schedule"]=key(values["schedule"]).removeprefix("только ")
            passed=compared==expected and routed["branch"]=="work"
            result=dict(**case,actual=simple,passed=passed)
            result["extracted"]=routed.get("work_fields")
        except Exception as error:
            result=dict(**case,passed=False,error=type(error).__name__)
        results.append(result)
        Path(output_path).parent.mkdir(parents=True,exist_ok=True)
        Path(output_path).write_text(json.dumps(results,ensure_ascii=False,indent=2),encoding="utf-8")
        print(json.dumps({k:result[k] for k in ("id","passed")}|({"actual":result.get("actual"),"expected":case["expected"]} if not result["passed"] else {}),ensure_ascii=True),flush=True)
        if case["id"]<limit: await asyncio.sleep(delay)
    return results


async def api_checks():
    results=[]
    for name,query,params in [
        ("tomsk_welder","сварщик",dict(region_code="7000000000000")),
        ("tula_no_experience","грузчик",dict(region_code="7100000000000",experience_to=0)),
        ("housing_broad",None,dict(region_code="7000000000000",accommodation=True)),
        ("tomsk_next_page","сварщик",dict(region_code="7000000000000",offset=1)),
    ]:
        try:
            page=await search_vacancies(query,limit=5,**params)
            result=dict(name=name,ok=True,total=page.total,next_offset=page.next_offset,
                        items=[dict(id=j.id,title=j.title,address=j.address,salary_from=j.salary_from,salary_to=j.salary_to,housing=j.accommodation,
                                    education=j.education,employment=j.employment,schedule=j.schedule,url=j.url) for j in page.items])
        except TrudvsemError as error: result=dict(name=name,ok=False,error=error.code)
        results.append(result);print(json.dumps(dict(name=name,ok=result["ok"],count=len(result.get("items",[])))),flush=True)
    return results


async def main(args):
    results=await (api_checks() if args.api else extraction_checks(args.limit,args.delay,args.output,{int(i) for i in args.ids.split(",") if i}))
    path=Path(args.output);path.parent.mkdir(parents=True,exist_ok=True)
    path.write_text(json.dumps(results,ensure_ascii=False,indent=2),encoding="utf-8")
    print(json.dumps(dict(output=str(path),passed=sum(r.get("passed",r.get("ok",False)) for r in results),total=len(results))))


if __name__=="__main__":
    parser=argparse.ArgumentParser();parser.add_argument("--limit",type=int,default=60);parser.add_argument("--api",action="store_true")
    parser.add_argument("--delay",type=float,default=10)
    parser.add_argument("--ids",default="")
    parser.add_argument("--output",default="tmp/work-live-results.json")
    asyncio.run(main(parser.parse_args()))
