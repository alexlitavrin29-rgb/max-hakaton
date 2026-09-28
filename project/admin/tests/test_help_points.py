"""The help branch chooses places from the prepared catalog, never model prose."""

import asyncio

from project.admin.help_points_draft import configure
from project.admin.seed import initial_config
from project.llm.services.flow import FlowDialogue, PreviewReminders
from project.llm.services.help_points import HelpPointsBranch, catalog
from project.llm.services import help_points
from project.llm.services.rental import RentalBranch


def engine():
    return FlowDialogue(PreviewReminders(), configure(initial_config()), revision=7)


def buttons(item):
    return [row[0] for row in item.get("attachments", [{}])[0].get("payload", {}).get("buttons", [])]


async def start(category="food"):
    bot = engine()
    session = bot.session(1)
    session.values["role"] = "child"
    await bot.enter(1, session, "help_points")
    session.values["help_category"] = category
    await bot.enter(1, session, "hp_city")
    return bot


def test_exact_city_then_region_fallback():
    async def run():
        bot = await start("food")
        exact = await bot.handle(1, text="Санкт-Петербург")
        assert "населённого пункта «Санкт-Петербург»" in exact[0]["text"]
        assert all("Город: Санкт-Петербург" in item["text"] for item in exact[1:-1])
        bot = await start("food")
        fallback = await bot.handle(1, text="Кингисепп")
        assert "населённого пункта «Кингисепп»" in fallback[0]["text"]
        assert "Ленинградская" in fallback[0]["text"]
        assert any("Город: Гатчина" in item["text"] for item in fallback[1:-1])
    asyncio.run(run())


def test_region_points_keep_published_category_city_order(monkeypatch):
    bot = engine()
    session = bot.session(1)
    branch = HelpPointsBranch(bot)
    session.help_points.update(category='food', catalog=[
        dict(region_code='4700000000000', city=city, city_key=city,
             name=city, categories=['food'], flags={'food_hot_meal': 'YES'})
        for city in ('Яркий', 'Альфа')])
    monkeypatch.setattr(branch, 'show', lambda session: session.help_points['matches'])
    result = branch.select(session, dict(name='Исходный', code='4700000000000', kind='city'))
    assert [point['city'] for point in result] == ['Альфа', 'Яркий']


def test_ambiguous_city_requires_region_choice():
    async def run():
        bot = await start("food")
        choices = await bot.handle(1, text="Тула")
        labels = [item["text"] for item in buttons(choices[0])]
        assert any("Новосибирская" in item for item in labels)
        assert any("Тульская" in item for item in labels)
        selected = next(item["payload"] for item in buttons(choices[0]) if "Тульская" in item["text"])
        result = await bot.handle(1, payload=selected)
        # The elderly-only Tula cafe was removed from the youth catalog.
        assert "пока нет проверенного места" in result[0]["text"]
        assert bot.session(1).help_points["place"]["code"].startswith("71")
    asyncio.run(run())


def test_city_stays_in_choices_when_many_villages_share_its_name():
    async def run():
        bot = await start("food")
        await bot.handle(1, text="Рыбное")
        choices = bot.session(1).help_points["choices"]
        assert any(place["type"] == "Город" and place["name"] == "Рыбное"
                   and "Рязанская" in place["region"] for place in choices)
    asyncio.run(run())


def test_llm_extracts_city_from_phrase_but_registry_selects_region(monkeypatch):
    async def extracted(*args, **kwargs):
        return '{"city":"Выборг","region":""}'

    monkeypatch.setattr(help_points, "call_llm", extracted)

    async def run():
        bot = await start("food")
        result = await bot.handle(1, text="Я сейчас в Выборге")
        assert "Выборг" in result[0]["text"]
        assert bot.session(1).help_points["place"]["code"].startswith("47")
    asyncio.run(run())


def test_more_button_continues_same_result_list():
    async def run():
        bot = await start("food")
        first = await bot.handle(1, text="Санкт-Петербург")
        more = next(item["payload"] for item in buttons(first[-1]) if item["text"].startswith("Ещё места"))
        second = await bot.handle(1, payload=more)
        assert "Показано 8 из" in second[-1]["text"]
        assert bot.session(1).help_points["place"]["code"].startswith("78")
    asyncio.run(run())


def test_broken_link_uses_phone_without_link_button():
    point = next(point for point in catalog() if not point["url"] and point["phone"])
    item = HelpPointsBranch(engine()).card(point, point["categories"][0])
    assert "Телефон:" in item["text"]
    assert "Ссылка сейчас не открывается" in item["text"]
    assert not buttons(item)


def test_child_menu_replaces_reminder_section_only_for_child():
    bot = engine()
    menu = bot.nodes[bot.config["menu"]]
    child = bot.session(1)
    child.values["role"] = "child"
    shown = [item["text"] for item in bot.node_buttons(menu, child)]
    assert "Где могут помочь" in shown
    assert "Напоминания" not in shown
    parent = bot.session(2)
    parent.values["role"] = "parent"
    adult = [item["text"] for item in bot.node_buttons(menu, parent)]
    assert "Где могут помочь" not in adult
    assert "Напоминания" in adult


def test_child_has_no_new_reminder_controls_but_can_open_existing_list():
    async def run():
        bot = engine()
        child = bot.session(1)
        child.values["role"] = "child"
        assert not any("Напомнить" in item["text"] for item in bot.node_buttons(
            bot.nodes["reminder_new"], child))
        listing = await bot.handle(1, text="/reminders")
        assert not any("Новое напоминание" in item["text"] for item in buttons(listing[0]))
        assert not any("Напомнить" in item["text"] for item in RentalBranch(bot).controls(child))
        assert bot.new_reminder(child)[0]["text"] == bot.text("stale")
    asyncio.run(run())
