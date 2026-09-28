import asyncio
import json
import logging

import httpx
import pytest
from openai import AsyncOpenAI

from project.llm.config import LLMConfigurationError, LLMSettings
from project.llm.services import llm

SECRET = "test-secret-never-log"
MESSAGES = [{"role": "user", "content": "Test message"}]


def sdk_transport(monkeypatch, handler):
    clients = []

    def factory(**kwargs):
        client = AsyncOpenAI(
            **kwargs, http_client=httpx.AsyncClient(transport=httpx.MockTransport(handler))
        )
        clients.append(client)
        return client

    monkeypatch.setattr(llm, "AsyncOpenAI", factory)
    return clients


@pytest.fixture(autouse=True)
def close_managed_llm():
    yield
    asyncio.run(llm.close())


def completion(content="OK"):
    return {
        "id": "test", "object": "chat.completion", "created": 0, "model": "other-model",
        "choices": [{"index": 0, "message": {"role": "assistant", "content": content},
                     "finish_reason": "stop"}],
    }


def test_success_uses_config_and_closes_client(monkeypatch):
    def handler(request):
        assert str(request.url) == "https://provider.example/v1/chat/completions"
        assert request.headers["authorization"] == f"Bearer {SECRET}"
        assert json.loads(request.content) == {"model": "another/model", "messages": MESSAGES}
        assert request.extensions["timeout"]["read"] == 7
        return httpx.Response(200, json=completion())

    clients = sdk_transport(monkeypatch, handler)
    settings = LLMSettings(SECRET, "https://provider.example/v1", "another/model", 7)
    assert asyncio.run(llm.call_llm(MESSAGES, settings=settings)) == "OK"
    assert not clients[0].is_closed()


def test_extraction_uses_stable_sampling_and_records_actual_usage(monkeypatch):
    def handler(request):
        body=json.loads(request.content)
        assert body['temperature']==0 and body['reasoning_effort']=='medium'
        reply=completion('{}');reply['model']='openai/gpt-oss-20b'
        reply['usage']=dict(prompt_tokens=10,completion_tokens=5,total_tokens=15)
        return httpx.Response(200,json=reply)
    sdk_transport(monkeypatch,handler);metrics={}
    asyncio.run(llm.call_llm(MESSAGES,settings=LLMSettings(SECRET,model='openai/gpt-oss-20b'),extraction=True,metrics=metrics))
    assert metrics['model']=='openai/gpt-oss-20b' and metrics['usage']['total_tokens']==15


@pytest.mark.parametrize('base_url,mode',[('https://api.groq.com/openai/v1','json_schema'),('https://provider.example/v1','json_object')])
def test_work_schema_is_strict_only_on_supported_provider(monkeypatch,base_url,mode):
    from project.llm.services.work_schema import SCHEMA
    def handler(request):
        fmt=json.loads(request.content)['response_format']
        assert fmt['type']==mode
        if mode=='json_schema':
            assert fmt['json_schema']['strict'] is True
            assert fmt['json_schema']['schema']['properties']['work_fields']['additionalProperties'] is False
        return httpx.Response(200,json=completion('{}'))
    sdk_transport(monkeypatch,handler)
    asyncio.run(llm.call_llm(MESSAGES,settings=LLMSettings(SECRET,base_url=base_url,model='openai/gpt-oss-20b'),extraction=True,json_mode=True,response_schema=SCHEMA))


def test_synthetic_diagnostics_distinguish_invalid_json_and_quota_without_account_data(monkeypatch):
    def invalid(request):
        return httpx.Response(400,json={'error':dict(code='json_validate_failed',failed_generation=SECRET)})
    sdk_transport(monkeypatch,invalid);metrics={}
    with pytest.raises(llm.LLMError,match='invalid_response'):
        asyncio.run(llm.call_llm(MESSAGES,settings=LLMSettings(SECRET),metrics=metrics))
    assert SECRET not in json.dumps(metrics)
    def quota(request):
        return httpx.Response(429,json={'error':dict(message=f'organization {SECRET}: tokens per day (TPD): Limit 200000, Used 199000, Requested 2500')})
    sdk_transport(monkeypatch,quota);metrics={}
    with pytest.raises(llm.LLMError,match='rate_limit'):
        asyncio.run(llm.call_llm(MESSAGES,settings=LLMSettings(SECRET),metrics=metrics))
    assert metrics['provider_limit']['period']=='day' and SECRET not in json.dumps(metrics)


@pytest.mark.parametrize("status,code", [
    (401, "authentication"), (403, "permission"), (402, "balance"),
    (429, "rate_limit"), (400, "api_error"),
])
def test_api_errors_are_safe_and_not_retried(monkeypatch, caplog, status, code):
    requests = []

    def handler(request):
        requests.append(request)
        return httpx.Response(status, json={"error": {"message": SECRET}})

    clients = sdk_transport(monkeypatch, handler)
    with caplog.at_level(logging.WARNING), pytest.raises(llm.LLMError) as caught:
        asyncio.run(llm.call_llm(MESSAGES, settings=LLMSettings(SECRET)))
    assert caught.value.code == code
    assert caught.value.status_code == status
    assert SECRET not in str(caught.value) + caplog.text
    assert caught.value.__suppress_context__
    assert len(requests) == 1
    assert not clients[0].is_closed()


