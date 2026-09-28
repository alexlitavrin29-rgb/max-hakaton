"""Audience exclusions survive rebuilds without excluding mixed-age services."""
import json

from project.admin import build_help_points as builder


def test_rebuild_omits_reviewed_points_and_preserves_mixed_services(tmp_path, monkeypatch):
    output = tmp_path / "points.json"
    monkeypatch.setattr(builder, "OUTPUT", output)
    builder.main()
    points = json.loads(output.read_text(encoding="utf-8"))
    assert not any("Добродомик" in p["name"] for p in points)
    assert not any("Очаг Добра" in p["name"] for p in points)
    assert not any(p["city"] == "Воронеж" and "Чистое сердце" in p["name"] for p in points)
    # The Sosnovskoye source describes Sergach; it cannot prove a local service.
    assert not any(p["city"] == "Сосновское" for p in points)
    assert any(p["city"] == "Киренск" and "выпускники" in p["target_group"] for p in points)
    assert any(p["city"] == "Вологда" and "Времена года" in p["name"] for p in points)
    assert any(p["city"] == "Томск" and "юные матери" in p["target_group"] for p in points)


def test_exclusion_does_not_ban_other_services_of_the_same_organization(tmp_path, monkeypatch):
    monkeypatch.setattr(builder, "REVIEWED", tmp_path / "no-review.json")
    original_read = builder.read
    row = next(r for r in original_read("NEW_ORGANIZATIONS_2026-09-25.csv")
               if "Добродомик" in r["organization_name"])
    row = dict(row, branch_name="Новая отдельная услуга для молодых", target_group="Все нуждающиеся независимо от возраста")
    monkeypatch.setattr(builder, "read", lambda name: [row] if name == "NEW_ORGANIZATIONS_2026-09-25.csv" else [{}] if name == "POINT_AUDIT_2026-09-25.csv" else original_read(name))
    output = tmp_path / "points.json"
    monkeypatch.setattr(builder, "OUTPUT", output)
    builder.main()
    points = json.loads(output.read_text(encoding="utf-8"))
    assert len(points) == 1
    assert points[0]["target_group"] == row["target_group"]
