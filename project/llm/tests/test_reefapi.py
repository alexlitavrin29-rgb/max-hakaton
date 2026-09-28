import asyncio
import copy
import json
import logging

import httpx
import pytest

from project.llm.integrations import reefapi


def test_request_metrics_include_success_and_failure_without_secrets(caplog):
    caplog.set_level(logging.INFO, logger=reefapi.__name__)
    calls = []
    def handler(request):
        calls.append(True)
        return response([]) if len(calls) == 1 else httpx.Response(503, text='private provider body')
    async def run():
        service = reefapi.ReefClient('private-key', transport=httpx.MockTransport(handler))
        try:
            await service.locations('private city one')
            with pytest.raises(reefapi.ReefError): await service.locations('private city two')
        finally: await service.close()
    asyncio.run(run())
    messages = [r.getMessage() for r in caplog.records if r.name == reefapi.__name__]
    assert len(messages) == 2
    assert 'outcome=ok' in messages[0] and 'outcome=http_503' in messages[1]
    assert all('duration_ms=' in line for line in messages)
    assert 'private' not in '\n'.join(messages)


def test_equal_concurrent_searches_make_one_provider_call():
    calls=[]
    async def handler(request):
        calls.append(json.loads(request.content))
        await asyncio.sleep(.01)
        return httpx.Response(200,json={'ok':True,'data':{'listings':[]}})
    async def run():
        client=reefapi.ReefClient('secret',transport=httpx.MockTransport(handler))
        try:
            results=await asyncio.gather(*(client.search('tomsk',30000,1) for _ in range(5)))
            assert len(calls)==1
            results[0]['listings'].append('changed')
            assert results[1]['listings']==[]
            await client.search('tomsk',40000,1)
            assert len(calls)==2
        finally: await client.close()
    asyncio.run(run())


@pytest.mark.parametrize('status',[401,402,403,429])
def test_invalid_key_or_quota_cools_down_then_can_recover(status):
    calls=[];now=[100.0]
    def handler(request):
        calls.append(request)
        return httpx.Response(status) if len(calls)==1 else response([])
    async def run():
        client=reefapi.ReefClient('secret',transport=httpx.MockTransport(handler),clock=lambda:now[0])
        try:
            for query in ['Томск','Омск']:
                with pytest.raises(reefapi.ReefError): await client.locations(query)
            assert len(calls)==1
            now[0]+=61
            assert await client.locations('Омск')==[]
            assert len(calls)==2
        finally: await client.close()
    asyncio.run(run())


def response(rows):
    return httpx.Response(200,json={'ok':True,'data':{'locations':rows}})


def test_locations_cache_normalizes_case_preserves_region_and_copies_values():
    calls=[]
    def handler(request):
        calls.append(request)
        query=copy.deepcopy(__import__('json').loads(request.content)['query'])
        return response([{'name':query,'slug':'place','location_id':1}])
    async def run():
        service=reefapi.ReefClient('secret',transport=httpx.MockTransport(handler),cache_size=4,cache_ttl=60)
        first=await service.locations(' Томск ');first[0]['name']='mutated'
        second=await service.locations('томск')
        assert len(calls)==1 and second[0]['name']=='Томск'
        await service.locations('Казань, Республика Татарстан')
        await service.locations('Казань')
        assert len(calls)==3
        assert service.metrics['cache_hit']==1 and service.metrics['cache_miss']==3
        await service.close();await service.close()
        assert service.client_creations==1 and service.close_count==1
    asyncio.run(run())


def test_locations_cache_keeps_ambiguity_and_limits_size():
    now=[0.0];calls=[]
    rows=[{'name':'Советск','slug':'a','location_id':1},{'name':'Советск','slug':'b','location_id':2}]
    def handler(request):calls.append(request);return response(rows)
    async def run():
        service=reefapi.ReefClient('secret',transport=httpx.MockTransport(handler),cache_size=2,
                                   cache_ttl=10,clock=lambda:now[0])
        assert len(await service.locations('Советск'))==2
        await service.locations('Омск');await service.locations('Томск')
        assert service.metrics['cache_eviction']==1
        await service.locations('Советск')
        assert len(calls)==4
        now[0]=20;await service.locations('Томск')
        assert len(calls)==5
        await service.close()
    asyncio.run(run())


