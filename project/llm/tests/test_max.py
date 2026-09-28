import asyncio

import httpx
import pytest

from project.llm.channels.max import MaxClient, MaxError, message, callback
from project.llm.bot import event_input


def test_max_authorization_in_header_only():
    seen = []
    def handler(request):
        seen.append(request)
        assert request.headers["Authorization"] == "test-token"
        assert "test-token" not in str(request.url)
        return httpx.Response(200, json={"message": {}})
    async def scenario():
        api = MaxClient("test-token")
        await api.client.aclose()
        api.client = httpx.AsyncClient(base_url="https://platform-api2.max.ru", headers={"Authorization": "test-token"}, transport=httpx.MockTransport(handler))
        await api.send(5, message("hello", [callback("Кнопка", "menu")]))
        await api.close()
    asyncio.run(scenario())
    assert seen[0].url.params["user_id"] == "5"


def test_wrong_host_cannot_receive_token():
    with pytest.raises(ValueError): MaxClient("test-token", "https://attacker.invalid")


def test_private_event_sender_and_callback():
    event = {"update_type": "message_created", "message": {"sender": {"user_id": 15}, "recipient": {"chat_type": "dialog"}, "body": {"text": "hello"}}}
    assert event_input(event) == (15, "hello", None, None)
    event["message"]["recipient"]["chat_type"] = "chat"
    assert event_input(event)[0] is None
    event["message"]["recipient"]["chat_type"] = "dialog"
    event["update_type"] = "message_callback"
    event["callback"] = {"user": {"user_id": 16}, "payload": "menu", "callback_id": "id"}
    assert event_input(event) == (16, "", "menu", "id")


def test_send_records_lock_sleep_http_and_total_without_payload():
    async def handler(request):
        await asyncio.sleep(0)
        return httpx.Response(200,json={'message':{}})
    async def run():
        api=MaxClient('test-token',transport=httpx.MockTransport(handler),min_send_interval=0)
        metrics={}
        await api.send(5,message('private text'),metrics=metrics)
        assert set(metrics)=={'delivery_queue_ms','delivery_sleep_ms','delivery_http_ms','delivery_total_ms'}
        assert all(value>=0 for value in metrics.values())
        assert 'private text' not in repr(metrics) and 'test-token' not in repr(metrics)
        await api.close();await api.close()
        assert api.close_count==1
    asyncio.run(run())


def test_send_error_releases_lock_for_next_request():
    calls=0
    def handler(request):
        nonlocal calls
        calls+=1
        return httpx.Response(503 if calls==1 else 200,json={'message':{}})
    async def run():
        api=MaxClient('test-token',transport=httpx.MockTransport(handler),min_send_interval=0)
        with pytest.raises(MaxError):await api.send(1,message('one'))
        await api.send(1,message('two'))
        assert calls==2
        await api.close()
    asyncio.run(run())


def test_safe_get_retries_one_transient_http_failure():
    calls=0
    def handler(request):
        nonlocal calls
        calls+=1
        return httpx.Response(503 if calls==1 else 200,json={'updates':[]})
    async def run():
        api=MaxClient('test-token',transport=httpx.MockTransport(handler),min_send_interval=0)
        assert await api.updates()=={'updates':[]}
        assert calls==2
        await api.close()
    asyncio.run(run())


def test_send_is_not_retried_when_delivery_is_ambiguous():
    calls=0
    def handler(request):
        nonlocal calls
        calls+=1
        return httpx.Response(503,json={'error':'private'})
    async def run():
        api=MaxClient('test-token',transport=httpx.MockTransport(handler),min_send_interval=0)
        with pytest.raises(MaxError):await api.send(1,message('one'))
        assert calls==1
        await api.close()
    asyncio.run(run())


def test_updates_rejects_malformed_batch():
    async def run():
        api=MaxClient('test-token',transport=httpx.MockTransport(
            lambda request:httpx.Response(200,json={'updates':{}})),min_send_interval=0)
        with pytest.raises(MaxError,match='invalid_response'):await api.updates()
        await api.close()
    asyncio.run(run())
