"""Add the prepared physical-help lookup to the child draft; never publish."""

from copy import deepcopy
import json

from project.admin.documents_reference_draft import ROOT, request
from project.llm.services.scenario import validate


CATEGORIES = [
    ("🍲 Бесплатная еда", "food"),
    ("🛏 Ночлег и временное жильё", "shelter"),
    ("👕 Одежда и обувь", "clothes"),
    ("🚿 Душ, стирка и гигиена", "hygiene"),
]


def configure(config):
    result = deepcopy(config)
    nodes = {node["id"]: node for node in result["nodes"]}
    main = nodes[result["menu"]]
    buttons = []
    for button in main["buttons"]:
        if button.get("target") == "help_points":
            continue
        if button.get("target") == "reminders":
            buttons.append(dict(label="Где могут помочь", target="help_points",
                                conditions=[dict(field="role", op="eq", value="child")]))
            button = {**button, "conditions": [dict(field="role", op="ne", value="child")]}
        buttons.append(button)
    main["buttons"] = buttons
    result["branches"] = [branch for branch in result["branches"] if branch["id"] != "help_points"] + [
        dict(id="help_points", title="Где могут помочь", entry="help_points", roles=["child"],
             status="draft", prompt="", keywords=["еда", "ночлег", "одежда", "душ"])
    ]
    nodes["help_points"] = dict(
        id="help_points", title="Где могут помочь", branch="help_points", kind="message",
        text="Где могут помочь\n\nВыбери, какая помощь нужна. Потом напиши город: покажу места в нём, а если подходящих точек там нет — варианты в его регионе.",
        buttons=[dict(label=title, target="hp_city", values={"help_category": category})
                 for title, category in CATEGORIES] + [dict(label="На главную", target=result["menu"])],
        routes=[], conditions=[], next="", tasks=[], sources=[],
    )
    nodes["hp_city"] = dict(
        id="hp_city", title="Напиши город", branch="help_points", kind="message",
        text="Напиши город или другой населённый пункт, где ты сейчас.",
        buttons=[dict(label="К видам помощи", target="help_points"),
                 dict(label="На главную", target=result["menu"])],
        routes=[], conditions=[], next="", tasks=[], sources=[],
    )
    result["nodes"] = list(nodes.values())
    result["help_points"] = {"enabled": True, "catalog_date": "2026-09-25"}
    return validate(result)


def main():
    before = request("state")
    config = configure(before["config"])
    assert configure(config) == config
    output = ROOT / "docs/test-results/help-points"
    output.mkdir(parents=True, exist_ok=True)
    (output / "draft-before.json").write_text(json.dumps(before["config"], ensure_ascii=False, indent=2), encoding="utf-8")
    saved = request("draft", dict(config=config, revision=before["revision"]), "PUT")
    after = request("state")
    assert after["revision"] == saved["revision"]
    assert after["published_id"] == before["published_id"]
    assert after["config"] == config
    result = dict(revision=after["revision"], published_id=after["published_id"],
                  catalog="project/llm/data/help_points.json", saved_matches=True)
    (output / "draft-update.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    main()
