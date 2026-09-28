"""Run the local MAX prototype: python -m project.llm.bot."""

import argparse
import asyncio
from collections import OrderedDict
from datetime import datetime, timezone
import json
import logging
import os
from pathlib import Path
import time

from dotenv import dotenv_values

from .channels.max import MaxClient, MaxError, callback, message
from .services.flow import PublishedDialogue
from .services.scenario import ScenarioStore
from .services.reminders import ReminderStore
from .services.bot_sessions import BotSessions
from .services.account_epoch import EXPECTED_EPOCH, read_epoch

ROOT = Path(__file__).resolve().parents[2]
logger = logging.getLogger("support_bot")


async def close_network_clients():
    from .services import llm
    from .integrations import reefapi, trudvsem, headhunter
    await llm.close();await reefapi.close();await trudvsem.close();await headhunter.close()


def event_input(event):
    kind = event.get("update_type")
    if kind in {"bot_started", "bot_stopped"}:
        user = event.get("user", {})
        return user.get("user_id"), "", "reset" if kind == "bot_started" else "stop", None
    msg = event.get("message") or {}
    if msg.get("recipient", {}).get("chat_type") != "dialog":
        return None, "", None, None
    if kind == "message_callback":
        c = event.get("callback") or {}
        return c.get("user", {}).get("user_id"), "", c.get("payload"), c.get("callback_id")
    if kind == "message_created":
        sender = msg.get("sender") or {}
        if sender.get("is_bot"):
            return None, "", None, None
        return sender.get("user_id"), (msg.get("body") or {}).get("text") or "", None, None
    return None, "", None, None


class Runner:
    def __init__(self, api, dialogue, store, username, max_concurrency=4):
        self.api, self.dialogue, self.store = api, dialogue, store
        self.started_ms = int(time.time() * 1000)
        self.locks = {}
        self.seen = OrderedDict()
        self.username = username
        self.last_poll = 0.0
        self.health = ROOT / "project/logs/bot-health.json"
        self.health.parent.mkdir(exist_ok=True)
        self.max_concurrency=max_concurrency
        self._group_slots=asyncio.Semaphore(max_concurrency)
        self._seen_lock=asyncio.Lock();self._active_groups=0;self.max_active_groups=0
        self._closing=False
        self._accepted_batches=set()

    def lock(self, user_id):
        return self.locks.setdefault(user_id, asyncio.Lock())

    async def process(self, event):
        user_id, text, payload, callback_id = event_input(event)
        if type(user_id) is not int or (event.get("timestamp") or 0) < self.started_ms:
            return
        identity = callback_id or ((event.get("message") or {}).get("body") or {}).get("mid")
        if identity and not getattr(self.dialogue, 'sessions_store', None):
            async with self._seen_lock:
                if identity in self.seen:return
                self.seen[identity]=True
                if len(self.seen)>2000:self.seen.popitem(last=False)
        async with self.lock(user_id):
            if identity and getattr(self.dialogue, 'sessions_store', None) and await self.dialogue.sessions_store.processed(str(identity)):
                return
            if payload == "stop":
                await self.store.delete_user(user_id)
                self.dialogue.sessions.pop(user_id, None)
                if getattr(self.dialogue, 'sessions_store', None):
                    await self.dialogue.sessions_store.delete(user_id)
                return
            if callback_id:
                try:
                    # MAX now expects an updated message rather than an empty body.
                    original=(event.get("message") or {}).get("body") or {}
                    body={k:original[k] for k in ("text","attachments") if original.get(k) is not None}
                    if body.get("text"): await self.api.answer_callback(callback_id,body)
                except MaxError: logger.warning("callback_ack_failed")
            progress_id = None
            session = self.dialogue.sessions.get(user_id)
            if getattr(session, 'branch', None) in {'work', 'rental'} and (text.strip() or (payload or '').startswith(('w:', 'r:'))):
                try:
                    sent = await self.api.send(user_id, message('Секундочку, обрабатываю запрос для поиска…'))
                    progress_id = ((sent.get('message') or {}).get('body') or {}).get('mid')
                except MaxError:
                    logger.warning('search_progress_send_failed')
            try:
                epoch_token = EXPECTED_EPOCH.set(await read_epoch(self.store.pool, user_id)) if getattr(self.dialogue, 'sessions_store', None) else None
                try:
                    replies = await self.dialogue.handle(user_id, text, payload,
                        event_id=str(identity) if identity else None) if getattr(self.dialogue, 'sessions_store', None) else await self.dialogue.handle(user_id, text, payload)
                finally:
                    if epoch_token is not None:
                        EXPECTED_EPOCH.reset(epoch_token)
            except Exception as error:
                # Do not log provider exception text, update objects, PII, tokens or user text.
                logger.error("dialogue_failed type=%s", type(error).__name__)
                replies = [message("Сейчас не получилось выполнить действие. Попробуй ещё раз или вернись в меню.",
                                   [callback("Главное меню", "menu")])]
            finally:
                if progress_id:
                    try:
                        await self.api.request('DELETE', '/messages', params={'message_id': progress_id})
                    except MaxError:
                        logger.warning('search_progress_delete_failed')
            for reply in replies or []:
                await self.api.send(user_id, reply)

    async def process_batch(self,events):
        if self._closing:return
        batch_task=asyncio.current_task()
        self._accepted_batches.add(batch_task)
        try:
            groups=OrderedDict()
            for event in events:
                user_id,_,_,_=event_input(event)
                groups.setdefault(user_id,[]).append(event)
            async def worker(items):
                async with self._group_slots:
                    self._active_groups+=1;self.max_active_groups=max(self.max_active_groups,self._active_groups)
                    try:
                        for event in items:await self.process(event)
                    finally:self._active_groups-=1
            results=await asyncio.gather(*(worker(items) for items in groups.values()),return_exceptions=True)
            for result in results:
                if isinstance(result,Exception):logger.warning("batch_user_failed type=%s",type(result).__name__)
        finally:
            self._accepted_batches.discard(batch_task)

    async def shutdown(self):
        self._closing=True
        current=asyncio.current_task()
        accepted=[task for task in self._accepted_batches if task is not current]
        if accepted:
            await asyncio.gather(*accepted,return_exceptions=True)

    async def poll(self):
        marker = None
        while True:
            try:
                batch = await self.api.updates(marker)
                self.last_poll = time.time()
                await self.process_batch(batch.get("updates", []))
                marker = batch.get("marker", marker)
            except MaxError as error:
                logger.warning("poll_failed code=%s status=%s", error.code, error.status)
                if error.status in {401, 403}: raise
                await asyncio.sleep(5)

    async def reminders(self):
        last_session_cleanup = 0.0
        while True:
            await self.store.cleanup()
            if getattr(self.dialogue, 'sessions_store', None) and time.monotonic()-last_session_cleanup > 300:
                await self.dialogue.sessions_store.cleanup()
                last_session_cleanup = time.monotonic()
            engine = await self.dialogue.refresh()
            due = await self.store.claim_due() if engine.config.get("reminders_enabled", True) else []
            for row in due:
                async with self.lock(row["user_id"]):
                    current = await self.store.get(row["user_id"], row["id"])
                    if not current or current["state"] != "sending": continue
                    if (datetime.now(timezone.utc) - current["due_at"]).total_seconds() > 60:
                        await self.store.expire(row["id"])
                        continue
                    try:
                        await self.api.send(row["user_id"], engine.say("notification", {"text":row["text"]}, [
                            engine.button("done", "delete:" + row["id"]), engine.button("snooze", "snooze:" + row["id"]),
                            engine.button("cancel", "delete:" + row["id"])]))
                    except MaxError:
                        # Delivery may be ambiguous. Do not retry a possibly delivered notification.
                        await self.store.finish_send(row["id"], False)
                        logger.warning("reminder_delivery_failed")
                    else:
                        await self.store.finish_send(row["id"], True)
            self.health.write_text(json.dumps({"updated_at": time.time(), "last_poll": self.last_poll,
                "username": self.username, "status": "running"}), encoding="utf-8")
            await asyncio.sleep(2)


