import asyncio
import copy

import httpx
import pytest

from project.llm.integrations import trudvsem


def test_fresh_exact_cache_avoids_duplicate_request():
    calls=[];now=[100.0]
    def handler(request):
        calls.append(request)
        return httpx.Response(200,json=envelope())
    async def run():
        client=trudvsem.TrudvsemClient(transport=httpx.MockTransport(handler),clock=lambda:now[0])
        client.fresh_ttl=60
        try:
            await client.search('грузчик')
            now[0]+=1
            assert (await client.search('грузчик')).stale_age_seconds is None
            assert len(calls)==1
            await client.search('повар')
            assert len(calls)==2
            now[0]+=61
            await client.search('грузчик')
            assert len(calls)==3
        finally: await client.close()
    asyncio.run(run())


@pytest.fixture(autouse=True)
def close_managed_client():
    yield
    asyncio.run(trudvsem.close())


def vacancy(**updates):
    raw = {
        "id": "job-1", "job-name": "Грузчик", "vac_url": "https://trudvsem.ru/vacancy/card/co/job-1",
        "company": {"name": "Склад", "companycode": "co"},
        "region": {"name": "Тульская область"},
        "salary_min": "40000", "salary_max": 50000,
        "requirements": "<p>Аккуратность &amp; внимание</p>",
        "duty": "<p>Погрузка</p><p>Разгрузка</p>",
        "requirement": {"experience": 0},
        "addresses": {"address": [{"location": "Тульская область, г Тула, ул. Ленина, 1"}]},
        "contact_list": [{"contact_type": "Телефон", "contact_value": "+7 000 000-00-00"}],
        "contact_person": "Отдел кадров",
    }
    raw.update(updates)
    return raw


def envelope(rows=None, total=1):
    return {"status": "200", "meta": {"total": total},
            "results": {"vacancies": [{"vacancy": raw} for raw in (rows if rows is not None else [vacancy()])]}}


def transport(monkeypatch, handler):
    original = httpx.AsyncClient
    clients = []

    def factory(**kwargs):
        client = original(**kwargs, transport=httpx.MockTransport(handler))
        clients.append(client)
        return client

    monkeypatch.setattr(trudvsem.httpx, "AsyncClient", factory)
    return clients


def test_combined_search_encodes_filters_and_normalizes(monkeypatch):
    def handler(request):
        assert request.url.path == "/api/v1/vacancies/region/7100000000000"
        assert dict(request.url.params) == {
            "text": "грузчик & склад", "experienceFrom": "0", "experienceTo": "2",
            "accommodation": "true", "limit": "5", "offset": "1",
        }
        assert "%D0" in str(request.url) and "%26" in str(request.url)
        assert "authorization" not in request.headers
        assert request.extensions["timeout"]["read"] == 7
        return httpx.Response(200, json=envelope(total="12"))

    clients = transport(monkeypatch, handler)
    page = asyncio.run(trudvsem.search_vacancies(
        "грузчик & склад", region_code="7100000000000", experience_from=0,
        experience_to=2, accommodation=True, offset=1, timeout_seconds=7,
    ))
    job = page.items[0]
    assert (job.id, job.source, job.title, job.company) == ("job-1", "trudvsem", "Грузчик", "Склад")
    assert (job.salary_from, job.salary_to, job.experience) == (40000, 50000, "0")
    assert job.requirements == "Аккуратность & внимание"
    assert job.responsibilities == "Погрузка Разгрузка"
    assert job.region == "Тульская область" and job.city is None
    assert job.address.startswith("Тульская область")
    assert job.accommodation is None  # Filter alone cannot fill missing source data.
    assert job.contacts[0].value == "+7 000 000-00-00"
    assert job.contact_person == "Отдел кадров"
    assert page.total == 12 and page.next_offset == 2
    assert not clients[0].is_closed


@pytest.mark.parametrize("kwargs,path,params", [
    ({"text": "грузчик"}, "/api/v1/vacancies", {"text": "грузчик"}),
    ({"region_code": "7700000000000"}, "/api/v1/vacancies/region/7700000000000", {}),
    ({"accommodation": False}, "/api/v1/vacancies", {"accommodation": "false"}),
])
def test_independent_search_modes(monkeypatch, kwargs, path, params):
    def handler(request):
        assert request.url.path == path
        assert dict(request.url.params) == {"limit": "5", "offset": "0", **params}
        return httpx.Response(200, json=envelope())

    transport(monkeypatch, handler)
    page = asyncio.run(trudvsem.search_vacancies(**kwargs))
    assert page.next_offset is None
    assert page.items[0].accommodation is None


