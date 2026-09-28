"""Search both sources concurrently, retaining independent per-session cursors."""

import asyncio
from itertools import zip_longest
from types import SimpleNamespace

from ..integrations import headhunter, trudvsem


async def search_vacancies(text=None, *, place=None, source_offsets=None, **parameters):
    if not headhunter.enabled():
        return await trudvsem.search_vacancies(text, **parameters)
    cursors = source_offsets if source_offsets is not None else {}
    for name in ('trudvsem', 'hh'):
        cursors.setdefault(name, parameters.get('offset', 0))
    active = [name for name, offset in cursors.items() if offset is not None]
    calls = []
    for name in active:
        actual = {**parameters, 'offset': cursors[name]}
        calls.append(headhunter.search_vacancies(text, place=place, **actual) if name == 'hh'
                     else trudvsem.search_vacancies(text, **actual))
    results = await asyncio.gather(*calls, return_exceptions=True)
    pages, failures = [], []
    for name, result in zip(active, results):
        if isinstance(result, trudvsem.TrudvsemError):
            failures.append(name)
        elif isinstance(result, BaseException):
            raise result
        else:
            pages.append(result)
            cursors[name] = result.next_offset
    if failures and not pages:
        raise trudvsem.TrudvsemError('sources_unavailable')
    items = tuple(item for pair in zip_longest(*(p.items for p in pages)) for item in pair if item is not None)
    ages = [p.stale_age_seconds for p in pages if getattr(p, 'stale_age_seconds', None) is not None]
    return SimpleNamespace(items=items, next_offset=parameters.get('offset', 0) + 1
        if any(offset is not None for offset in cursors.values()) else None,
        attempt_count=max((getattr(p, 'attempt_count', 1) for p in pages), default=1),
        stale_age_seconds=max(ages) if ages else None, failed_sources=tuple(failures))
