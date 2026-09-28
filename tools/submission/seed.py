"""Install the reviewed scenario into a NEW database; never replace existing data."""
import asyncio
import json
import os
from pathlib import Path

import asyncpg
from project.llm.services.scenario import ScenarioStore, validate


async def main():
    snapshot = json.loads(Path('submission/scenario.json').read_text(encoding='utf-8-sig'))
    config = validate(snapshot['config'])
    pool = await asyncpg.create_pool(host=os.environ['POSTGRES_HOST'],
        port=int(os.getenv('POSTGRES_PORT', '5432')), user=os.environ['POSTGRES_USER'],
        password=os.environ['POSTGRES_PASSWORD'], database=os.environ['POSTGRES_DB'])
    try:
        exists = await pool.fetchval("SELECT to_regclass('public.scenario_state')")
        if exists and await pool.fetchval('SELECT EXISTS(SELECT 1 FROM scenario_state)'):
            print('Existing scenario retained; no changes.')
            return
        store = ScenarioStore(pool)
        await store.initialize()
        current = await store.read()
        revision = await store.save(config, current['revision'])
        await store.publish(revision, 'Submission snapshot, production version ' + str(snapshot['published_version']))
        print('Reviewed scenario installed into new database.')
    finally:
        await pool.close()


if __name__ == '__main__':
    asyncio.run(main())
