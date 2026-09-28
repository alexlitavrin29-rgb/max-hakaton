"""Opt-in synthetic dialogues: real configured LLM, recorded ReefAPI fixtures."""
import asyncio
from copy import deepcopy
import json
from pathlib import Path
import time

from project.admin.rental_draft import configure
from project.admin.seed import initial_config
from project.llm.config import LLMSettings
from project.llm.services.flow import FlowDialogue, PreviewReminders
from project.llm.services import rental
from project.llm.integrations import reefapi

ROOT = Path(__file__).resolve().parents[3]
OUT = ROOT / 'docs/test-results/housing'


async def main():
    settings = LLMSettings.from_env(ROOT / '.env')
    fixture = json.loads((OUT / 'search-tomsk-30000.json').read_text(encoding='utf-8'))
    city = dict(name='Томск', slug='tomsk', region='Томская область', location_id=657600)
    original_llm = rental.call_llm
    records = []

    async def locations(query):
        if query.casefold() == 'советск':
            return [dict(name='Советск', slug='sovetsk_a', region='Первая область', location_id=1),
                    dict(name='Советск', slug='sovetsk_b', region='Вторая область', location_id=2)]
        return [deepcopy(city)] if query.casefold() == 'томск' else []

    async def search(place, budget, page):
        return dict(listings=[r for r in fixture['listings'] if r['price'] <= budget], has_more=False,
                    filters_applied=dict(location=place, price_max=budget, transaction='rent_long', property_type='apartment'))

    reefapi.locations, reefapi.search = locations, search
    cases = [
        ('together', ['Томск до 30 тысяч'], 30000, 'Томск', None),
        ('city_first', ['Томск', 'до 25 тысяч'], 25000, 'Томск', None),
        ('budget_first', ['готов платить максимум 20000 рублей', 'в Томске'], 20000, 'Томск', None),
        ('correction', ['Томск до 30 тысяч', 'нет, не 30, а 25 тысяч'], 25000, 'Томск', None),
        ('units', ['30', 'тысяч', 'Томск'], 30000, 'Томск', None),
        ('extra', ['Томск до 30 тысяч без залога'], 30000, None, 'other'),
        ('ambiguous', ['Советск до 30 тысяч'], 30000, None, 'city'),
        ('salary_not_budget', ['В Томске, мне 20, зарплата 60000, аренда до 25 тысяч'], 25000, 'Томск', None),
    ]
    for name, turns, budget, city_name, awaiting in cases:
        record = dict(case=name, turns=[], source='saved ReefAPI fixture', llm_model=settings.model)

        async def llm(*args, **kwargs):
            record['last_metrics']={}
            result = await original_llm(*args, settings=settings, metrics=record['last_metrics'], **kwargs)
            record['last_extraction'] = json.loads(result)
            return result

        rental.call_llm = llm
        bot = FlowDialogue(PreviewReminders(), configure(initial_config()), 31)
        await bot.handle(1, payload='jump:rental')
        started = time.monotonic()
        for text in turns:
            record.pop('last_extraction',None)
            replies = await bot.handle(1, text)
            st = bot.session(1).rental
            record['turns'].append(dict(input=text, extraction=record.get('last_extraction'), metrics=record.get('last_metrics'),
                replies=[r['text'] for r in replies], trace=deepcopy(bot.session(1).trace),
                understood={k: deepcopy(st[k]) for k in ['city','budget','pending_city','pending_budget','awaiting','other']}))
        st = bot.session(1).rental
        record.update(seconds=round(time.monotonic()-started,2),
            passed=st['budget']==budget and (st['city'] or {}).get('name')==city_name and st['awaiting']==awaiting
                   and not any(t['kind']=='llm_error' for turn in record['turns'] for t in turn['trace']))
        records.append(record)
        (OUT / 'live-llm-fixture-dialogues.json').write_text(json.dumps(records,ensure_ascii=False,indent=2),encoding='utf-8')
        print(json.dumps(dict(case=name,passed=record['passed'],seconds=record['seconds'])),flush=True)
    assert all(r['passed'] for r in records), 'Inspect synthetic dialogue report'


if __name__ == '__main__':
    asyncio.run(main())
