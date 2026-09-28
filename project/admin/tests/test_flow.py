import asyncio
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from project.admin.seed import initial_config
from project.llm.services import flow
from project.llm.services.scenario import validate, ScenarioError


def buttons(replies):
    return [b for r in replies for a in r.get("attachments",[]) for row in a["payload"]["buttons"] for b in row]


def test_seed_has_no_broken_targets(): validate(initial_config())


def test_removed_node_cannot_publish_until_references_fixed():
    config=initial_config();config["nodes"]=[n for n in config["nodes"] if n["id"]!="work_city"]
    with pytest.raises(ScenarioError,match="work_city"): validate(config)


def test_edit_text_button_and_transition_used_by_runtime():
    async def run():
        c=initial_config();n=next(n for n in c["nodes"] if n["id"]=="welcome")
        n["text"]="Моё приветствие";n["buttons"][0].update(label="Моя кнопка",target="money")
        bot=flow.FlowDialogue(flow.PreviewReminders(),validate(c),7)
        replies=await bot.handle(1,payload="reset")
        assert replies[0]["text"]=="Моё приветствие"
        b=buttons(replies)[0];assert b["text"]=="Моя кнопка"
        await bot.handle(1,payload=b["payload"])
        assert bot.session(1).node=="money"
        stale=await bot.handle(1,payload="g:6:welcome:0")
        assert "неактивна" in stale[0]["text"]
    asyncio.run(run())


def test_question_sequence_and_condition_edit_without_llm(monkeypatch):
    async def forbidden(*args,**kwargs): raise AssertionError("Number entry must not call LLM")
    monkeypatch.setattr(flow,"call_llm",forbidden)
    async def run():
        c=initial_config();n=next(n for n in c["nodes"] if n["id"]=="work_age")
        n["next"]="money"
        money=next(n for n in c["nodes"] if n["id"]=="money")
        money["routes"]=[dict(field="age",op="eq",value=88,target="forms")]
        bot=flow.FlowDialogue(flow.PreviewReminders(),c)
        await bot.handle(1,payload="jump:work_age")
        await bot.handle(1,"88")
        assert bot.session(1).node=="forms"
    asyncio.run(run())


def test_router_salvages_optional_fields_and_uses_json_mode(monkeypatch):
    async def mocked(messages,**kwargs):
        assert kwargs["json_mode"]
        return '```json\n{"branch":"work","values":{"age":"19","city":"Тула","region_code":71,"salary":null,"query":"грузчик","experience":"oops","url":"https://fake"}}\n```'
    monkeypatch.setattr(flow,"call_llm",mocked)
    async def run():
        bot=flow.FlowDialogue(None,initial_config())
        parsed=await bot.route(bot.session(1),"test",{})
        assert parsed["values"]["region_code"]=="7100000000000"
        assert parsed["values"]["age"]==19 and "salary" in parsed["values"]
        assert "experience" not in parsed["values"] and "url" not in parsed["values"]
    asyncio.run(run())


def test_reminder_confirmation_user_isolation_and_editor_template():
    async def run():
        c=initial_config();c["ui"]["reminder_saved"]["text"]="Записали: {date}"
        store=flow.PreviewReminders();bot=flow.FlowDialogue(store,c)
        await bot.handle(1,payload="newrem")
        await bot.handle(1,"Документы")
        await bot.handle(1,"Москва")
        date=(datetime.now(timezone.utc)+timedelta(days=2)).strftime("18:30, %d.%m.%Y")
        replies=await bot.handle(1,date)
        save=next(b["payload"] for b in buttons(replies) if b["text"]=="Сохранить")
        assert not store.rows
        replies=await bot.handle(1,payload=save)
        assert replies[0]["text"].startswith("Записали:")
        await bot.handle(1,payload=save);assert len(store.rows)==1
        key=next(iter(store.rows));await bot.handle(2,payload="delete:"+key)
        assert len(store.rows)==1
        await bot.handle(1,payload="delete:"+key);assert not store.rows
    asyncio.run(run())


def test_search_diagnostics_matches_actual_rejections(monkeypatch):
    def job(id,city,salary): return SimpleNamespace(id=id,title="Грузчик",company="Тест",address="г "+city,city=city,region="Тест",salary_from=salary,salary_to=salary,experience="0",accommodation=False,url="https://trudvsem.ru/vacancy/"+id,education=None,schedule=None,employment=None)
    async def search(*args,**kwargs): return SimpleNamespace(items=[job("1","Москва",50000),job("2","Тула",20000),job("3","Тула",60000)],next_offset=None)
    monkeypatch.setattr(flow,"search_vacancies",search)
    async def run():
        bot=flow.FlowDialogue(None,initial_config());s=bot.session(1)
        s.values.update(age=19,query="грузчик",city="Тула",region_code="7100000000000",salary=40000)
        replies=await bot.enter(1,s,"work_search")
        diag=next(t for t in s.trace if t["kind"]=="search")
        assert diag["shown"]==1 and diag["excluded_count"]==2
        assert set(diag["excluded_reasons"])=={"Другой город","Зарплата ниже выбранной"}
        assert sum(b["text"]=="Открыть вакансию" for b in buttons(replies))==1
    asyncio.run(run())


