"""Read-only prepared directory, shared by the bot and external clients."""
import hashlib
from fastapi import APIRouter, HTTPException, Query
from project.llm.services.geography import resolve, key
from project.llm.services.help_points import catalog, TITLES, is_local

router = APIRouter(prefix='/api/help')


def identified(point):
    identity = '\n'.join(point.get(k, '') for k in ('region_code', 'city', 'name', 'branch'))
    return dict(point, id=hashlib.sha256(identity.encode()).hexdigest()[:20],
                availability='contact_required',
                cost_status='see_conditions' if point.get('cost') not in ('', 'UNVERIFIED') else 'unconfirmed')


def location(place, region):
    name, sep, suffix = place.partition(',')
    choices = resolve(name.strip(), region or (suffix.strip() if sep else None))
    if not choices:
        raise HTTPException(404, 'Населённый пункт не найден')
    if len(choices) != 1 or choices[0].get('matched') == 'suggestion':
        raise HTTPException(409, dict(message='Уточните населённый пункт и регион', choices=choices[:20]))
    return choices[0]


@router.get('/search')
def search(place: str = Query(min_length=1, max_length=150), region: str = Query(default='', max_length=150)):
    name, sep, suffix = place.partition(',')
    choices = resolve(name.strip(), region or (suffix.strip() if sep else None))
    return dict(choices=choices[:20], total=len(choices))


@router.get('/summary')
def summary(place: str = Query(min_length=1, max_length=150), region: str = Query(default='', max_length=150)):
    selected = location(place, region)
    rows = [p for p in catalog() if p['region_code'] == selected['code']]
    local = [p for p in rows if is_local(p, selected)]
    return dict(place=selected, city_total=len(local), region_total=len(rows), categories=[
        dict(id=c, title=title, city_count=sum(c in p['categories'] for p in local),
             region_count=sum(c in p['categories'] for p in rows)) for c, title in TITLES.items()])


@router.get('/points')
def points(place: str = Query(min_length=1, max_length=150), region: str = Query(default='', max_length=150),
           category: str = '', offset: int = Query(default=0, ge=0), limit: int = Query(default=4, ge=1, le=100),
           scope: str = 'auto'):
    if category and category not in TITLES or scope not in {'auto', 'region', 'city'}:
        raise HTTPException(422, 'Недопустимая категория или область поиска')
    selected = location(place, region)
    rows = [p for p in catalog() if p['region_code'] == selected['code'] and (not category or category in p['categories'])]
    local = [p for p in rows if is_local(p, selected)]
    actual = 'city' if scope == 'city' or scope == 'auto' and local else 'region'
    rows = local if actual == 'city' else rows
    rows = sorted(rows, key=lambda p: (p['city_key'] != key(selected['name']), p['city'], p['name']))
    return dict(place=selected, scope=actual, total=len(rows), offset=offset,
                items=[identified(p) for p in rows[offset:offset+limit]],
                next_offset=offset+limit if offset+limit < len(rows) else None)


@router.get('/points/{identifier}')
def point(identifier: str):
    for row in catalog():
        item = identified(row)
        if item['id'] == identifier:
            return item
    raise HTTPException(404, 'Карточка не найдена')
