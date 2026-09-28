"""Performance contracts without timing assertions or live provider calls."""
import asyncio

import httpx
import pytest

from project.llm.services import geography
from project.llm.integrations.reefapi import ReefClient


def test_full_address_does_not_morphologically_scan_city_directory(monkeypatch):
    calls=[]
    original=geography.same_words
    def counted(*args):
        calls.append(args)
        return original(*args)
    monkeypatch.setattr(geography,'same_words',counted)
    assert geography.resolve_legacy('Томская область, город Томск, улица Ленина, дом 10, офис 15')==[]
    assert calls==[], 'Different word counts cannot match a directory name'


@pytest.mark.parametrize('name,region',[
    ('Томск',None),('Томске',None),('спб',None),('Ростов',None),
    ('Ростов','Ярославская область'),('Нижнем Новгороде',None),
    ('Несуществующее место',None),('Томск','Омская область'),
])
def test_faster_lookup_preserves_legacy_matching(name,region):
    def reference(name,region=None):
        candidate=geography.ALIASES.get(geography.key(name),geography.key(name))
        matches=[p for p in geography.legacy_places() if geography.key(p['name'])==candidate]
        if not matches:
            matches=[p for p in geography.legacy_places() if geography.same_words(p['name'],name)
                     and len(geography.words(p['name']))==len(geography.words(name))]
        if region:
            codes={p['code'] for p in reference(region) if p['kind']=='region' or p['code'][:2] in {'77','78','92'}}
            matches=[p for p in matches if p['code'] in codes]
        return matches
    expected=reference(name,region)
    actual=geography.resolve_legacy(name,region)
    assert actual==expected
    if actual:
        actual[0]['name']='modified by caller'
        assert geography.resolve_legacy(name,region)==expected


def test_location_directory_survives_hour_and_restart_but_refreshes_daily(tmp_path):
    now=[100.0];calls=[]
    def handler(request):
        calls.append(request.url.path)
        return httpx.Response(200,json={'ok':True,'data':{'locations':[{'name':'Томск','slug':'tomsk'}]}})
    async def run():
        options=dict(transport=httpx.MockTransport(handler),clock=lambda:now[0],cache_path=tmp_path/'reef.json')
        first=ReefClient('test',**options)
        try:assert (await first.locations('Томск'))[0]['slug']=='tomsk'
        finally:await first.close()
        now[0]+=3600
        second=ReefClient('test',**options)
        try:
            rows=await second.locations('томск')
            assert len(calls)==1
            rows[0]['slug']='mutated'
            assert (await second.locations('Томск'))[0]['slug']=='tomsk'
            now[0]=100+86401
            await second.locations('Томск')
            assert len(calls)==2
        finally:await second.close()
    asyncio.run(run())


def test_longer_location_cache_does_not_extend_listing_or_empty_cache():
    now=[100.0];calls=[]
    def handler(request):
        calls.append(request.url.path)
        return httpx.Response(200,json={'ok':True,'data':{'locations':[],'listings':[]}})
    async def run():
        service=ReefClient('test',transport=httpx.MockTransport(handler),clock=lambda:now[0])
        try:
            await service.locations('unknown')
            await service.search('tomsk',30000,1)
            now[0]+=121
            await service.locations('unknown')
            await service.search('tomsk',30000,1)
            assert len(calls)==4
        finally:await service.close()
    asyncio.run(run())
