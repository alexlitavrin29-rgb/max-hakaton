"""One bounded, read-only HH request; print no credential or provider body."""

import asyncio
import json

from project.llm.integrations import headhunter, trudvsem


async def main():
    token = headhunter.settings().get('HH_ACCESS_TOKEN')
    if not token:
        raise RuntimeError('HH_ACCESS_TOKEN is not configured')
    client = headhunter.HeadHunterClient(token, timeout_seconds=8)
    try:
        try:
            response = await client._get('/vacancies', params={'area': '113', 'per_page': 1, 'page': 0})
        except trudvsem.TrudvsemError as error:
            print(json.dumps({'provider': 'hh', 'status': error.status_code, 'result': error.code}))
            return
        print(json.dumps({'provider': 'hh', 'status': 200,
                          'returned': len(response.get('items', []))}))
    finally:
        await client.close()


if __name__ == '__main__':
    asyncio.run(main())
