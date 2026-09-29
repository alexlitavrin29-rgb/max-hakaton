import asyncio
import json
from pathlib import Path
import time
from types import SimpleNamespace

import httpx
import jsonschema

from project.miniapp.app import app
from tools.submission.export_openapi import contract


def test_account_contract_matches_authentication_and_response(monkeypatch):
    document = contract()
    operation = document['paths']['/api/account']['delete']
    assert operation.get('security') == [{'MaxSession': []}]
    assert {'200', '401', '403', '413'} <= operation['responses'].keys()
    schema = operation['responses']['200']['content']['application/json']['schema']
    assert 'deleted' in schema.get('required', [])

    async def delete_account(owner):
        assert owner == 'audit'

    monkeypatch.setattr(app.state, 'preview', True, raising=False)
    monkeypatch.setattr(app.state, 'shared', None, raising=False)
    monkeypatch.setattr(app.state, 'sessions', {
        'test-token': SimpleNamespace(owner='audit', touched=time.monotonic()),
    }, raising=False)
    monkeypatch.setattr(app.state, 'favorites', SimpleNamespace(delete_account=delete_account), raising=False)

    async def run():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://testserver') as client:
            denied = await client.delete('/api/account')
            assert denied.status_code == 401
            response = await client.delete('/api/account', headers={'Authorization': 'Bearer test-token'})
            assert response.status_code == 200
            jsonschema.validate(response.json(), schema)
            assert (await client.delete('/api/account', headers={'Authorization': 'Bearer test-token'})).status_code == 401

    asyncio.run(run())


def test_submitted_openapi_matches_exporter():
    assert json.loads(Path('openapi.yaml').read_text(encoding='utf-8')) == contract()