@pytest.mark.parametrize("results,total,offset", [({}, 0, 0), ({"vacancies": []}, 0, 0), ({}, 3, 1)])
def test_empty_and_exhausted_pages(monkeypatch, results, total, offset):
    transport(monkeypatch, lambda r: httpx.Response(200, json={
        "status": "200", "meta": {"total": total}, "results": results,
    }))
    page = asyncio.run(trudvsem.search_vacancies("нет", offset=offset))
    assert page.items == () and page.next_offset is None


def test_sparse_vacancy_and_url_fallback(monkeypatch):
    raw = {"id": "job/1", "job-name": "Рабочий", "company": {"companycode": "co/1"}}
    transport(monkeypatch, lambda r: httpx.Response(200, json=envelope([raw])))
    job = asyncio.run(trudvsem.search_vacancies()).items[0]
    assert job.salary_from is None and job.salary_to is None
    assert job.city is None and job.address is None and job.experience is None
    assert job.contacts == () and job.requirements is None
    assert job.url == "https://trudvsem.ru/vacancy/card/co%2F1/job%2F1"


def test_null_optional_fields(monkeypatch):
    raw = vacancy(addresses={"address": None}, contact_list=None, requirement=None,
                  salary_min=None, salary_max=None, requirements=None, duty=None)
    transport(monkeypatch, lambda r: httpx.Response(200, json=envelope([raw])))
    job = asyncio.run(trudvsem.search_vacancies()).items[0]
    assert job.address is None and job.contacts == () and job.experience is None
    assert job.requirements is None and job.responsibilities is None


@pytest.mark.parametrize("housing,expected", [(True, True), (False, False), ("false", False), ("true", True)])
def test_explicit_housing_overrides_search_context(monkeypatch, housing, expected):
    transport(monkeypatch, lambda r: httpx.Response(200, json=envelope([vacancy(accommodation=housing)])))
    assert asyncio.run(trudvsem.search_vacancies(accommodation=True)).items[0].accommodation is expected


@pytest.mark.parametrize("salary", [None, "", "договорная", True, -1, "NaN", "Infinity"])
def test_unknown_salary_is_not_invented(monkeypatch, salary):
    transport(monkeypatch, lambda r: httpx.Response(200, json=envelope([vacancy(salary_min=salary)])))
    assert asyncio.run(trudvsem.search_vacancies()).items[0].salary_from is None


@pytest.mark.parametrize("kwargs", [
    {"limit": 0}, {"limit": 101}, {"limit": True}, {"offset": -1}, {"offset": 1.5},
    {"experience_from": -1}, {"experience_to": True}, {"experience_from": 3, "experience_to": 1},
    {"region_code": "77/../../"}, {"region_code": ""}, {"region_code": "Москва"},
    {"accommodation": "true"}, {"text": 1}, {"timeout_seconds": 0}, {"timeout_seconds": float("nan")},
])
def test_invalid_parameters_do_not_call_api(monkeypatch, kwargs):
    transport(monkeypatch, lambda r: pytest.fail("Must validate before request"))
    with pytest.raises(ValueError):
        asyncio.run(trudvsem.search_vacancies(**kwargs))


@pytest.mark.parametrize("status,code", [(400, "api_error"), (401, "api_error")])
def test_permanent_http_errors_safe_and_no_retries(monkeypatch, status, code):
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(status, text="private-upstream-error")

    clients = transport(monkeypatch, handler)
    with pytest.raises(trudvsem.TrudvsemError) as caught:
        asyncio.run(trudvsem.search_vacancies())
    assert caught.value.code == code and caught.value.status_code == status
    assert "private-upstream-error" not in str(caught.value)
    assert caught.value.__suppress_context__ and len(calls) == 1
    assert not clients[0].is_closed


