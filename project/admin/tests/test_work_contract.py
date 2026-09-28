import asyncio
import json
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from project.admin.seed import initial_config
from project.admin.work_draft import configure_work
from project.llm.services import flow
from project.llm.services.geography import resolve
from project.llm.services.work import assess, field, validate_fields


def config(): return configure_work(initial_config())
def bot(): return flow.FlowDialogue(flow.PreviewReminders(),config(),8)
def buttons(replies): return [b for r in replies for a in r.get("attachments",[]) for row in a["payload"]["buttons"] for b in row]
def output(replies): return "\n".join(r["text"] for r in replies)
def data(**items): return json.dumps(dict(branch="work",work_fields=items),ensure_ascii=False)
def job(id="1",**updates):
    values=dict(id=id,title="Сварщик",company="Тестовый работодатель",address="Томская область, г Томск",city="Томск",region="Томская область",
                salary_from=60000,salary_to=70000,experience="0",accommodation=True,url="https://trudvsem.ru/vacancy/card/test/"+id,
                education="Среднее профессиональное",schedule="Сменный график",employment="Частичная занятость")
    values.update(updates);return SimpleNamespace(**values)


@pytest.mark.parametrize("text,name,value",[
    ("сварщик","query","сварщик"),("сварщиком","query","сварщик"),("Томск","city","Томск"),
    ("в Томске","city","Томск"),("мне 20","age",20),("сыну 17","age",17),
    ("после колледжа","education_level","vocational"),("нет профессионального образования","education_level","no_professional"),
    ("окончил вуз","education_level","higher"),("11 классов","education_level","general"),
    ("без опыта","experience",0),("2 года опыта","experience",2),("подработка","employment","part_time"),
    ("полная занятость","employment","full_time"),("временная работа","employment","temporary"),
    ("вечером","schedule","вечером"),("сменный график","schedule","сменный график"),
    ("от 60 тысяч","salary",60000),("60000","salary",60000),("с жильём","housing",True),
])
@pytest.mark.parametrize("prefix",["","Ищу работу: ","Уточнение: "])
def test_sixty_grounded_extractions(text,name,value,prefix):
    parsed=validate_fields({name:field("known",value,text)},prefix+text,"child")
    assert parsed[name]["value"]==value


@pytest.mark.parametrize("raw,text",[
    ({"region_code":field("known","9900000000000","Томск")},"Томск"),
    ({"query":field("known","курьер","сварщик")},"сварщик"),
    ({"age":field("known",40,"мне 40")},"мне 40, сыну 17"),
    ({"salary":field("known",90000,"60000")},"60000"),
    ({"city":field("known","Москва","Тула")},"Тула"),
])
def test_model_cannot_invent_values(raw,text):
    parsed=validate_fields(raw,text,"parent" if "сын" in text else "child")
    if "сын" in text: assert parsed["age"]["value"]==17
    else: assert not any(item["status"]=="known" for item in parsed.values())


def test_geography_is_from_directory_and_ambiguous_places_stay_ambiguous():
    assert resolve("Томск")[0]["code"]=="7000000000000"
    assert len(resolve("Советск"))==3
    assert resolve("Советск","Тульская область")[0]["code"]=="7100000000000"
    assert resolve("Неведомый город")==[]
    assert len(resolve("Томске"))==1


