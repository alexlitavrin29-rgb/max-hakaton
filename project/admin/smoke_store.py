"""Isolated end-to-end storage test. Never publishes into the live scenario tables."""

import asyncio,json,os,secrets
from copy import deepcopy

import asyncpg

from project.llm.services.scenario import ScenarioStore,ScenarioError
from project.llm.services.flow import PublishedDialogue,PreviewReminders,FlowDialogue


async def main():
    args=dict(host=os.getenv("POSTGRES_HOST","postgres"),port=int(os.getenv("POSTGRES_PORT","5432")),user=os.getenv("POSTGRES_USER","support_router"),password=os.environ["POSTGRES_PASSWORD"],database=os.getenv("POSTGRES_DB","support_router"))
    admin=await asyncpg.connect(**args); schema="editor_smoke_"+secrets.token_hex(8);pool=None
    try:
        await admin.execute(f'CREATE SCHEMA "{schema}"')
        pool=await asyncpg.create_pool(**args,min_size=1,max_size=3,server_settings={"search_path":schema})
        store=ScenarioStore(pool);await store.initialize()
        original=await store.read(); config=deepcopy(original["config"])
        welcome=next(n for n in config["nodes"] if n["id"]==config["start"])
        welcome["text"]="SYNTHETIC EDIT";welcome["buttons"][0]["label"]="SYNTHETIC BUTTON"
        revision=await store.save(config,original["revision"])
        live=PublishedDialogue(PreviewReminders(),store)
        assert (await live.handle(1,payload="reset"))[0]["text"]!="SYNTHETIC EDIT"
        preview=FlowDialogue(PreviewReminders(),config,revision)
        assert (await preview.handle(1,payload="reset"))[0]["text"]=="SYNTHETIC EDIT"
        await store.publish(revision,"Synthetic publish")
        assert (await live.handle(1,payload="reset"))[0]["text"]=="SYNTHETIC EDIT"
        try: await store.save(config,original["revision"])
        except ScenarioError: pass
        else: raise AssertionError("Stale edit should be rejected")
        revision=await store.restore(original["published_id"],revision)
        assert (await live.handle(1,payload="reset"))[0]["text"]=="SYNTHETIC EDIT"
        await store.publish(revision,"Synthetic restore")
        assert (await live.handle(1,payload="reset"))[0]["text"]!="SYNTHETIC EDIT"
        await store.event("search","work");await store.event("search","work")
        assert await pool.fetchval("SELECT count FROM bot_event_counts WHERE kind='search'")==2
        assert len(await store.history())==3
        print("PASS: draft isolation, preview, publication consumed by MAX engine, optimistic locking, restore+publish, immutable history, anonymous counters")
        from project.admin import app as editor
        async def judge(*args,**kwargs): return '{"score":5,"passed":true,"reason":"Synthetic judge result"}'
        editor.call_llm=judge
        editor.app.state.pool=pool
        editor.app.state.eval_lock=asyncio.Lock()
        editor.app.state.jobs={"test":dict(status="queued",results=[],total=0)}
        data=await store.read()
        data["config"]["evaluations"]=[dict(id="case",title="Synthetic",messages=["/start"],criteria="Greeting",good="",bad="")]
        await editor.evaluation_job("test",data,["case"])
        job=editor.app.state.jobs["test"]
        assert job["status"]=="done" and job["results"][0]["judge"]["score"]==5
        assert await pool.fetchval("SELECT count(*) FROM evaluation_runs")==1
        print("PASS: evaluation execution, structured judge result and durable run history (mock judge; real database)")
    finally:
        if pool: await pool.close()
        await admin.execute(f'DROP SCHEMA "{schema}" CASCADE');await admin.close()


if __name__=="__main__": asyncio.run(main())
