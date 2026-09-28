from fastapi import FastAPI
from fastapi.testclient import TestClient
from project.miniapp.help_api import router

app = FastAPI()
app.include_router(router)
client = TestClient(app)


def test_ambiguity_does_not_select_random_region():
    response = client.get('/api/help/summary', params={'place': 'Тула'})
    assert response.status_code == 409
    assert len(response.json()['detail']['choices']) > 1


def test_regional_fallback_and_pagination():
    params = {'place': 'Кингисепп', 'region': 'Ленинградская область'}
    response = client.get('/api/help/summary', params=params)
    assert response.status_code == 200
    assert response.json()['region_total'] >= 1
    response = client.get('/api/help/points', params={**params, 'category': 'clothes', 'limit': 1})
    data = response.json()
    assert data['scope'] == 'region'
    assert data['items'][0]['region'] == 'Ленинградская область'
    assert client.get('/api/help/points/' + data['items'][0]['id']).status_code == 200
    assert client.get('/api/help/points', params={**params, 'limit': 101}).status_code == 422


def test_empty_category_does_not_return_other_services():
    data = client.get('/api/help/points', params={'place': 'Горно-Алтайск', 'category': 'shelter'}).json()
    assert data['items'] == []
