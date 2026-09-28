import asyncio
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from project.llm.services import dialogue as d
from project.llm.services.reminders import parse_time
from project.llm.services.understanding import Intent


class MemoryReminders:
    def __init__(self): self.rows = {}
    async def list(self, user): return [r for r in self.rows.values() if r["user_id"] == user]
    async def create(self, user, text, due, zone):
        key = str(len(self.rows) + 1)
        self.rows[key] = dict(id=key, user_id=user, text=text, due_at=due, zone=zone, state="pending")
        return key
    async def get(self, user, key):
        row = self.rows.get(key)
        return row if row and row["user_id"] == user else None
    async def delete(self, user, key):
        if await self.get(user, key): del self.rows[key]
    async def reschedule(self, user, key, due, zone):
        row = await self.get(user, key)
        if not row: return False
        row.update(due_at=due, zone=zone, state="pending")
        return True


def buttons(reply):
    return [b for r in reply for a in r.get("attachments", []) for row in a["payload"]["buttons"] for b in row]


def contents(reply): return "\n".join(r["text"] for r in reply)


def test_skipped_benefits_age_does_not_enable_adult_job_search(monkeypatch):
    async def forbidden(*args, **kwargs): raise AssertionError("Age must be known first")
    monkeypatch.setattr(d, "search_vacancies", forbidden)
    async def scenario():
        bot = d.Dialogue(MemoryReminders())
        s = bot.session(1)
        s.skipped.add("age")
        s.work_fields = {"query": "грузчик", "city": "Тула", "region_code": "7100000000000"}
        reply = await bot.work(s)
        assert s.awaiting == "age" and "Сколько лет" in contents(reply)
    asyncio.run(scenario())


def test_benefit_categories_follow_study_and_family_situation():
    bot = d.Dialogue(MemoryReminders())
    s = bot.session(1)
    s.fields = {"age": 17, "family_status": "guardian", "study": "школа"}
    a = bot.card(s, "benefits")["text"]
    assert "выплаты взрослому" in a and "совершеннолетием" in a
    s.fields = {"age": 19, "family_status": "graduate", "study": "не учусь"}
    b = bot.card(s, "benefits")["text"]
    assert "После выпуска" in b and "• Поддержка во время учёбы" not in b


def test_free_text_can_remove_salary_filter_and_set_parent_role(monkeypatch):
    async def understood(text, context):
        return Intent(topic="work", role="parent", skip_fields=["salary"])
    async def search(*args, **kwargs):
        return SimpleNamespace(items=[job("one", low=20000, high=30000)], next_offset=None)
    monkeypatch.setattr(d, "understand", understood)
    monkeypatch.setattr(d, "search_vacancies", search)
    async def scenario():
        bot = d.Dialogue(MemoryReminders())
        s = bot.session(1)
        s.fields["age"] = 19
        s.work_fields = {"query": "грузчик", "city": "Тула", "region_code": "7100000000000", "experience": 0, "salary": 100000}
        reply = await bot.handle(1, "Я родитель, зарплата неважна")
        assert s.role == "parent" and "salary" not in s.work_fields
        assert any(b["text"] == "Открыть вакансию" for b in buttons(reply))
    asyncio.run(scenario())


def test_reminder_explicit_confirmation_edit_idempotency_and_reset():
    async def scenario():
        store = MemoryReminders()
        bot = d.Dialogue(store)
        await bot.handle(1, payload="rem:new")
        await bot.handle(1, "Подготовить документы")
        await bot.handle(1, "Москва")
        date = (datetime.now(timezone.utc) + timedelta(days=2)).strftime("18:30, %d.%m.%Y")
        reply = await bot.handle(1, date)
        assert not store.rows
        old_save = next(b["payload"] for b in buttons(reply) if b["text"] == "Сохранить")
        await bot.handle(1, payload="rem:editdate")
        assert "уже использована" in contents(await bot.handle(1, payload=old_save))
        reply = await bot.handle(1, date)
        save = next(b["payload"] for b in buttons(reply) if b["text"] == "Сохранить")
        await bot.handle(1, payload=save)
        await bot.handle(1, payload=save)
        assert len(store.rows) == 1
        assert next(iter(store.rows.values()))["due_at"].hour == 15
        await bot.handle(1, payload="reset")
        assert len(store.rows) == 1 and bot.session(1).fields == {}
        await bot.handle(2, payload="delete:1")
        assert len(store.rows) == 1
        await bot.handle(1, payload="delete:1")
        assert not store.rows
    asyncio.run(scenario())


def test_checklist_old_buttons_and_users_are_isolated():
    async def scenario():
        bot = d.Dialogue(MemoryReminders())
        await bot.handle(1, payload="card:education")
        a = bot.session(1).revision
        await bot.handle(2, payload="card:family")
        await bot.handle(2, payload=f"done:{a}:0")
        assert not bot.session(2).done
        await bot.handle(1, payload=f"done:{a}:0")
        assert bot.session(1).done == {0}
        await bot.handle(1, payload="card:money")
        await bot.handle(1, payload=f"done:{a}:1")
        assert not bot.session(1).done
        reply = await bot.handle(1, payload="rem:tasks")
        assert all("Подготовить" not in b["text"] for b in buttons(reply))
    asyncio.run(scenario())