async def run(check_only=False):
    import asyncpg
    values = {**dotenv_values(ROOT / ".env", interpolate=False), **os.environ}
    api = MaxClient(values.get("MAX_BOT_TOKEN", ""), values.get("MAX_API_BASE_URL", "https://platform-api2.max.ru"))
    pool = None
    try:
        me = await api.request("GET", "/me")
        subscriptions = await api.request("GET", "/subscriptions")
        if subscriptions.get("subscriptions"):
            raise RuntimeError("Webhook configured; polling not started")
        print("MAX connected: https://max.ru/" + str(me.get("username", "")), flush=True)
        if check_only: return
        pool = await asyncpg.create_pool(host=values.get("POSTGRES_HOST", "127.0.0.1"),
            port=int(values.get("POSTGRES_PORT", "5432")), user=values.get("POSTGRES_USER", "support_router"),
            password=values.get("POSTGRES_PASSWORD", ""), database=values.get("POSTGRES_DB", "support_router"), min_size=2, max_size=5)
        # Session advisory lock prevents two polling/scheduler processes for this bot.
        async with pool.acquire() as guard:
            locked = await guard.fetchval("SELECT pg_try_advisory_lock($1)", int(me["user_id"]))
            if not locked: raise RuntimeError("Another bot process is running")
            store = ReminderStore(pool)
            await store.initialize()
            sessions_store = BotSessions(pool)
            await sessions_store.initialize()
            scenarios = ScenarioStore(pool)
            await scenarios.initialize()
            runner = Runner(api, PublishedDialogue(store, scenarios, sessions_store), store, me.get("username"))
            print("Prototype ready. New private messages only; reminders enabled.", flush=True)
            async with asyncio.TaskGroup() as tasks:
                tasks.create_task(runner.poll())
                tasks.create_task(runner.reminders())
    finally:
        await close_network_clients()
        await api.close()
        if pool: await pool.close()


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    try:
        asyncio.run(run(args.check))
    except KeyboardInterrupt:
        pass
    except Exception as error:
        logger.error("startup_failed type=%s", type(error).__name__)
        raise SystemExit(1) from None