@pytest.mark.parametrize('first',[500,502,503,504])
def test_transient_provider_error_is_retried_once(monkeypatch,first):
    calls=0
    def handler(request):
        nonlocal calls
        calls+=1
        return httpx.Response(first,json={'error':{'message':'private'}}) if calls==1 else httpx.Response(200,json=completion('recovered'))
    sdk_transport(monkeypatch,handler);metrics={}
    assert asyncio.run(llm.call_llm(MESSAGES,settings=LLMSettings(SECRET),metrics=metrics))=='recovered'
    assert calls==2 and metrics['attempt_count']==2 and metrics['retry_count']==1


def test_connection_failure_is_retried_once(monkeypatch):
    calls=0
    def handler(request):
        nonlocal calls
        calls+=1
        if calls==1:raise httpx.ConnectError('private',request=request)
        return httpx.Response(200,json=completion('recovered'))
    sdk_transport(monkeypatch,handler)
    assert asyncio.run(llm.call_llm(MESSAGES,settings=LLMSettings(SECRET)))=='recovered'
    assert calls==2


@pytest.mark.parametrize("error_type,code", [(httpx.ReadTimeout, "timeout"),
                                            (httpx.ConnectError, "connection")])
def test_network_error(monkeypatch, caplog, error_type, code):
    def handler(request):
        raise error_type(SECRET, request=request)

    sdk_transport(monkeypatch, handler)
    with pytest.raises(llm.LLMError) as caught:
        asyncio.run(llm.call_llm(MESSAGES, settings=LLMSettings(SECRET)))
    assert caught.value.code == code
    assert SECRET not in str(caught.value) + caplog.text


def test_total_deadline(monkeypatch):
    async def handler(request):
        await asyncio.sleep(1)
        return httpx.Response(200, json=completion())

    clients = sdk_transport(monkeypatch, handler)
    with pytest.raises(llm.LLMError, match="timeout"):
        asyncio.run(llm.call_llm(MESSAGES, settings=LLMSettings(SECRET, timeout_seconds=0.01)))
    assert not clients[0].is_closed()


@pytest.mark.parametrize("content", [None, ""])
def test_empty_response(monkeypatch, content):
    sdk_transport(monkeypatch, lambda request: httpx.Response(200, json=completion(content)))
    with pytest.raises(llm.LLMError, match="empty_response"):
        asyncio.run(llm.call_llm(MESSAGES, settings=LLMSettings(SECRET)))


def test_missing_key_never_calls_sdk(monkeypatch, tmp_path, caplog):
    monkeypatch.delenv("POLZA_API_KEY", raising=False)
    monkeypatch.setenv("LLM_API_KEY", "legacy-must-not-be-used")
    load_settings = LLMSettings.from_env
    monkeypatch.setattr(llm.LLMSettings, "from_env", lambda: load_settings(tmp_path / ".env"))

    def forbidden(**kwargs):
        pytest.fail("SDK must not be constructed without a key")

    monkeypatch.setattr(llm, "AsyncOpenAI", forbidden)
    with pytest.raises(LLMConfigurationError, match="POLZA_API_KEY"):
        asyncio.run(llm.call_llm(MESSAGES))
    assert "legacy-must-not-be-used" not in caplog.text


def test_env_file_and_environment_precedence(monkeypatch, tmp_path):
    for name in ("POLZA_API_KEY", "LLM_BASE_URL", "LLM_MODEL", "LLM_TIMEOUT_SECONDS"):
        monkeypatch.delenv(name, raising=False)
    env_file = tmp_path / ".env"
    env_file.write_text(f'POLZA_API_KEY={SECRET}\nLLM_MODEL=file-model\n', encoding="utf-8")
    settings = LLMSettings.from_env(env_file)
    assert settings.api_key == SECRET
    assert SECRET not in repr(settings)
    assert settings.model == "file-model"
    monkeypatch.setenv("LLM_MODEL", "env-model")
    assert LLMSettings.from_env(env_file).model == "env-model"
    monkeypatch.setenv("POLZA_API_KEY", "")
    with pytest.raises(LLMConfigurationError):
        LLMSettings.from_env(env_file)


def test_environment_cannot_redirect_llm_secret_to_an_unapproved_host(monkeypatch, tmp_path):
    monkeypatch.setenv('POLZA_API_KEY', SECRET)
    monkeypatch.setenv('LLM_BASE_URL', 'https://attacker.invalid/v1')
    with pytest.raises(LLMConfigurationError, match='host'):
        LLMSettings.from_env(tmp_path / '.env')


@pytest.mark.parametrize("timeout", [0, -1, float("inf"), float("nan")])
def test_invalid_timeout(timeout):
    with pytest.raises(LLMConfigurationError):
        LLMSettings(SECRET, timeout_seconds=timeout)


