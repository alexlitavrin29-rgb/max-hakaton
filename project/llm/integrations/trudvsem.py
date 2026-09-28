"""Read-only, unauthenticated adapter for the official Trudvsem open API."""

import asyncio
import copy
import json
import logging
import math
import os
import time
from collections import OrderedDict
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import quote, urlsplit

import httpx
from httpx import Limits

from ..services.vacancies import Vacancy, VacancyContact, VacancyPage

BASE_URL = "https://opendata.trudvsem.ru/api/v1/vacancies"
logger = logging.getLogger(__name__)
_TRANSIENT_HTTP = {429, 500, 502, 503, 504}


class TrudvsemError(RuntimeError):
    """Safe backend error, without upstream bodies or user search parameters."""

    def __init__(self, code: str, status_code: int | None = None) -> None:
        self.code = code
        self.status_code = status_code
        super().__init__(f"Vacancy search failed: {code}.")


class TrudvsemClient:
    def __init__(self, *, transport=None, timeout_seconds=30.0, cache_path=None,
                 cache_size=64, stale_ttl=21600.0, clock=time.time):
        if cache_size < 1 or stale_ttl <= 0:
            raise ValueError("Invalid cache settings")
        self.timeout_seconds=timeout_seconds;self._closed=False
        self._cache_path=Path(cache_path) if cache_path else None
        self._cache_size,self._stale_ttl,self._clock=cache_size,stale_ttl,clock
        self._cache=OrderedDict()
        self.fresh_ttl=0.0
        self.client_creations=1;self.close_count=0
        options=dict(timeout=timeout_seconds,follow_redirects=False,
            limits=Limits(max_connections=24,max_keepalive_connections=12,keepalive_expiry=30))
        if transport is not None:options['transport']=transport
        self.client=httpx.AsyncClient(**options)
        self._load_cache()

    def _load_cache(self):
        if self._cache_path is None or not self._cache_path.exists():return
        try:
            saved=json.loads(self._cache_path.read_text(encoding="utf-8"))
            if saved.get("version")!=1:return
            now=self._clock()
            for item in saved.get("searches",[]):
                key,stored_at,payload=item.get("key"),item.get("stored_at"),item.get("payload")
                if (isinstance(key,str) and type(stored_at) in {int,float} and isinstance(payload,dict)
                        and 0<=now-stored_at<=self._stale_ttl):
                    self._cache[key]=(float(stored_at),payload)
        except (OSError,ValueError,TypeError):
            logger.warning("Trudvsem cache load failed")

    def _persist_cache(self):
        if self._cache_path is None:return
        data={"version":1,"searches":[{"key":key,"stored_at":value[0],"payload":value[1]}
            for key,value in self._cache.items()]}
        temporary=self._cache_path.with_suffix(self._cache_path.suffix+".tmp")
        try:
            self._cache_path.parent.mkdir(parents=True,exist_ok=True)
            temporary.write_text(json.dumps(data,ensure_ascii=False,separators=(",",":")),encoding="utf-8")
            temporary.replace(self._cache_path)
        except OSError:
            logger.warning("Trudvsem cache persist failed")

    def store_cache(self,key,payload):
        self._cache[key]=(self._clock(),copy.deepcopy(payload));self._cache.move_to_end(key)
        while len(self._cache)>self._cache_size:self._cache.popitem(last=False)
        self._persist_cache()

    def cached(self,key):
        item=self._cache.get(key)
        if not item:return None
        age=max(0,self._clock()-item[0])
        if age>self._stale_ttl:return None
        self._cache.move_to_end(key)
        return copy.deepcopy(item[1]),int(age)

    @property
    def is_closed(self):return self._closed

    async def close(self):
        if self._closed:return
        self._closed=True;self.close_count+=1
        await self.client.aclose()

    async def search(self,text=None,**kwargs):
        if self._closed:raise TrudvsemError('closed')
        kwargs.setdefault('timeout_seconds',self.timeout_seconds)
        return await search_vacancies(text,service=self,**kwargs)


class _PlainText(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []

    def handle_data(self, data: str) -> None:
        self.parts.append(data)

    def handle_starttag(self, tag: str, attrs: list) -> None:
        self.parts.append(" ")

    def handle_endtag(self, tag: str) -> None:
        self.parts.append(" ")


def _text(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    parser = _PlainText()
    parser.feed(value)
    return " ".join("".join(parser.parts).split()) or None


def _mapping(value: object) -> dict:
    return value if isinstance(value, dict) else {}


def _salary(value: object) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float, str)):
        return None
    try:
        number = float(value)
    except ValueError:
        return None
    return number if math.isfinite(number) and number >= 0 else None