@pytest.mark.parametrize("changes,values,reason,missing",[
    ({"salary_from":30000},{"salary":60000},None,"нижняя граница"),
    ({"salary_from":None,"salary_to":None},{"salary":60000},None,"не указана"),
    ({"salary_from":30000,"salary_to":40000},{"salary":60000},"Зарплата ниже выбранной",None),
    ({"accommodation":None},{"housing":True},None,"проживание"),
    ({"accommodation":False},{"housing":True},"Жильё не предоставляется",None),
    ({"employment":None},{"employment":"part_time"},None,"занятость"),
    ({"employment":"Полная занятость"},{"employment":"part_time"},"Другой тип занятости",None),
    ({"education":None},{"education_level":"vocational"},None,"образованию"),
    ({"education":"Высшее"},{"education_level":"vocational"},"Требуется другой уровень образования",None),
    ({"schedule":None},{"schedule":"сменный график"},None,"график"),
    ({"schedule":"Полный рабочий день"},{"schedule":"сменный график"},"Другой график",None),
    ({"experience":None},{"experience":0},None,"опыт"),
    ({"experience":"2"},{"experience":0},"Требуется больший опыт",None),
    ({"address":None,"city":None},{},None,"место"),
])
def test_sparse_and_mismatching_cards(changes,values,reason,missing):
    rejected,unknown=assess(job(**changes),values,resolve("Томск")[0])
    assert rejected==reason
    if missing: assert missing in "; ".join(unknown)


def test_minor_does_not_get_vacancies_requiring_qualifications_or_experience():
    place = resolve("Томск")[0]
    assert assess(job(education="Высшее образование", experience="Без опыта"), {"age": 17}, place)[0] == "Требуется профессиональное образование"
    assert assess(job(education="Среднее профессиональное", experience="Без опыта"), {"age": 17}, place)[0] == "Требуется профессиональное образование"
    assert assess(job(education="Не требуется", experience="От 2 лет"), {"age": 17}, place)[0] == "Требуется опыт работы"
    assert assess(job(education="Не требуется", experience=None, requirements="Опыт работы от 2 лет"), {"age": 17}, place)[0] == "Требуется опыт работы"
    assert assess(job(education="Не требуется", experience="Без опыта"), {"age": 17}, place)[0] is None
    assert assess(job(education="Высшее образование", experience="Без опыта"), {"age": 18}, place)[0] is None