@pytest.mark.parametrize("url", ["not-a-url", "https://[broken", "https://user:password@host/v1"])
def test_invalid_url_has_safe_error(url):
    with pytest.raises(LLMConfigurationError) as caught:
        LLMSettings(SECRET, base_url=url)
    assert url not in str(caught.value)


def test_proxyapi_selects_own_key_and_endpoint(monkeypatch, tmp_path):
    for name in ['LLM_PROVIDER', 'LLM_BASE_URL', 'LLM_MODEL', 'PROXYAPI_API_KEY', 'POLZA_API_KEY']:
        monkeypatch.delenv(name, raising=False)
    path = tmp_path / '.env'
    path.write_text('LLM_PROVIDER=proxyapi\nPROXYAPI_API_KEY=proxy-test\nPOLZA_API_KEY=polza-test\n')
    settings = LLMSettings.from_env(path)
    assert settings.api_key == 'proxy-test'
    assert settings.base_url == 'https://api.proxyapi.ru/v1'
    assert settings.model == 'deepseek/deepseek-v4-flash'
    path.write_text('LLM_PROVIDER=proxyapi\nPOLZA_API_KEY=polza-test\n')
    with pytest.raises(LLMConfigurationError, match='PROXYAPI_API_KEY'):
        LLMSettings.from_env(path)


@pytest.mark.parametrize('json_mode', [False, True])
def test_proxyapi_plain_and_json_responses_hide_request_data(monkeypatch, caplog, json_mode):
    private_text = 'private-user-text'
    messages = [{'role': 'user', 'content': private_text}]
    def handler(request):
        assert str(request.url) == 'https://api.proxyapi.ru/v1/chat/completions'
        assert request.headers['authorization'] == f'Bearer {SECRET}'
        body = json.loads(request.content)
        assert body['messages'] == messages
        assert ('response_format' in body) == json_mode
        return httpx.Response(200, json=completion('{"ok":true}' if json_mode else 'Готово'))
    sdk_transport(monkeypatch, handler)
    with caplog.at_level(logging.WARNING):
        result = asyncio.run(llm.call_llm(messages, settings=LLMSettings(
            SECRET, 'https://api.proxyapi.ru/v1', 'deepseek/deepseek-v4-flash'), json_mode=json_mode))
    assert result == ('{"ok":true}' if json_mode else 'Готово')
    assert SECRET not in caplog.text and private_text not in caplog.text


def test_managed_llm_reuses_client_and_closes_idempotently():
    requests=[]
    def handler(request):
        requests.append(request)
        return httpx.Response(200,json=completion())
    async def run():
        service=llm.LLMService(LLMSettings(SECRET,'https://provider.example/v1','model',7),
                               transport=httpx.MockTransport(handler))
        await service.complete(MESSAGES)
        await service.complete(MESSAGES)
        assert service.client_creations==1 and len(requests)==2 and not service.is_closed
        await service.close();await service.close()
        assert service.close_count==1 and service.is_closed
        with pytest.raises(llm.LLMError,match='closed'):
            await service.complete(MESSAGES)
    asyncio.run(run())


def test_managed_llm_transient_error_retries_without_closing():
    calls=0
    def handler(request):
        nonlocal calls
        calls+=1
        if calls==1:return httpx.Response(503,json={'error':{'message':'private'}})
        return httpx.Response(200,json=completion('next'))
    async def run():
        service=llm.LLMService(LLMSettings(SECRET,'https://provider.example/v1','model',7),
                               transport=httpx.MockTransport(handler))
        assert await service.complete(MESSAGES)=='next'
        assert calls==2 and not service.is_closed
        await service.close()
    asyncio.run(run())


def test_default_managed_llm_loads_settings_only_once(monkeypatch):
    loads = 0

    def settings_from_env():
        nonlocal loads
        loads += 1
        return LLMSettings(SECRET, "https://provider.example/v1", "model", 7)

    sdk_transport(monkeypatch, lambda request: httpx.Response(200, json=completion()))
    monkeypatch.setattr(llm.LLMSettings, "from_env", settings_from_env)

    async def run():
        await llm.call_llm(MESSAGES)
        await llm.call_llm(MESSAGES)

    asyncio.run(run())
    assert loads == 1


def test_groq_selects_own_key_and_model(monkeypatch, tmp_path):
    for name in ["LLM_PROVIDER", "LLM_BASE_URL", "LLM_MODEL", "GROQ_API_KEY", "POLZA_API_KEY"]:
        monkeypatch.delenv(name, raising=False)
    path = tmp_path / ".env"
    path.write_text("LLM_PROVIDER=groq\nGROQ_API_KEY=groq-test\nPOLZA_API_KEY=polza-test\n")
    settings = LLMSettings.from_env(path)
    assert settings.api_key == "groq-test"
    assert settings.model == "openai/gpt-oss-120b"
    assert settings.base_url == "https://api.groq.com/openai/v1"
    path.write_text("LLM_PROVIDER=groq\nPOLZA_API_KEY=polza-test\n")
    with pytest.raises(LLMConfigurationError, match="GROQ_API_KEY"):
        LLMSettings.from_env(path)
