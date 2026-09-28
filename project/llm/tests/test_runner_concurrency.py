import asyncio
from collections import defaultdict

from project.llm.bot import Runner


def event(user,mid,text):
    return {'update_type':'message_created','timestamp':10**15,
            'message':{'recipient':{'chat_type':'dialog'},'sender':{'user_id':user},
                       'body':{'mid':mid,'text':text}}}


class Api:
    def __init__(self):self.sent=[]
    async def send(self,user,reply):self.sent.append((user,reply['text']))

class Store:
    async def delete_user(self,user):pass


def test_batch_parallelizes_users_and_preserves_each_user_order():
    started=defaultdict(asyncio.Event);release=asyncio.Event()
    class Dialogue:
        sessions={}
        async def handle(self,user,text,payload):
            started[user].set()
            if text=='slow':await release.wait()
            return [{'text':text}]
    async def run():
        api=Api();runner=Runner(api,Dialogue(),Store(),'test',max_concurrency=2)
        task=asyncio.create_task(runner.process_batch([
            event(1,'a','slow'),event(1,'b','second'),event(2,'c','ready')]))
        await started[1].wait();await asyncio.wait_for(started[2].wait(),.2)
        assert not started[1].is_set() or not any(text=='second' for _,text in api.sent)
        release.set();await task
        assert [text for user,text in api.sent if user==1]==['slow','second']
        assert runner.max_active_groups==2
    asyncio.run(run())


def test_batch_error_does_not_cancel_other_user_and_deduplicates():
    class Dialogue:
        sessions={}
        async def handle(self,user,text,payload):
            if user==1:raise RuntimeError('private')
            return [{'text':text}]
    async def run():
        api=Api();runner=Runner(api,Dialogue(),Store(),'test',max_concurrency=2)
        await runner.process_batch([event(1,'a','bad'),event(2,'b','ok'),event(2,'b','duplicate')])
        assert [item for item in api.sent if item[0]==2]==[(2,'ok')]
        assert len([item for item in api.sent if item[0]==1])==1
    asyncio.run(run())


def test_shutdown_waits_for_an_accepted_batch_and_rejects_new_events():
    started = asyncio.Event()
    release = asyncio.Event()

    class Dialogue:
        sessions = {}

        async def handle(self, user, text, payload):
            started.set()
            await release.wait()
            return [{'text': text}]

    async def run():
        api = Api()
        runner = Runner(api, Dialogue(), Store(), 'test', max_concurrency=1)
        accepted = asyncio.create_task(runner.process_batch([event(1, 'a', 'accepted')]))
        await started.wait()
        stopping = asyncio.create_task(runner.shutdown())
        await asyncio.sleep(0)
        assert not stopping.done()
        release.set()
        await stopping
        await accepted
        await runner.process_batch([event(2, 'b', 'too late')])
        assert api.sent == [(1, 'accepted')]

    asyncio.run(run())


def test_ten_users_respect_group_limit_and_one_user_uses_one_slot():
    release = asyncio.Event()
    all_started = asyncio.Event()
    active = 0
    max_active = 0

    class Dialogue:
        sessions = {}

        async def handle(self, user, text, payload):
            nonlocal active, max_active
            active += 1
            max_active = max(max_active, active)
            if active == 3:
                all_started.set()
            await release.wait()
            active -= 1
            return [{'text': text}]

    async def run():
        api = Api()
        runner = Runner(api, Dialogue(), Store(), 'test', max_concurrency=3)
        events = [event(user, f'm{user}', str(user)) for user in range(10)]
        events += [event(0, 'm0-second', 'second')]
        task = asyncio.create_task(runner.process_batch(events))
        await asyncio.wait_for(all_started.wait(), .2)
        assert max_active == 3
        release.set()
        await task
        assert [text for user, text in api.sent if user == 0] == ['0', 'second']
        assert runner.max_active_groups == 3

    asyncio.run(run())
