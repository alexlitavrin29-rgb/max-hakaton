"""Read-only ReefAPI boundary with managed connections and a bounded location cache."""
import asyncio
import copy
import json
import logging
import os
import re
import time
from collections import OrderedDict
from pathlib import Path

import httpx
from httpx import Limits
from dotenv import dotenv_values

BASE_URL='https://api.reefapi.com/avito/v1/'
logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)


class ReefError(RuntimeError):
    def __init__(self, code):
        self.code = code
        super().__init__(code)


class ReefClient:
    def __init__(self, secret, *, transport=None, cache_size=128, cache_ttl=86400.0,
                 empty_ttl=30.0, timeout=35.0, clock=time.time, cache_path=None,
                 search_cache_size=64, search_fresh_ttl=120.0, search_stale_ttl=21600.0,
                 location_stale_ttl=2592000.0):
        if not isinstance(secret,str) or not secret.strip():raise ReefError('configuration')
        if (cache_size<1 or cache_ttl<=0 or empty_ttl<=0 or search_cache_size<1 or
                search_fresh_ttl<=0 or search_stale_ttl<search_fresh_ttl or location_stale_ttl<=0):
            raise ValueError('Invalid cache settings')
        self._clock,self._cache_size,self._cache_ttl,self._empty_ttl=clock,cache_size,cache_ttl,empty_ttl
        self._search_cache_size,self._search_fresh_ttl=search_cache_size,search_fresh_ttl
        self._search_stale_ttl,self._location_stale_ttl=search_stale_ttl,location_stale_ttl
        self._cache_path=Path(cache_path) if cache_path else None
        self._cache=OrderedDict();self._location_history=OrderedDict();self._search_cache=OrderedDict()
        self._locks={};self._closed=False
        self._search_locks={};self._cooldown_until=0.0;self._cooldown_error=None
        self.client_creations=1;self.close_count=0
        self.metrics=dict(cache_hit=0,cache_miss=0,cache_eviction=0,search_cache_hit=0,stale_hit=0)
        options=dict(base_url=BASE_URL,headers={'x-api-key':secret},timeout=timeout,
            follow_redirects=False,
            limits=Limits(max_connections=16,max_keepalive_connections=8,keepalive_expiry=30))
        if transport is not None:options['transport']=transport
        self.client=httpx.AsyncClient(**options)
        self._load_cache()

    @staticmethod
    def _search_key(location,budget,page):
        return str(location),None if budget is None else int(budget),int(page)

    def _load_cache(self):
        if self._cache_path is None or not self._cache_path.exists():return
        try:
            payload=json.loads(self._cache_path.read_text(encoding='utf-8'))
            if payload.get('version') != 1:return
            now=self._clock()
            for item in payload.get('locations',[]):
                key,stored_at,data=item.get('key'),item.get('stored_at'),item.get('data')
                if isinstance(key,str) and type(stored_at) in {int,float} and isinstance(data,list) and 0<=now-stored_at<=self._location_stale_ttl:
                    self._location_history[key]=(float(stored_at),copy.deepcopy(data))
                    ttl=self._cache_ttl if data else self._empty_ttl
                    if now-stored_at<ttl:self._cache[key]=(stored_at+ttl,copy.deepcopy(data))
            for item in payload.get('searches',[]):
                raw_key,stored_at,data=item.get('key'),item.get('stored_at'),item.get('data')
                if (isinstance(raw_key,list) and len(raw_key)==3 and type(stored_at) in {int,float} and
                        isinstance(data,dict) and 0<=now-stored_at<=self._search_stale_ttl):
                    self._search_cache[self._search_key(*raw_key)]=(float(stored_at),copy.deepcopy(data))
        except (OSError,ValueError,TypeError):
            logger.warning('Reef cache load failed')

    def _persist_cache(self):
        if self._cache_path is None:return
        payload=dict(version=1,
            locations=[dict(key=key,stored_at=value[0],data=value[1]) for key,value in self._location_history.items()],
            searches=[dict(key=list(key),stored_at=value[0],data=value[1]) for key,value in self._search_cache.items()])
        temporary=self._cache_path.with_suffix(self._cache_path.suffix+'.tmp')
        try:
            self._cache_path.parent.mkdir(parents=True,exist_ok=True)
            temporary.write_text(json.dumps(payload,ensure_ascii=False,separators=(',',':')),encoding='utf-8')
            temporary.replace(self._cache_path)
        except OSError:
            logger.warning('Reef cache persist failed')

    def cached_locations(self,query):
        cached=self._location_history.get(self.cache_key(query))
        if not cached:return None
        age=max(0,self._clock()-cached[0])
        if age>self._location_stale_ttl:return None
        self.metrics['stale_hit']+=1
        return copy.deepcopy(cached[1]),int(age)

    def cached_search(self,location,budget,page):
        cached=self._search_cache.get(self._search_key(location,budget,page))
        if not cached:return None
        age=max(0,self._clock()-cached[0])
        if age>self._search_stale_ttl:return None
        self.metrics['stale_hit']+=1
        return copy.deepcopy(cached[1]),int(age)

    @property
    def is_closed(self):return self._closed

    async def close(self):
        if self._closed:return
        self._closed=True;self.close_count+=1
        await self.client.aclose()

    async def request(self,endpoint,params):
        if self._closed:raise ReefError('closed')
        if endpoint not in {'locations','real_estate/search'}:raise ValueError('Unsupported ReefAPI endpoint')
        if self._clock()<self._cooldown_until:raise ReefError(self._cooldown_error)
        started=time.perf_counter();outcome='cancelled'
        try:
            response=await self.client.post(endpoint,json=params)
            if response.status_code in {401,402,403,429}:
                self._cooldown_until=self._clock()+60
                self._cooldown_error='http_'+str(response.status_code)
            if response.status_code!=200:raise ReefError('http_'+str(response.status_code))
            result=response.json()
            if not isinstance(result,dict) or result.get('ok') is not True or not isinstance(result.get('data'),dict):
                raise ReefError('provider')
            outcome='ok'
            return result['data']
        except ReefError as error:
            outcome=error.code;raise
        except httpx.TimeoutException:
            outcome='timeout';raise ReefError(outcome) from None
        except httpx.HTTPError:
            outcome='connection';raise ReefError(outcome) from None
        except ValueError:
            outcome='invalid_response';raise ReefError(outcome) from None
        finally:
            logger.info('reef_request endpoint=%s outcome=%s duration_ms=%.1f',
                        endpoint,outcome,(time.perf_counter()-started)*1000)

    @staticmethod
    def cache_key(query):return re.sub(r'\s+',' ',query.strip()).casefold()

    async def locations(self,query):
        key=self.cache_key(query);now=self._clock();cached=self._cache.get(key)
        if cached and cached[0]>now:
            self._cache.move_to_end(key);self.metrics['cache_hit']+=1
            return copy.deepcopy(cached[1])
        if cached:self._cache.pop(key,None)
        self.metrics['cache_miss']+=1
        lock=self._locks.setdefault(key,asyncio.Lock())
        try:
            async with lock:
                now=self._clock();cached=self._cache.get(key)
                if cached and cached[0]>now:
                    self.metrics['cache_hit']+=1
                    return copy.deepcopy(cached[1])
                data=await self.request('locations',{'query':query.strip()})
                rows=data.get('locations')
                if not isinstance(rows,list):raise ReefError('invalid_response')
                stored=copy.deepcopy(rows);ttl=self._cache_ttl if rows else self._empty_ttl
                self._cache[key]=(now+ttl,stored);self._cache.move_to_end(key)
                self._location_history[key]=(now,copy.deepcopy(stored));self._location_history.move_to_end(key)
                while len(self._cache)>self._cache_size:
                    self._cache.popitem(last=False);self.metrics['cache_eviction']+=1
                while len(self._location_history)>self._cache_size:self._location_history.popitem(last=False)
                self._persist_cache()
                return copy.deepcopy(stored)
        finally:
            if not lock.locked():self._locks.pop(key,None)

    async def search(self,location,budget,page):
        key=self._search_key(location,budget,page)
        entry=self._search_locks.setdefault(key,[asyncio.Lock(),0]);entry[1]+=1
        try:
            async with entry[0]:
                return await self._search(location,budget,page)
        finally:
            entry[1]-=1
            if not entry[1]:self._search_locks.pop(key,None)

    async def _search(self,location,budget,page):
        params=dict(transaction='rent_long',property_type='apartment',location=location,
                    page=page,include_pii=False,include_sponsored=False)
        if budget is not None:params['price_max']=budget
        key=self._search_key(location,budget,page);cached=self._search_cache.get(key);now=self._clock()
        if cached and now-cached[0]<=self._search_fresh_ttl:
            self._search_cache.move_to_end(key);self.metrics['search_cache_hit']+=1
            return copy.deepcopy(cached[1])
        data=await self.request('real_estate/search',params)
        stored=copy.deepcopy(data);self._search_cache[key]=(now,stored);self._search_cache.move_to_end(key)
        while len(self._search_cache)>self._search_cache_size:self._search_cache.popitem(last=False)
        self._persist_cache()
        return copy.deepcopy(stored)


_default=None
_default_factory=None

def _service():
    global _default,_default_factory
    factory=httpx.AsyncClient
    if _default is None or _default.is_closed or _default_factory is not factory:
        values={**dotenv_values(Path(__file__).resolve().parents[3]/'.env',interpolate=False),**os.environ}
        _default=ReefClient(values.get('HOUSING_API_KEY',''),cache_path=values.get('REEF_CACHE_PATH') or None)
        _default_factory=factory
    return _default

async def close():
    global _default,_default_factory
    if _default is not None:await _default.close();_default=None
    _default_factory=None

async def request(endpoint,params):return await _service().request(endpoint,params)
async def locations(query):return await _service().locations(query)
async def search(location,budget,page):return await _service().search(location,budget,page)
def cached_locations(query):return None if _default is None or _default.is_closed else _default.cached_locations(query)
def cached_search(location,budget,page):return None if _default is None or _default.is_closed else _default.cached_search(location,budget,page)