def test_full_dialogues(monkeypatch):
    requests=[];responses={};fail_api=False
    async def llm(messages,**kwargs):
        text=json.loads(messages[-1]["content"])["message"]
        if text=="ошибка модели": raise flow.LLMError("timeout")
        if text=="сломанный ответ": return "not json"
        return responses[text]
    async def search(query=None,**kwargs):
        requests.append((query,kwargs))
        if fail_api: raise flow.TrudvsemError("timeout")
        return SimpleNamespace(items=[job(str(i)) for i in range(8)],next_offset=None)
    monkeypatch.setattr(flow,"call_llm",llm);monkeypatch.setattr(flow,"search_vacancies",search)
    def add(text,**values): responses[text]=data(**values)
    add("томск сварщик",city=field("known","Томск","томск"),query=field("known","сварщик","сварщик"))
    add("Томск",city=field("known","Томск","Томск"))
    add("Сварщик",query=field("known","сварщик","Сварщик"))
    add("мне 20, после колледжа, подработка от 60 тысяч",age=field("known",20,"мне 20"),education_level=field("known","vocational","после колледжа"),employment=field("known","part_time","подработка"),salary=field("known",60000,"от 60 тысяч"))
    add("ищу любую работу с жильём",query=field("any",None,"любую работу"),housing=field("known",True,"с жильём"))
    add("профессия не важна",query=field("any",None,"профессия не важна"))
    add("мне 40, сыну 17, работа в Томске",age=field("known",17,"сыну 17"),city=field("known","Томск","Томске"))
    responses["мне 40, сыну 17, работа в Томске"]=json.dumps({**json.loads(responses["мне 40, сыну 17, работа в Томске"]),"values":{"role":"parent"}})
    add("теперь Тула",city=field("known","Тула","Тула"))
    add("теперь зарплата неважна",salary=field("any",None,"зарплата неважна"))
    add("не знаю",query=field("unknown",None,"не знаю"))
    add("Советск, мне 20",city=field("known","Советск","Советск"),age=field("known",20,"мне 20"))
    add("Тульская область",city=field("known","Тульская область","Тульская область"))
    add("город Неведомый",city=field("known","Неведомый","Неведомый"))
    add("полный день и только смены",schedule=field("clarify",None,"полный день и только смены"))
    add("сменный график",schedule=field("known","сменный график","сменный график"))
    responses["посмотрю завтра"]=json.dumps(dict(branch="work",work_action="remind"))
    async def run():
        nonlocal fail_api
        # 1–3: compound regression, only city, only profession.
        for texts in (["томск сварщик"],["Томск"],["Сварщик","Томск"]):
            b=bot();await b.handle(1,payload="jump:work")
            for text in texts: reply=await b.handle(1,text)
            assert b.session(1).node=="work_search" and b.session(1).work['fields']['age']['value'] is None
        # 4: conditions in parts, including mandatory reported phrase.
        b=bot();await b.handle(1,"мне 20, после колледжа, подработка от 60 тысяч")
        await b.handle(1,"Томск")
        assert b.session(1).values["salary"]==60000 and requests[-1][0] is None
        # 5–6: any profession, explicit housing; exact 'not important' phrase.
        for text in ("ищу любую работу с жильём","профессия не важна"):
            b=bot();await b.handle(1,payload="jump:work");await b.handle(1,text);await b.handle(1,"Томск");await b.handle(1,"мне 20")
            assert requests[-1][0] is None and b.session(1).work["fields"]["query"]["status"]=="any"
        # 7: parent age belongs to applicant.
        b=bot();reply=await b.handle(1,"мне 40, сыну 17, работа в Томске")
        assert b.session(1).values["age"]==17 and "несовершеннолетнему" in output(reply)
        # 8–9: corrections restart pages and preserve other conditions; remove salary.
        b=bot();await b.handle(1,"мне 20, после колледжа, подработка от 60 тысяч");await b.handle(1,"Томск")
        await b.handle(1,"теперь Тула")
        assert requests[-1][1]["region_code"]=="7100000000000" and requests[-1][1]["offset"]==0
        assert b.session(1).values["salary"]==60000
        await b.handle(1,"теперь зарплата неважна")
        assert b.session(1).values["salary"] is None
        # 10–11: same-name city by button and by region text.
        for choose_text in (False,True):
            b=bot();reply=await b.handle(1,"Советск, мне 20")
            before=len(requests);assert len(b.session(1).work["choices"])==3
            if choose_text: await b.handle(1,"Тульская область")
            else:
                chosen=next(v for v in buttons(reply) if "Тульская" in v["text"])
                await b.handle(1,payload=chosen["payload"])
            assert len(requests)>before and requests[-1][1]["region_code"]=="7100000000000"
        # 12: search starts without asking age or announcing its absence.
        b=bot();reply=await b.handle(1,"Томск")
        assert "Возраст не указан" not in output(reply) and b.session(1).node=="work_search"
        reply=await b.handle(1,payload="jump:work_more");assert "Сколько лет" not in output(reply)
        # 13: unknown place never becomes a guessed region.
        b=bot();before=len(requests);await b.handle(1,"город Неведомый")
        assert len(requests)==before and b.session(1).work["fields"]["city"]["status"]=="clarify"
        # 14: conflict blocks search until resolved.
        b=bot();await b.handle(1,"Томск");await b.handle(1,"мне 20");before=len(requests)
        await b.handle(1,"полный день и только смены");assert len(requests)==before
        await b.handle(1,"сменный график");assert len(requests)>before
        # 15–16: separate timeout/invalid JSON leave prior fields intact.
        for text in ("ошибка модели","сломанный ответ"):
            b=bot();await b.handle(1,"томск сварщик");before=dict(b.session(1).values)
            reply=await b.handle(1,text)
            assert b.session(1).values==before and "Ранее названные" in output(reply)
        # 17: source error and retry, no fake results.
        b=bot();await b.handle(1,"томск сварщик");fail_api=True;reply=await b.handle(1,"мне 20")
        assert "Условия сохранены" in output(reply) and "Сварщик\n" not in output(reply)
        fail_api=False;await b.handle(1,payload=next(v["payload"] for v in buttons(reply) if v["text"]=="Повторить поиск"))
        assert b.session(1).values["query"]=="сварщик"
        # 18: more has no duplicates and retains request conditions.
        b=bot();await b.handle(1,"Томск");first=await b.handle(1,"мне 20");second=await b.handle(1,payload="jump:work_more")
        urls=lambda replies:{v["url"] for v in buttons(replies) if "url" in v}
        assert len(urls(first))==5 and len(urls(second))==3 and not urls(first)&urls(second)
        assert not any(v["text"]=="Показать ещё" for v in buttons(second))
        # 19: misses do not mutate conditions; HH appears after two misses.
        before=dict(b.session(1).values);await b.handle(1,payload="jump:work_miss");reply=await b.handle(1,payload="jump:work_miss")
        assert before==b.session(1).values and "https://hh.ru/search/vacancy" in output(reply)
        assert "https://www.superjob.ru/vacancy/search/" in output(reply)
        # 20: reminder intent, explicit location/date, confirmation, no MAX store.
        reply=await b.handle(1,"посмотрю завтра");await b.handle(1,payload=buttons(reply)[0]["payload"])
        await b.handle(1,"Москва")
        reply=await b.handle(1,(datetime.now(timezone.utc)+timedelta(days=3)).strftime("18:30, %d.%m.%Y"))
        assert not b.reminders.rows
        await b.handle(1,payload=next(v["payload"] for v in buttons(reply) if v["text"]=="Сохранить"))
        assert len(b.reminders.rows)==1
    asyncio.run(run())