def test_parent_benefits_only_missing_questions(monkeypatch):
    async def understood(text, context):
        return Intent(topic="benefits", age=17, city="Тула", study="школа", family_status="guardian")
    monkeypatch.setattr(d, "understand", understood)
    async def scenario():
        bot = d.Dialogue(MemoryReminders())
        await bot.handle(1, payload="role:parent")
        reply = await bot.handle(1, "synthetic")
        assert "стоит проверить" in contents(reply)
        assert "Сколько лет" not in contents(reply)
        assert bot.session(1).awaiting == ""
        assert "Ребёнку скоро 18" in contents([bot.menu(bot.session(1))]) or any(b["text"] == "Ребёнку скоро 18" for b in buttons([bot.menu(bot.session(1))]))
    asyncio.run(scenario())


def job(identifier, city="Тула", low=50000, high=60000):
    return SimpleNamespace(id=identifier, title="Грузчик", company="Тест", city=None, address=f"г {city}, улица", region="Тульская область",
                           salary_from=low, salary_to=high, experience="0", accommodation=True, url=f"https://trudvsem.ru/vacancy/card/test/{identifier}")


def test_real_filters_and_buffer_preserve_pages(monkeypatch):
    requests = []
    async def search(query, **kwargs):
        requests.append(kwargs)
        return SimpleNamespace(items=[job("wrong", "Омск"), job("low", low=20000, high=30000)] + [job(str(i)) for i in range(7)] + [job("unknown", low=None, high=None)], next_offset=None)
    monkeypatch.setattr(d, "search_vacancies", search)
    async def scenario():
        bot = d.Dialogue(MemoryReminders())
        s = bot.session(1)
        s.topic, s.fields = "work", {"age": 19}
        s.work_fields = {"query": "грузчик", "city": "Тула", "region_code": "7100000000000", "salary": 50000, "experience": 0, "housing": True}
        first = await bot.work(s)
        assert len([b for b in buttons(first) if b["type"] == "link"]) == 5
        second = await bot.handle(1, payload="work:more")
        urls = [b["url"] for b in buttons(first + second) if b["type"] == "link"]
        assert len(urls) == len(set(urls)) == 8
        assert not any(u.endswith("wrong") or u.endswith("low") for u in urls)
        assert "Без указанной зарплаты" in contents(second)
        assert len(requests) == 1 and requests[0]["experience_to"] == 0
        assert requests[0]["accommodation"] is True
    asyncio.run(scenario())


def test_minor_under16_never_searches(monkeypatch):
    async def forbidden(*args, **kwargs): pytest.fail("Adult vacancies must not be searched")
    monkeypatch.setattr(d, "search_vacancies", forbidden)
    async def scenario():
        bot = d.Dialogue(MemoryReminders())
        bot.session(1).fields["age"] = 15
        assert "тест" in contents(await bot.work(bot.session(1)))
    asyncio.run(scenario())


def test_job_location_survives_topic_switch_and_does_not_become_reminder_city(monkeypatch):
    async def understood(text, context): return Intent(topic="education", city="Казань", region_code="1600000000000", subtopic="admission")
    monkeypatch.setattr(d, "understand", understood)
    async def scenario():
        bot = d.Dialogue(MemoryReminders())
        s = bot.session(1)
        s.work_fields = {"city": "Тула", "query": "грузчик"}
        await bot.handle(1, "учёба")
        assert s.work_fields["city"] == "Тула" and s.fields["city"] == "Казань"
        await bot.handle(1, payload="rem:work")
        assert s.pending["stage"] == "city" and "zone" not in s.pending
    asyncio.run(scenario())


def test_two_misses_offer_hh():
    async def scenario():
        bot = d.Dialogue(MemoryReminders())
        a = await bot.handle(1, payload="work:miss")
        b = await bot.handle(1, payload="work:miss")
        assert not any(x["type"] == "link" for x in buttons(a))
        assert any(x.get("url") == "https://hh.ru/" for x in buttons(b))
    asyncio.run(scenario())


@pytest.mark.parametrize("value", ["завтра", "18:00, 31.02.2030", "25:00, 23.09.2026", "18:00, 01.01.2000"])
def test_invalid_reminder_dates(value):
    with pytest.raises(ValueError): parse_time(value, "Europe/Moscow")


def test_timezone_conversion():
    now = datetime(2026, 1, 1, tzinfo=timezone.utc)
    assert parse_time("18:30, 23.09.2026", "Asia/Vladivostok", now=now).hour == 8


def test_source_urls_are_not_llm_fields():
    parsed = Intent.model_validate({"topic": "education", "url": "https://attacker.invalid", "response": "made up benefit"})
    assert "url" not in parsed.model_dump()


def test_callback_role_menus_and_out_of_scope(monkeypatch):
    async def understood(text, context): return Intent(topic="off_topic")
    monkeypatch.setattr(d, "understand", understood)
    async def scenario():
        bot = d.Dialogue(MemoryReminders())
        candidate = await bot.handle(1, payload="role:candidate")
        assert not any(b["text"] == "Работа" for b in buttons(candidate))
        reply = await bot.handle(1, "исторический вопрос")
        assert "Я помогаю" in contents(reply)
    asyncio.run(scenario())