def _normalize(raw: dict, *, housing_filter: bool) -> Vacancy:
    company = _mapping(raw.get("company"))
    requirement = _mapping(raw.get("requirement"))
    identifier = _text(raw.get("id"))
    title = _text(raw.get("job-name"))
    url = raw.get("vac_url")
    if not url and identifier and company.get("companycode"):
        url = "https://trudvsem.ru/vacancy/card/{}/{}".format(
            quote(str(company["companycode"]), safe=""), quote(identifier, safe="")
        )
    if not identifier or not title or not isinstance(url, str):
        raise ValueError("Missing vacancy identity")
    parsed_url = urlsplit(url)
    if parsed_url.scheme not in {"https", "http"} or not parsed_url.hostname:
        raise ValueError("Invalid vacancy URL")

    addresses = _mapping(raw.get("addresses")).get("address") or []
    if isinstance(addresses, dict):
        addresses = [addresses]
    locations = [_text(item.get("location")) for item in addresses if isinstance(item, dict)]
    address = "; ".join(dict.fromkeys(item for item in locations if item)) or None
    experience = requirement.get("experience")
    if isinstance(experience, (int, float)) and not isinstance(experience, bool):
        experience = str(experience)
    else:
        experience = _text(experience)

    housing = raw.get("accommodation")
    if isinstance(housing, bool):
        accommodation = housing
    elif isinstance(housing, str) and housing.lower() in {"true", "false"}:
        accommodation = housing.lower() == "true"
    else:
        # A requested filter is not evidence that a sparse record confirms housing.
        accommodation = None

    contacts = []
    for item in raw.get("contact_list") or []:
        if isinstance(item, dict):
            value = _text(item.get("contact_value"))
            if value:
                contacts.append(VacancyContact(_text(item.get("contact_type")) or "Контакт", value))

    return Vacancy(
        id=identifier,
        source="trudvsem",
        title=title,
        company=_text(company.get("name")) or "Работодатель не указан",
        salary_from=_salary(raw.get("salary_min")),
        salary_to=_salary(raw.get("salary_max")),
        region=_text(_mapping(raw.get("region")).get("name")),
        city=_text(raw.get("city")),
        address=address,
        experience=experience,
        accommodation=accommodation,
        requirements=(
            _text(raw.get("requirements")) or _text(requirement.get("qualification"))
            or _text(raw.get("qualification"))
        ),
        responsibilities=_text(raw.get("duty")),
        url=url,
        contacts=tuple(contacts),
        contact_person=_text(raw.get("contact_person")),
        education=_text(requirement.get("education")),
        schedule=_text(raw.get("schedule")),
        employment=_text(raw.get("employment")),
    )


def _integer(value: object, name: str, minimum: int, maximum: int | None = None) -> None:
    if type(value) is not int or value < minimum or (maximum is not None and value > maximum):
        raise ValueError(f"Invalid {name}.")


def _parse_payload(payload,*,limit,offset,accommodation,attempt_count=1,stale_age_seconds=None):
    try:
        if not isinstance(payload,dict):raise ValueError("Invalid envelope")
        status=str(payload.get("status"))
        if status!="200":
            if status.isdigit():raise TrudvsemError("rate_limit" if status=="429" else "api_error",int(status))
            raise ValueError("Missing status")
        total_raw=payload["meta"]["total"]
        if isinstance(total_raw,str) and total_raw.isascii() and total_raw.isdigit():total_raw=int(total_raw)
        _integer(total_raw,"total",0)
        results=payload["results"]
        if not isinstance(results,dict):raise ValueError("Invalid results")
        rows=results.get("vacancies")
        if rows is None and offset*limit>=total_raw:rows=[]
        if not isinstance(rows,list):raise ValueError("Invalid vacancies")
        items=tuple(_normalize(row["vacancy"],housing_filter=accommodation is True) for row in rows)
        return VacancyPage(items=items,total=total_raw,limit=limit,offset=offset,
                           attempt_count=attempt_count,stale_age_seconds=stale_age_seconds)
    except TrudvsemError:raise
    except (ValueError,TypeError,KeyError,AttributeError,OverflowError):
        raise TrudvsemError("invalid_response") from None