def test_all_nine_conditions_and_api_parameters(monkeypatch):
    text="Мне 20, окончил колледж. Томск, сварщик, опыт 1 год, подработка, сменный график, от 60000, с проживанием"
    updates={name:field("known",value,evidence) for name,value,evidence in [
        ("age",20,"Мне 20"),("education_level","vocational","окончил колледж"),("city","Томск","Томск"),
        ("query","сварщик","сварщик"),("experience",1,"опыт 1 год"),("employment","part_time","подработка"),
        ("schedule","сменный график","сменный график"),("salary",60000,"от 60000"),("housing",True,"с проживанием")]}
    async def llm(*args,**kwargs): return data(**updates)
    async def search(query,**kwargs):
        assert query=="сварщик"
        assert kwargs.pop('place')['name']=='Томск'
        assert kwargs.pop('source_offsets')=={}
        assert kwargs==dict(region_code="7000000000000",experience_to=1,accommodation=True,limit=100,offset=0)
        return SimpleNamespace(items=[job("complete",requirements='Возраст от 16 лет'),job("sparse",salary_from=None,salary_to=None,accommodation=None,education=None,employment=None,schedule=None,experience=None)],next_offset=None)
    monkeypatch.setattr(flow,"call_llm",llm);monkeypatch.setattr(flow,"search_vacancies",search)
    async def run():
        b=bot();reply=await b.handle(1,text)
        assert len([i for i in b.session(1).work["fields"].values() if i["status"]=="known"])==9
        assert len([v for v in buttons(reply) if "url" in v])==2
        content=output(reply)
        assert content.index("соответствуют выбранным")<content.index("требуют уточнения")
        assert "проживание в карточке не подтверждено" in content and "Сколько лет" not in content
    asyncio.run(run())