@pytest.mark.parametrize('failure',[httpx.ReadTimeout,httpx.ConnectError])
def test_locations_error_is_not_cached_and_next_call_succeeds(failure):
    calls=0
    def handler(request):
        nonlocal calls
        calls+=1
        if calls==1:raise failure('private',request=request)
        return response([])
    async def run():
        service=reefapi.ReefClient('secret',transport=httpx.MockTransport(handler))
        with pytest.raises(reefapi.ReefError):await service.locations('Томск')
        assert await service.locations('Томск')==[] and calls==2
        await service.close()
    asyncio.run(run())


def test_concurrent_equal_locations_share_one_request_but_different_keys_do_not():
    calls=[];entered=asyncio.Event();release=asyncio.Event()
    async def handler(request):
        calls.append(__import__('json').loads(request.content)['query'])
        entered.set();await release.wait()
        return response([])
    async def run():
        service=reefapi.ReefClient('secret',transport=httpx.MockTransport(handler))
        same=[asyncio.create_task(service.locations('Томск')) for _ in range(5)]
        await entered.wait();release.set();await asyncio.gather(*same)
        assert calls==['Томск']
        await asyncio.gather(service.locations('Омск'),service.locations('Казань'))
        assert set(calls[1:])=={'Омск','Казань'}
        await service.close()
    asyncio.run(run())


def test_search_cache_survives_client_recreation_and_returns_an_isolated_stale_copy(tmp_path):
    now = [100.0]
    cache_path = tmp_path / 'reefapi-cache.json'
    payload = {
        'listings': [{'ad_id': '1', 'title': 'Квартира'}],
        'has_more': True,
        'filters_applied': {
            'transaction': 'rent_long',
            'property_type': 'apartment',
            'location': 'chita',
            'price_max': 50000,
        },
    }

    def success(request):
        data = ({'locations': [{'name': 'Чита', 'slug': 'chita', 'location_id': 661950}]}
                if request.url.path.endswith('/locations') else payload)
        return httpx.Response(200, json={'ok': True, 'data': data})

    async def run():
        first = reefapi.ReefClient(
            'secret', transport=httpx.MockTransport(success), clock=lambda: now[0],
            cache_path=cache_path, search_fresh_ttl=30, search_stale_ttl=3600,
        )
        assert (await first.locations('Чита'))[0]['slug'] == 'chita'
        live = await first.search('chita', 50000, 1)
        live['listings'][0]['title'] = 'mutated'
        await first.close()

        stored = json.loads(cache_path.read_text(encoding='utf-8'))
        assert stored['version'] == 1 and len(stored['searches']) == 1 and len(stored['locations']) == 1

        now[0] = 200.0
        second = reefapi.ReefClient(
            'secret', transport=httpx.MockTransport(lambda request: (_ for _ in ()).throw(
                httpx.ConnectError('private', request=request))), clock=lambda: now[0],
            cache_path=cache_path, search_fresh_ttl=30, search_stale_ttl=3600,
        )
        cached, age = second.cached_search('chita', 50000, 1)
        assert cached['listings'][0]['title'] == 'Квартира'
        assert age == 100
        cached['listings'][0]['title'] = 'changed again'
        assert second.cached_search('chita', 50000, 1)[0]['listings'][0]['title'] == 'Квартира'
        places, place_age = second.cached_locations('чита')
        assert places[0]['location_id'] == 661950 and place_age == 100
        await second.close()

    asyncio.run(run())


def test_first_start_without_cache_file_is_not_logged_as_a_failure(tmp_path, caplog):
    service = reefapi.ReefClient(
        'secret', transport=httpx.MockTransport(lambda request: response([])),
        cache_path=tmp_path / 'not-created-yet.json',
    )
    assert 'cache load failed' not in caplog.text.casefold()
    asyncio.run(service.close())


def test_reserve_never_crosses_query_or_age_boundary():
    now = [100.0]
    async def run():
        service = reefapi.ReefClient('secret', clock=lambda: now[0], search_fresh_ttl=1,
            search_stale_ttl=60, transport=httpx.MockTransport(lambda request:
                httpx.Response(200, json={'ok': True, 'data': {'listings': [{'ad_id': 'one'}]}})))
        try:
            await service.search('tomsk', 30000, 1)
            assert service.cached_search('tomsk', 30000, 1) is not None
            for query in [('omsk', 30000, 1), ('tomsk', 30001, 1), ('tomsk', 30000, 2)]:
                assert service.cached_search(*query) is None
            now[0] = 161
            assert service.cached_search('tomsk', 30000, 1) is None
        finally: await service.close()
    asyncio.run(run())