def test_material_answer_uses_only_curated_sources_and_rules(monkeypatch):
    async def mocked(messages,**kwargs):
        assert "MY_RULE" in messages[0]["content"]
        assert 'material_' in messages[1]["content"]
        return '{"text":"Объяснение","sources":["sfr","evil","https://evil.example"]}'
    monkeypatch.setattr(flow,"call_llm",mocked)
    async def run():
        c=initial_config();c["rules"]["tone"]="MY_RULE"
        bot=flow.FlowDialogue(None,c);s=bot.session(1);s.branch="benefits"
        replies=await bot.answer(s,"Вопрос")
        urls=[b["url"] for b in buttons(replies) if b.get("url")]
        assert urls==[c["sources"]["sfr"]["url"]]
    asyncio.run(run())


def test_preview_templates_cannot_execute_python():
    assert flow.fill('{city} {__import__} {secret.attr}',{'city':'Тула'})=='Тула  {secret.attr}'


def test_network_failure_still_allows_explicit_job_request(monkeypatch):
    async def unavailable(*args,**kwargs): raise flow.LLMError("timeout")
    async def search(*args,**kwargs): return SimpleNamespace(items=[],next_offset=None)
    monkeypatch.setattr(flow,"call_llm",unavailable);monkeypatch.setattr(flow,"search_vacancies",search)
    async def run():
        bot=flow.FlowDialogue(None,initial_config())
        result=await bot.handle(1,"Мне 19, ищу работу грузчиком в Туле, без опыта, зарплата неважна")
        s=bot.session(1)
        assert s.node=="work_search" and s.values["salary"] is None
        assert any(t["kind"]=="fallback" for t in s.trace)
        assert "просмотренной части" in result[0]["text"]
    asyncio.run(run())


def test_known_city_and_age_path_needs_no_llm(monkeypatch):
    async def forbidden(*args,**kwargs): raise AssertionError("LLM should not run")
    async def search(*args,**kwargs): return SimpleNamespace(items=[],next_offset=None)
    monkeypatch.setattr(flow,"call_llm",forbidden);monkeypatch.setattr(flow,"search_vacancies",search)
    async def run():
        bot=flow.FlowDialogue(None,initial_config())
        await bot.handle(1,payload="jump:work")
        for text in ["грузчик","Тула","19","без опыта","неважно"]: await bot.handle(1,text)
        assert bot.session(1).node=="work_search"
    asyncio.run(run())


def test_work_without_profession_uses_available_conditions(monkeypatch):
    async def router(*args, **kwargs):
        return '{"branch":"work","values":{"city":"Москва","age":18,"salary":70000}}'
    async def search(self,s,n):
        assert s.values["query"] is None
        assert s.values["region_code"] == "7700000000000"
        assert s.values["salary"] == 70000
        return [{"text":"BROAD SEARCH"}]
    monkeypatch.setattr(flow,"call_llm",router)
    monkeypatch.setattr(flow.FlowDialogue,"search",search)
    async def run():
        c=initial_config()
        next(n for n in c["nodes"] if n["id"]=="work")["required"]=True
        bot=flow.FlowDialogue(flow.PreviewReminders(),c)
        await bot.handle(1,payload="jump:work")
        r=await bot.handle(1,"Москва, 18 лет, 70 тысяч")
        assert r[0]["text"] == "BROAD SEARCH"
    asyncio.run(run())


def test_education_and_employment_constraints():
    check=flow.FlowDialogue.work_constraint
    job=SimpleNamespace(education="Высшее образование",employment="Полная занятость")
    assert check(job,{"education_level":"no_professional"})
    assert check(job,{"education_level":"vocational"})
    assert check(job,{"education_level":"higher"}) is None
    assert check(job,{"employment":"part_time"})
    job.education=None;job.employment=None
    assert check(job,{"education_level":"no_professional","employment":"part_time"}) is None


def test_short_education_reply_is_not_a_profession(monkeypatch):
    async def router(*args,**kwargs):
        return '{"branch":"work","values":{"education_level":"no_professional","employment":"part_time"}}'
    monkeypatch.setattr(flow,"call_llm",router)
    async def run():
        bot=flow.FlowDialogue(flow.PreviewReminders(),initial_config())
        await bot.handle(1,payload="jump:work")
        await bot.handle(1,"Без образования, подработка")
        values=bot.session(1).values
        assert values["query"] is None
        assert values["education_level"]=="no_professional"
        assert values["employment"]=="part_time"
    asyncio.run(run())


def test_tomsk_job_request_keeps_city_even_when_router_omits_it(monkeypatch):
    async def router(*args, **kwargs):
        return '{"branch":"work","values":{"query":"сварщик"}}'
    async def search(self, s, n):
        assert s.values["city"] == "Томск"
        assert s.values["region_code"] == "7000000000000"
        assert s.values["age"] == 20
        return [{"text":"SEARCHED"}]
    monkeypatch.setattr(flow,"call_llm",router)
    monkeypatch.setattr(flow.FlowDialogue,"search",search)
    async def run():
        bot=flow.FlowDialogue(flow.PreviewReminders(),initial_config())
        await bot.handle(1,payload="jump:work")
        answer=await bot.handle(1,"томск сварщик")
        assert "Сколько лет" in answer[0]["text"]
        await bot.handle(1,"20")
        await bot.handle(1,"не знаю")
        answer=await bot.handle(1,"не знаю")
        assert answer[0]["text"] == "SEARCHED"
        assert bot.session(1).values["city"] == "Томск"
    asyncio.run(run())


def test_unknown_city_is_preserved_when_region_code_missing():
    async def run():
        bot=flow.FlowDialogue(flow.PreviewReminders(),initial_config())
        s=bot.session(1)
        s.values.update(city="Неизвестный город",age=20)
        answer=await bot.enter(1,s,"work_search")
        assert "уточни регион" in answer[0]["text"]
        assert s.values["city"] == "Неизвестный город"
    asyncio.run(run())