def test_partial_scan_empty_then_more_and_mid_scan_error(monkeypatch):
    requested=[];fail=True
    async def search(query,**kwargs):
        nonlocal fail
        offset=kwargs["offset"];requested.append(offset)
        if offset==1 and fail:
            fail=False;raise flow.TrudvsemError("connection")
        return SimpleNamespace(items=[job(str(offset),city="Москва",address="г Москва")],next_offset=offset+1 if offset<4 else None)
    monkeypatch.setattr(flow,"search_vacancies",search)
    async def run():
        b=bot();s=b.session(1)
        b.work.apply(s,{"age":field("known",20,"20"),"city":field("known","Томск","Томск")},"20 Томск")
        reply=await b.enter(1,s,"work_search")
        assert requested==[0] and s.offset==1 and "В просмотренной части" in output(reply)
        assert any(v["text"]=="Показать ещё" for v in buttons(reply))
        reply=await b.handle(1,payload="jump:work_more")
        assert s.offset==1 and "Условия сохранены" in output(reply)
        reply=await b.handle(1,payload=next(v["payload"] for v in buttons(reply) if v["text"]=="Повторить поиск"))
        assert requested==[0,1,1]
        assert "В просмотренной части" in output(reply) and s.offset==2
        assert any(v["text"]=="Показать ещё" for v in buttons(reply))
        reply=await b.handle(1,payload="jump:work_more")
        assert requested==[0,1,1,2] and s.offset==3
    asyncio.run(run())


def test_first_work_result_never_reads_second_page_to_fill_cards(monkeypatch):
    requested=[]
    async def search(query,**kwargs):
        requested.append(kwargs['offset'])
        return SimpleNamespace(items=[job(str(kwargs['offset']))],next_offset=kwargs['offset']+1)
    monkeypatch.setattr(flow,'search_vacancies',search)
    async def run():
        b=bot();s=b.session(1)
        b.work.apply(s,{"age":field("known",20,"20"),"city":field("known","Томск","Томск")},"20 Томск")
        reply=await b.enter(1,s,"work_search")
        assert requested==[0]
        assert len([v for v in buttons(reply) if 'url' in v])==1
        assert any(v['text']=='Показать ещё' for v in buttons(reply))
    asyncio.run(run())


def test_max_runner_uses_the_same_work_engine(monkeypatch):
    from project.llm.bot import Runner
    events=[];sent=[]
    class Scenarios:
        async def read(self,published=False):
            assert published
            return dict(config=config(),revision=8)
        async def event(self,kind,branch): events.append((kind,branch))
    from project.llm.tests.fake_max import RecordingMax
    class Api(RecordingMax):
        def __init__(self): super().__init__(sent)
    async def llm(*args,**kwargs): return data(city=field("known","Томск","томск"),query=field("known","сварщик","сварщик"))
    async def search(*args,**kwargs): return SimpleNamespace(items=[],next_offset=None)
    monkeypatch.setattr(flow,"call_llm",llm);monkeypatch.setattr(flow,"search_vacancies",search)
    async def run():
        dialogue=flow.PublishedDialogue(flow.PreviewReminders(),Scenarios())
        runner=Runner(Api(),dialogue,dialogue.reminders,"synthetic-test")
        for i,text in enumerate(["томск сварщик"]):
            await runner.process(dict(update_type="message_created",timestamp=runner.started_ms+1,
                                      message=dict(recipient=dict(chat_type="dialog"),sender=dict(user_id=999),body=dict(text=text,mid=f"synthetic-{i}"))))
        assert dialogue.sessions[999].values["city"]=="Томск"
        assert dialogue.sessions[999].work['fields']['age']['value'] is None
        assert "Сколько лет" not in output(sent) and any(kind=="search" for kind,_ in events)
    asyncio.run(run())


@pytest.mark.parametrize("text,name,value",[("Мне двадцать лет","age",20),("опыта нет","experience",0),("без образования","education_level","no_professional")])
def test_live_extraction_regressions(text,name,value):
    raw_value="unspecified" if name=="education_level" else value
    assert validate_fields({name:field("known",raw_value,text)},text,"child")[name]["value"]==value


def test_unknown_profession_does_not_mean_any_profession():
    text="Не знаю, кем работать"
    parsed=validate_fields({"query":field("any",None,text)},text,"child")
    assert parsed["query"]["status"]=="unknown"


