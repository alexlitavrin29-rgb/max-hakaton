"""The reviewed import must stay reproducible and reachable through public lookup."""
import json
from project.admin import build_help_points as builder
from project.miniapp.help_api import identified, points
from project.llm.services.help_points import catalog


def test_review_rebuild_and_unique_api_ids(tmp_path, monkeypatch):
    expected = catalog()
    monkeypatch.setattr(builder, 'OUTPUT', tmp_path / 'points.json')
    builder.main()
    actual = json.loads(builder.OUTPUT.read_text(encoding='utf-8'))
    assert actual == expected
    ids = [identified(p)['id'] for p in actual]
    assert len(ids) == len(set(ids))
    assert not any('Добродомик' in p['name'] or 'Очаг Добра' in p['name'] for p in actual)
    assert not any('Тирэх' in p['branch'] for p in actual)


def test_new_services_are_found_by_city_and_keep_conditions():
    kazan = points('Казань', 'Татарстан', 'shelter', 0, 100, 'city')
    central = next(p for p in kazan['items'] if 'Приют человека' in p['name'])
    assert 'совершеннолетние' in central['target_group']
    assert 'Для питания' in central['documents_required']
    ufa = points('Уфа', 'Башкортостан', 'food', 0, 100, 'city')
    assert len([p for p in ufa['items'] if 'Уфа Добрая' in p['name']]) == 2
    moscow = points('Москва', 'Москва', 'hygiene', 0, 100, 'city')
    assert any(p['flags']['shower'] == 'RESTRICTIONS' and 'Ночлежка' in p['name'] for p in moscow['items'])
    kostroma = points('Кострома', 'Костромская область', 'clothes', 0, 100, 'city')
    center = next(p for p in kostroma['items'] if 'Второе дыхание' in p['name'])
    assert center['address'] == 'Шагова, 3'
    assert 'ходатайство' in center['documents_required']