@pytest.mark.parametrize('status',[429,500,502,503,504])
def test_read_retries_transient_http_failure(monkeypatch,status):
    calls=0
    def handler(request):
        nonlocal calls
        calls+=1
        return httpx.Response(status,text='private') if calls==1 else httpx.Response(200,json=envelope())
    transport(monkeypatch,handler)
    page=asyncio.run(trudvsem.search_vacancies('грузчик'))
    assert page.items and page.attempt_count==2 and calls==2


@pytest.mark.parametrize("error,code", [(httpx.ReadTimeout, "timeout"), (httpx.ConnectError, "connection")])
def test_transport_failures(monkeypatch, error, code):
    def handler(request):
        raise error("private", request=request)

    clients = transport(monkeypatch, handler)
    with pytest.raises(trudvsem.TrudvsemError) as caught:
        asyncio.run(trudvsem.search_vacancies())
    assert caught.value.code == code and not clients[0].is_closed


def test_total_deadline(monkeypatch):
    async def handler(request):
        await asyncio.sleep(1)
        return httpx.Response(200, json=envelope())

    clients = transport(monkeypatch, handler)
    with pytest.raises(trudvsem.TrudvsemError, match="timeout"):
        asyncio.run(trudvsem.search_vacancies(timeout_seconds=0.01))
    assert not clients[0].is_closed


def test_provider_error_inside_http_200(monkeypatch):
    transport(monkeypatch, lambda r: httpx.Response(200, json={"status": "500", "meta": {"error": "private"}}))
    with pytest.raises(trudvsem.TrudvsemError) as caught:
        asyncio.run(trudvsem.search_vacancies())
    assert caught.value.code == "api_error" and caught.value.status_code == 500


@pytest.mark.parametrize("payload", [
    [], {}, {"status": "200"}, {"status": "200", "meta": {"total": 1}, "results": {}},
    envelope([{}]), envelope([vacancy(vac_url="javascript:alert(1)")]),
    envelope(total=True), envelope(total=1.5), envelope(total=-1),
    {"status": "200", "meta": {"total": 0}, "results": []},
])
def test_malformed_responses_not_reported_as_no_results(monkeypatch, payload):
    transport(monkeypatch, lambda r: httpx.Response(200, json=copy.deepcopy(payload)))
    with pytest.raises(trudvsem.TrudvsemError, match="invalid_response"):
        asyncio.run(trudvsem.search_vacancies())


def test_non_json_response(monkeypatch):
    transport(monkeypatch, lambda r: httpx.Response(200, text="<html>maintenance</html>"))
    with pytest.raises(trudvsem.TrudvsemError, match="invalid_response"):
        asyncio.run(trudvsem.search_vacancies())


def test_managed_trudvsem_reuses_client_and_survives_error():
    calls=0
    def handler(request):
        nonlocal calls
        calls+=1
        if calls in {2,3}:return httpx.Response(503,text='private')
        return httpx.Response(200,json=envelope())
    async def run():
        service=trudvsem.TrudvsemClient(transport=httpx.MockTransport(handler))
        assert (await service.search()).items
        cached=await service.search()
        assert cached.stale_age_seconds==0 and calls==3 and not service.is_closed
        assert (await service.search(offset=1)).items and calls==4 and service.client_creations==1
        await service.close();await service.close()
        assert service.close_count==1
    asyncio.run(run())


def test_exact_persistent_cache_is_used_after_retryable_outage(tmp_path):
    cache_path=tmp_path/'trudvsem.json';now=[100.0]
    async def run():
        first=trudvsem.TrudvsemClient(transport=httpx.MockTransport(
            lambda request:httpx.Response(200,json=envelope())),cache_path=cache_path,clock=lambda:now[0])
        live=await first.search('грузчик',region_code='7100000000000')
        assert live.stale_age_seconds is None
        await first.close();now[0]=160.0;calls=0
        def failed(request):
            nonlocal calls
            calls+=1
            raise httpx.ConnectError('private',request=request)
        second=trudvsem.TrudvsemClient(transport=httpx.MockTransport(failed),cache_path=cache_path,clock=lambda:now[0])
        cached=await second.search('грузчик',region_code='7100000000000')
        assert calls==2 and cached.items==live.items and cached.stale_age_seconds==60
        assert cached.attempt_count==2
        await second.close()
    asyncio.run(run())