def test_work_questions_honor_editor_text_next_and_routes(monkeypatch):
    async def llm(*args,**kwargs): return data(query=field("known","сварщик","сварщик"))
    monkeypatch.setattr(flow,"call_llm",llm)
    async def run():
        edited=config();nodes={n["id"]:n for n in edited["nodes"]}
        nodes["work_city"]["text"]="Выберите место для поиска вакансий."
        nodes["work_city"]["next"]="money"
        b=flow.FlowDialogue(flow.PreviewReminders(),edited,8)
        reply=await b.handle(1,"сварщик")
        assert "Выберите место для поиска вакансий." in output(reply)
        await b.handle(1,"Томск")
        assert b.session(1).branch=="money"
        nodes["work_city"]["routes"]=[dict(field="query",op="eq",value="сварщик",target="education")]
        b=flow.FlowDialogue(flow.PreviewReminders(),edited,8)
        await b.handle(1,"сварщик")
        assert b.session(1).branch=="education"
    asyncio.run(run())


def test_parent_role_and_omitted_profession_in_preview_regression(monkeypatch):
    async def llm(*args,**kwargs):
        return data(age=field("known",17,"сыну 17"),city=field("known","Тула","Туле"),query=field("unknown",None,"ищем ему работу"))
    async def search(*args,**kwargs): return SimpleNamespace(items=[],next_offset=None)
    monkeypatch.setattr(flow,"call_llm",llm);monkeypatch.setattr(flow,"search_vacancies",search)
    async def run():
        b=bot();await b.handle(1,payload="jump:work")
        await b.handle(1,"Мне 40, сыну 17, ищем ему работу в Туле")
        assert b.session(1).values["role"]=="parent"
        assert b.session(1).values["age"]==17 and b.session(1).node=="work_search"
    asyncio.run(run())


def test_model_defaults_do_not_erase_conditions_or_block_city_question(monkeypatch):
    async def llm(*args,**kwargs):
        return data(age=field("known",20,"мне 20"),education_level=field("known","vocational","после колледжа"),
                    employment=field("known","part_time","подработка"),salary=field("known",60000,"от 60 тысяч"),
                    query=field("any",None,""),city=field("known",None,""))
    monkeypatch.setattr(flow,"call_llm",llm)
    async def run():
        b=bot();await b.handle(1,payload="jump:work")
        answer=await b.handle(1,"мне 20, после колледжа, подработка от 60 тысяч")
        assert b.session(1).node=="work_city" and "В каком городе" in output(answer)
        assert b.session(1).work["fields"]["query"]["status"]=="unknown"
    asyncio.run(run())


def test_literal_remote_work_condition_is_not_lost():
    assert validate_fields({},"удалённо","child")["schedule"]["value"]=="удалённо"


def test_work_reminder_local_time_snooze_and_cancel():
    async def run():
        b=bot();await b.handle(1,payload="newrem");await b.handle(1,"Посмотреть вакансии");await b.handle(1,"Томск")
        date=(datetime.now(timezone.utc)+timedelta(days=3)).strftime("18:30, %d.%m.%Y")
        reply=await b.handle(1,date)
        assert "UTC+7" in output(reply) and "Etc/GMT" not in output(reply)
        assert not b.reminders.rows
        await b.handle(1,payload=next(v["payload"] for v in buttons(reply) if v["text"]=="Сохранить"))
        key,row=next(iter(b.reminders.rows.items()));assert row["due_at"].hour==11
        await b.handle(1,payload="snooze:"+key)
        newdate=(datetime.now(timezone.utc)+timedelta(days=4)).strftime("19:30, %d.%m.%Y")
        reply=await b.handle(1,newdate)
        await b.handle(1,payload=next(v["payload"] for v in buttons(reply) if v["text"]=="Сохранить"))
        assert len(b.reminders.rows)==1 and b.reminders.rows[key]["due_at"].hour==12
        await b.handle(1,payload="delete:"+key);assert not b.reminders.rows
        await b.handle(1,payload="newrem");await b.handle(1,payload="cancel");assert not b.reminders.rows
    asyncio.run(run())
