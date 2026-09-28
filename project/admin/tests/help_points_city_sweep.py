"""Reproducible 150-city check against the saved help-points draft."""

import asyncio
import csv
import json
import random
from collections import defaultdict
from pathlib import Path

from project.admin.documents_reference_draft import ROOT, request
from project.llm.services.flow import FlowDialogue, PreviewReminders
from project.llm.services.geography import key, places
from project.llm.services.help_points import catalog


SEED = 20260925
CATEGORIES = ("food", "shelter", "clothes", "hygiene")
OUTPUT = ROOT / "docs/test-results/help-points"


def sample():
    rng = random.Random(SEED)
    cities = [place for place in places() if place["kind"] == "city"
              and place["type"] == "Город" and not place["code"].startswith("99")]
    by_region = defaultdict(list)
    for place in cities:
        by_region[place["code"]].append(place)
    selected = [rng.choice(group) for _, group in sorted(by_region.items())]
    selected_ids = {(place["code"], key(place["name"])) for place in selected}
    catalog_ids = {(point["region_code"], point["city_key"]) for point in catalog()}
    extra = [place for place in cities if (place["code"], key(place["name"])) in catalog_ids
             and (place["code"], key(place["name"])) not in selected_ids]
    rng.shuffle(extra)
    for place in extra:
        identity = place["code"], key(place["name"])
        if identity not in selected_ids:
            selected.append(place)
            selected_ids.add(identity)
        if len(selected) == 150:
            break
    assert len(selected) == 150
    rng.shuffle(selected)
    return selected


async def check_case(config, revision, place, category):
    bot = FlowDialogue(PreviewReminders(), config, revision)
    user = 1
    session = bot.session(user)
    session.values.update(role="child", help_category=category)
    await bot.enter(user, session, "hp_city")
    replies = await bot.handle(user, text=place["name"])
    state = session.help_points
    ambiguous = bool(state.get("choices"))
    if ambiguous:
        index = next((i for i, choice in enumerate(state["choices"])
                      if choice["code"] == place["code"] and key(choice["name"]) == key(place["name"])), None)
        if index is None:
            return dict(error="нужного города нет среди вариантов выбора", ambiguous=True)
        replies = await bot.handle(user, payload=f"hp:place:{index}:{state['nonce']}")
    chosen = state.get("place")
    if not chosen or chosen["code"] != place["code"] or key(chosen["name"]) != key(place["name"]):
        return dict(error="выбран другой населённый пункт", ambiguous=ambiguous)
    matching = [point for point in catalog() if point["region_code"] == place["code"]
                and category in point["categories"]]
    local = [point for point in matching if point["city_key"] == key(place["name"])]
    expected = local or matching
    actual = state.get("matches", [])
    if len(actual) != len(expected) or {id(point) for point in actual} != {id(point) for point in expected}:
        return dict(error="список точек не совпадает с каталогом", ambiguous=ambiguous)
    if state.get("scope") != ("city" if local else "region"):
        return dict(error="неверный уровень выдачи", ambiguous=ambiguous)
    shown = []
    while expected and state["offset"] < len(expected):
        shown.extend(replies[1:-1])
        replies = await bot.handle(user, payload=f"hp:more:{state['nonce']}")
    if expected:
        shown.extend(replies[1:-1] if len(replies) > 2 else [])
    if len(shown) != len(expected):
        return dict(error=f"показано {len(shown)} из {len(expected)}", ambiguous=ambiguous)
    for card, point in zip(shown, actual):
        if point["name"] not in card["text"] or point["city"] not in card["text"]:
            return dict(error="в карточке неверное название или город", ambiguous=ambiguous)
        buttons = [button for attachment in card.get("attachments", [])
                   for row in attachment.get("payload", {}).get("buttons", []) for button in row]
        if point["url"] and not any(button.get("url") == point["url"] for button in buttons):
            return dict(error="нет подтверждённой ссылки в карточке", ambiguous=ambiguous)
        if not point["url"] and point["phone"] not in card["text"]:
            return dict(error="нет телефона при отсутствии ссылки", ambiguous=ambiguous)
    return dict(error="", ambiguous=ambiguous, scope=state["scope"], points=len(expected))


async def main():
    current = request("state")
    assert current["config"].get("help_points", {}).get("enabled")
    rows = []
    for place in sample():
        for category in CATEGORIES:
            try:
                result = await check_case(current["config"], current["revision"], place, category)
            except Exception as exc:
                result = dict(error=f"{type(exc).__name__}: {exc}")
            rows.append(dict(region=place["region"], city=place["name"],
                             category=category, **result))
    OUTPUT.mkdir(parents=True, exist_ok=True)
    with (OUTPUT / "city-sweep-150.csv").open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=["region", "city", "category", "scope", "points", "ambiguous", "error"])
        writer.writeheader()
        writer.writerows(rows)
    summary = dict(seed=SEED, revision=current["revision"], cities=150,
                   regions=len({row["region"] for row in rows}), cases=len(rows),
                   errors=[row for row in rows if row.get("error")],
                   ambiguous=sum(bool(row.get("ambiguous")) for row in rows),
                   city_results=sum(row.get("scope") == "city" for row in rows),
                   region_results=sum(row.get("scope") == "region" and row.get("points", 0) > 0 for row in rows),
                   empty_results=sum(row.get("scope") == "region" and row.get("points", 0) == 0 for row in rows))
    (OUTPUT / "city-sweep-150-summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({**summary, "errors": summary["errors"][:15]}, ensure_ascii=False))


if __name__ == "__main__":
    asyncio.run(main())
