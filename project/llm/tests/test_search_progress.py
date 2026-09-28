import asyncio
from types import SimpleNamespace

import httpx
import pytest

from project.llm.bot import Runner
from project.llm.channels.max import MaxClient
from project.llm.tests.test_runner_concurrency import event, Store


@pytest.mark.parametrize('branch', ['work', 'rental'])
@pytest.mark.parametrize('fails', [False, True])
def test_progress_precedes_processing_and_is_removed_before_reply(branch, fails):
    calls = []

    def transport(request):
        calls.append((request.method, request.url.params.get('message_id')))
        if request.method == 'POST':
            return httpx.Response(200, json={'message': {'body': {'mid': 'progress-1'}}})
        return httpx.Response(200, json={'success': True})

    class Dialogue:
        sessions = {1: SimpleNamespace(branch=branch)}

        async def handle(self, user, text, payload):
            assert calls == [('POST', None)]
            if fails:
                raise RuntimeError('provider unavailable')
            return [{'text': 'Результат'}]

    async def run():
        api = MaxClient('test', transport=httpx.MockTransport(transport), min_send_interval=0)
        try:
            await Runner(api, Dialogue(), Store(), 'test').process(event(1, 'input', 'Москва'))
        finally:
            await api.close()
        assert calls == [('POST', None), ('DELETE', 'progress-1'), ('POST', None)]

    asyncio.run(run())
