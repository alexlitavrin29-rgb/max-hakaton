"""Read-only HeadHunter search using a previously issued application token."""

import asyncio
import os
from pathlib import Path

import httpx
from dotenv import dotenv_values

from .trudvsem import TrudvsemError, _mapping, _salary, _text, _integer
from ..services.geography import key, legacy_places, same_words
from ..services.vacancies import Vacancy, VacancyPage


BASE_URL = 'https://api.hh.ru'


def settings():
    return {**dotenv_values(Path(__file__).resolve().parents[3] / '.env', interpolate=False), **os.environ}


def enabled():
    return bool(settings().get('HH_ACCESS_TOKEN'))


def _same_region(a, b):
    return same_words(a, b) and same_words(b, a)


class HeadHunterClient:
    def __init__(self, token, *, user_agent='TochkaOpory/1.0 (https://135-106-229-210.sslip.io/)',
                 transport=None, timeout_seconds=15):
        self.timeout_seconds = timeout_seconds
        self._token = token
        self.client = httpx.AsyncClient(transport=transport, timeout=timeout_seconds,
            follow_redirects=False, headers={'HH-User-Agent': user_agent, 'User-Agent': user_agent},
            limits=httpx.Limits(max_connections=12, max_keepalive_connections=6))
        self.areas = None
        self._areas_lock = asyncio.Lock()

    async def close(self):
        await self.client.aclose()

    async def _get(self, path, **kwargs):
        try:
            headers = {'Authorization': 'Bearer ' + self._token} if path != '/areas' else {}
            response = await self.client.get(BASE_URL + path, headers=headers, **kwargs)
            response.raise_for_status()
            return response.json()
        except httpx.HTTPStatusError as error:
            status = error.response.status_code
            raise TrudvsemError('hh_rate_limit' if status == 429 else 'hh_api_error', status) from None
        except httpx.TimeoutException:
            raise TrudvsemError('hh_timeout') from None
        except httpx.RequestError:
            raise TrudvsemError('hh_connection') from None
        except ValueError:
            raise TrudvsemError('hh_invalid_response') from None

    async def load_areas(self):
        async with self._areas_lock:
            if self.areas is not None:
                return
            payload = await self._get('/areas')
            try:
                russia = next(row for row in payload if row['id'] == '113')
                regions = {p['region'] for p in legacy_places()}
                region_names = {tuple(sorted(key(r).split())): r for r in regions}
                areas = {}
                def walk(node, region=None):
                    matched = region_names.get(tuple(sorted(key(node['name']).split())))
                    if matched:
                        region = matched
                    elif region is None and node['id'] != '113':
                        matches = [r for r in regions if _same_region(r, node['name'])]
                        region = matches[0] if len(matches) == 1 else None
                    areas[str(node['id'])] = dict(name=node['name'], region=region)
                    for child in node['areas']:
                        walk(child, region)
                walk(russia)
                self.areas = areas
            except (KeyError, TypeError, StopIteration):
                raise TrudvsemError('hh_invalid_areas') from None

    def area_id(self, place, region_code):
        if place is None and region_code:
            candidates = [p for p in legacy_places() if p['code'] == region_code]
            if not candidates:
                raise TrudvsemError('hh_area_unavailable')
            place = dict(name=candidates[0]['region'], region=candidates[0]['region'], kind='region')
        if place is None:
            return '113'
        matches = []
        for identifier, area in self.areas.items():
            name_matches = (key(place['name']) == key(area['name']) if place['kind'] == 'city'
                            else _same_region(place['name'], area['name']))
            if name_matches and area['region'] and _same_region(place['region'], area['region']):
                matches.append(identifier)
        if len(matches) != 1:
            # Never silently replace an absent/ambiguous town with a wider region.
            raise TrudvsemError('hh_area_unavailable')
        return matches[0]

    def normalize(self, raw):
        identifier, title = str(raw['id']), _text(raw['name'])
        if not identifier.isascii() or not identifier.isdigit() or not title:
            raise ValueError('Invalid vacancy identity')
        area = _mapping(raw.get('area'))
        address = _mapping(raw.get('address'))
        salary = _mapping(raw.get('salary_range')) or _mapping(raw.get('salary'))
        rubles = salary.get('currency') in {'RUR', 'RUB'}
        mode = _mapping(salary.get('mode')).get('id')
        period = {'MONTH': 'month', 'HOUR': 'hour', 'SHIFT': 'shift', 'DAY': 'day',
                  'WEEK': 'week', 'FLY_IN_FLY_OUT': 'rotation'}.get(mode)
        experience = _mapping(raw.get('experience'))
        experience_min = {'noExperience': 0, 'between1And3': 1, 'between3And6': 3,
                          'moreThan6': 7}.get(experience.get('id'))
        snippet = _mapping(raw.get('snippet'))
        schedule = [_text(_mapping(raw.get('schedule')).get('name'))]
        for field in ('work_schedule_by_days', 'work_format', 'working_hours'):
            schedule.extend(_text(v.get('name')) for v in raw.get(field) or [] if isinstance(v, dict))
        return Vacancy(id=identifier, source='hh', title=title,
            company=_text(_mapping(raw.get('employer')).get('name')) or 'Работодатель не указан',
            salary_from=_salary(salary.get('from')) if rubles else None,
            salary_to=_salary(salary.get('to')) if rubles else None,
            salary_period=period, salary_tax={True: 'gross', False: 'net'}.get(salary.get('gross')),
            region=self.areas.get(str(area.get('id')), {}).get('region'),
            city=_text(area.get('name')), address=_text(address.get('raw')) or ', '.join(
                str(address[v]) for v in ('city', 'street', 'building') if address.get(v)) or None,
            experience=_text(experience.get('name')), experience_min_years=experience_min,
            work_formats=tuple(v['id'] for v in raw.get('work_format') or []
                               if isinstance(v, dict) and isinstance(v.get('id'), str)),
            accommodation=None,
            requirements=_text(snippet.get('requirement')), responsibilities=_text(snippet.get('responsibility')),
            education=None, employment=_text(_mapping(raw.get('employment')).get('name')) or
                _text(_mapping(raw.get('employment_form')).get('name')),
            schedule='; '.join(dict.fromkeys(v for v in schedule if v)) or None,
            url='https://hh.ru/vacancy/' + identifier)

    async def search(self, text=None, *, place=None, region_code=None, limit=100, offset=0,
                     experience_from=None, experience_to=None, accommodation=None):
        _integer(limit, 'limit', 1, 100)
        _integer(offset, 'offset', 0)
        # HH exposes at most 2000 search results.
        if offset * limit >= 2000:
            return VacancyPage((), 2000, limit, offset)
        try:
            async with asyncio.timeout(self.timeout_seconds):
                await self.load_areas()
                params = dict(area=self.area_id(place, region_code), per_page=limit, page=offset,
                              no_magic='true', order_by='publication_time')
                if text:
                    params.update(text=text, search_field='name')
                # All conditions are assessed against returned metadata by WorkBranch.
                payload = await self._get('/vacancies', params=params)
                total, pages = payload['found'], payload['pages']
                _integer(total, 'found', 0)
                _integer(pages, 'pages', 0)
                rows = payload['items']
                if not isinstance(rows, list):
                    raise ValueError('Invalid items')
                items = tuple(self.normalize(row) for row in rows if not row.get('archived'))
                return VacancyPage(items, min(total, pages * limit, 2000), limit, offset)
        except TimeoutError:
            raise TrudvsemError('hh_timeout') from None
        except (ValueError, TypeError, KeyError, AttributeError):
            raise TrudvsemError('hh_invalid_response') from None


_default = None


async def search_vacancies(text=None, **parameters):
    global _default
    if _default is None:
        values = settings()
        if not values.get('HH_ACCESS_TOKEN'):
            raise TrudvsemError('hh_not_configured')
        options = {'user_agent': values['HH_USER_AGENT']} if values.get('HH_USER_AGENT') else {}
        _default = HeadHunterClient(values['HH_ACCESS_TOKEN'], **options)
    return await _default.search(text, **parameters)


async def close():
    global _default
    if _default is not None:
        await _default.close()
    _default = None