async def search_vacancies(
    text: str | None = None,
    *,
    region_code: str | None = None,
    experience_from: int | None = None,
    experience_to: int | None = None,
    accommodation: bool | None = None,
    limit: int = 5,
    offset: int = 0,
    timeout_seconds: float = 30.0,
    service: TrudvsemClient | None = None,
) -> VacancyPage:
    """Search by text, region or both. offset is a zero-based page number.

    experience_* are years REQUIRED by the vacancy, not the applicant's tenure.
    accommodation=None omits the filter; False explicitly requests no housing.
    No results return an empty page; provider failures raise TrudvsemError.
    """
    _integer(limit, "limit", 1, 100)
    _integer(offset, "offset", 0)
    for name, value in (("experience_from", experience_from), ("experience_to", experience_to)):
        if value is not None:
            _integer(value, name, 0)
    if experience_from is not None and experience_to is not None and experience_from > experience_to:
        raise ValueError("experience_from must not exceed experience_to.")
    if accommodation is not None and type(accommodation) is not bool:
        raise ValueError("accommodation must be a boolean or None.")
    if not math.isfinite(timeout_seconds) or timeout_seconds <= 0:
        raise ValueError("timeout_seconds must be positive and finite.")

    url = BASE_URL
    if region_code is not None:
        if not isinstance(region_code, str) or not region_code.isascii() or not region_code.isdigit():
            raise ValueError("region_code must contain ASCII digits.")
        url += "/region/" + quote(region_code, safe="")
    params: dict[str, str | int] = {"limit": limit, "offset": offset}
    if text is not None:
        if not isinstance(text, str):
            raise ValueError("text must be a string or None.")
        if text.strip():
            params["text"] = text.strip()
    if experience_from is not None:
        params["experienceFrom"] = experience_from
    if experience_to is not None:
        params["experienceTo"] = experience_to
    if accommodation is not None:
        params["accommodation"] = str(accommodation).lower()

    managed=service or _service()
    cache_key=json.dumps([url,sorted(params.items())],ensure_ascii=False,separators=(",",":"))
    fresh=managed.cached(cache_key) if managed.fresh_ttl>0 else None
    if fresh and fresh[1]<managed.fresh_ttl:
        return _parse_payload(fresh[0],limit=limit,offset=offset,accommodation=accommodation,attempt_count=0)
    attempt=0;failure=None
    try:
        async with asyncio.timeout(timeout_seconds):
            while attempt<2:
                attempt+=1
                try:
                    response=await managed.client.get(url,params=params,timeout=timeout_seconds)
                    response.raise_for_status()
                    payload=response.json()
                    page=_parse_payload(payload,limit=limit,offset=offset,accommodation=accommodation,
                                        attempt_count=attempt)
                    managed.store_cache(cache_key,payload)
                    return page
                except httpx.TimeoutException:
                    failure=TrudvsemError("timeout");break
                except httpx.HTTPStatusError as error:
                    status=error.response.status_code
                    failure=TrudvsemError("rate_limit" if status==429 else "api_error",status)
                except httpx.RequestError:
                    failure=TrudvsemError("connection")
                except (ValueError,TypeError):
                    failure=TrudvsemError("invalid_response")
                except TrudvsemError as error:
                    failure=error
                transient=(failure.code in {"connection","rate_limit","invalid_response"} or
                           failure.status_code in _TRANSIENT_HTTP)
                if attempt==1 and transient:
                    logger.warning("Trudvsem retry code=%s status=%s attempt=2",failure.code,failure.status_code)
                    continue
                break
    except TimeoutError:
        failure=TrudvsemError("timeout")
    cached=managed.cached(cache_key) if failure and (failure.code in {"timeout","connection","rate_limit","invalid_response"}
                                                     or failure.status_code in _TRANSIENT_HTTP) else None
    if cached:
        payload,age=cached
        logger.warning("Trudvsem exact cache fallback age_seconds=%s",age)
        return _parse_payload(payload,limit=limit,offset=offset,accommodation=accommodation,
                              attempt_count=max(1,attempt),stale_age_seconds=age)
    raise failure from None


_default=None
_default_factory=None

def _service():
    global _default,_default_factory
    factory=id(httpx.AsyncClient)
    if _default is None or _default.is_closed or _default_factory!=factory:
        _default=TrudvsemClient(cache_path=os.environ.get("TRUDVSEM_CACHE_PATH") or None);_default_factory=factory
        _default.fresh_ttl=float(os.environ.get('TRUDVSEM_FRESH_TTL','0'))
    return _default

async def close():
    global _default,_default_factory
    if _default is not None:await _default.close()
    _default=None;_default_factory=None
