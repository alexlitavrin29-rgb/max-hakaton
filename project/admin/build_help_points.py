"""Build the bot's reviewed help-point catalog from the research CSVs."""

import csv
import json
import re
from pathlib import Path

from project.llm.services.geography import places, words


ROOT = Path(__file__).resolve().parents[2]
RESEARCH = ROOT / "docs/research/help-points"
OUTPUT = ROOT / "project/llm/data/help_points.json"
EXCLUSIONS = ROOT / "project/llm/data/help_points_exclusions.json"
REVIEWED = ROOT / "project/llm/data/help_points_reviewed.json"
FIELDS = {
    "food": ("food_hot_meal", "food_package"),
    "shelter": ("shelter_overnight", "shelter_temporary"),
    "clothes": ("clothes", "shoes"),
    "hygiene": ("shower", "laundry", "hygiene_items"),
}
REGION_ALIASES = {
    "Кемеровская область — Кузбасс": "Кемеровская область - Кузбасс Область",
    "Москва": "Москва Город",
    "Санкт-Петербург": "Санкт-Петербург Город",
    "Севастополь": "Севастополь Город",
    "Ханты-Мансийский автономный округ — Югра": "Ханты-Мансийский Автономный округ - Югра Автономный округ",
    "Чувашская Республика": "Чувашская Республика - Чувашия",
}


def read(name):
    with (RESEARCH / name).open(encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def city_key(city):
    city = re.sub(r"\([^)]*\)", "", city).strip()
    city = re.sub(r"^(?:г\.?|город|с\.?|село|д\.?|деревня|п\.?|пос\.?|пос[её]лок|станица|хутор)\s+", "", city, flags=re.I)
    return " ".join(words(city))


def region_codes():
    codes = {}
    for place in places():
        if place["kind"] == "region" or place["code"][:2] in {"77", "78", "92"} and place["source_id"] == place["code"]:
            codes[tuple(sorted(words(place["region"])))] = place["code"]
    return codes


def main():
    rows = read("NEW_ORGANIZATIONS_2026-09-25.csv")
    audit = read("POINT_AUDIT_2026-09-25.csv")
    links = {row["url"]: row["status"] for row in read("LINK_AUDIT_2026-09-25.csv")}
    assert len(rows) == len(audit)
    codes = region_codes()
    exclusions = json.loads(EXCLUSIONS.read_text(encoding="utf-8"))
    output = []
    for row in rows:
        if any(all(row[field] == value for field, value in item["match"].items())
               for item in exclusions):
            continue
        if row["verification_status"].startswith(("PARTIAL_OLD", "SEASONAL_NOT_OPEN")):
            continue
        categories = [name for name, fields in FIELDS.items()
                      if any(row[field] in {"YES", "RESTRICTIONS"} for field in fields)]
        if not categories:
            continue
        region = REGION_ALIASES.get(row["region"], row["region"])
        code = codes.get(tuple(sorted(words(region))))
        if not code:
            raise ValueError(f"Unknown region: {row['region']}")
        urls = [row["source_url"], row["user_action_url"], row["official_website"]]
        urls += [value.strip() for value in row["source_urls"].split(";")]
        url = next((value for value in dict.fromkeys(urls)
                    if value.startswith("https://") and links.get(value) == "OPEN"), "")
        phone = next((number for number in (row["phone"], row["additional_phone"])
                      if number not in {"", "UNVERIFIED"}), "")
        if not url and not phone:
            continue
        address = row["address"]
        if any(flag in row["verification_status"] for flag in ("ADDRESS_CONFLICT", "ADDRESS_APPROXIMATE", "LOCATION_UNCONFIRMED")):
            address = ""
        output.append({
            "region": row["region"], "region_code": code, "city": row["city"],
            "city_key": city_key(row["city"]), "name": row["organization_name"],
            "branch": row["branch_name"], "address": address,
            "phone": phone, "additional_phone": row["additional_phone"],
            "url": url, "categories": categories,
            "flags": {field: row[field] for fields in FIELDS.values() for field in fields},
            "target_group": row["target_group"], "documents_required": row["documents_required"],
            "appointment_required": row["appointment_required"],
            "referral_required": row["referral_required"],
            "working_hours": row["working_hours"], "food_hours": row["food_hours"],
            "shelter_checkin_hours": row["shelter_checkin_hours"],
            "cost": row["cost"], "gender_restrictions": row["gender_restrictions"],
            "sobriety_requirement": row["sobriety_requirement"],
            "verified_at": row["verified_at"], "verification_status": row["verification_status"],
        })
    if REVIEWED.exists():
        review = json.loads(REVIEWED.read_text(encoding="utf-8"))
        for change in review["changes"]:
            matches = [p for p in output if all(p[k] == v for k, v in change["match"].items())]
            for point in matches:
                if change.get("remove"):
                    output.remove(point)
                else:
                    point.update(change["set"])
        output.extend(review["additions"])
    OUTPUT.write_text(json.dumps(output, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    print(json.dumps({"points": len(output), "regions": len({p['region_code'] for p in output}),
                      "categories": {name: sum(name in p["categories"] for p in output) for name in FIELDS}},
                     ensure_ascii=False))


if __name__ == "__main__":
    main()
