"""Versioned product configuration shared by the editor, preview and MAX."""

import json
import re
from urllib.parse import urlsplit


class ScenarioError(ValueError):
    pass


def validate(config):
    errors = []
    nodes = config.get("nodes", [])
    branches = config.get("branches", [])
    ids = {n.get("id") for n in nodes}
    branch_ids = {b.get("id") for b in branches}
    if not nodes or len(nodes) > 1000 or len(ids) != len(nodes):
        errors.append("Нужны шаги с уникальными идентификаторами; максимум 1000.")
    if len(branch_ids) != len(branches): errors.append("Идентификаторы веток повторяются.")
    if config.get("start") not in ids or config.get("menu") not in ids:
        errors.append("Приветствие и главное меню должны ссылаться на существующие шаги.")
    for item in nodes + branches:
        if not re.fullmatch(r"[a-zA-Z][a-zA-Z0-9_-]{0,79}", str(item.get("id", ""))):
            errors.append("Недопустимый идентификатор шага или ветки.")
    def target(value, label):
        if value and value not in ids: errors.append(f"{label}: переход «{value}» ведёт в удалённый шаг.")
    for b in branches:
        target(b.get("entry"), b.get("title", "Ветка"))
        if not b.get("roles") or any(r not in {"child","parent","candidate"} for r in b.get("roles",[])):
            errors.append(f"{b.get('title')}: выберите хотя бы одну допустимую роль.")
    for k,v in config.get("bindings",{}).items(): target(v,k)
    for n in nodes:
        title = n.get("title", n.get("id", "Шаг"))
        if n.get("branch") not in branch_ids: errors.append(f"{title}: нет ветки.")
        if n.get("kind") not in {"message", "question", "action", "answer"}: errors.append(f"{title}: неизвестный тип шага.")
        if n.get("action") not in {None, "", "search", "search_more", "search_change", "search_miss", "checklist", "reminders", "reminder_new", "reminder_tasks", "social"}:
            errors.append(f"{title}: неизвестное действие.")
        if n.get("kind") == "question" and not re.fullmatch(r"[a-z][a-z0-9_]{0,49}", n.get("field", "")):
            errors.append(f"{title}: нужно указать поле ответа.")
        if n.get("kind")=="question" and not n.get("next"): errors.append(f"{title}: выберите следующий шаг после ответа.")
        if n.get("input_type", "text") not in {"text", "number", "city", "boolean"}: errors.append(f"{title}: неизвестный формат ответа.")
        if len(n.get("text", "")) > 3500 or len(n.get("adult_text", "")) > 3500: errors.append(f"{title}: текст длиннее 3500 символов.")
        target(n.get("next"), title)
        for route in n.get("routes", []): target(route.get("target"), title)
        if len(n.get("buttons", [])) > 25: errors.append(f"{title}: максимум 25 кнопок.")
        for button in n.get("buttons", []):
            target(button.get("target"), title)
            if not button.get("label") or len(button["label"]) > 128: errors.append(f"{title}: название кнопки — от 1 до 128 символов.")
            if button.get("url") and urlsplit(button["url"]).scheme != "https": errors.append(f"{title}: ссылка кнопки должна начинаться с https://.")
            if not button.get("url") and not button.get("target"): errors.append(f"{title}: кнопке нужен переход или ссылка.")
        for rule in n.get("routes", []) + n.get("conditions", []) + [r for b in n.get("buttons", []) for r in b.get("conditions", [])]:
            if rule.get("op") not in {"eq", "ne", "lt", "lte", "gt", "gte", "contains", "known"}:
                errors.append(f"{title}: неизвестное условие.")
        for key in n.get("sources", []):
            if key not in config.get("sources", {}): errors.append(f"{title}: источник «{key}» отсутствует.")
    for key, template in config.get("ui", {}).items():
        target(template.get("target"), key)
        if len(template.get("text", "")) > 3500: errors.append(f"{key}: слишком длинный текст.")
    for key, source in config.get("sources", {}).items():
        if urlsplit(source.get("url", "")).scheme != "https": errors.append(f"Источник {key}: нужна HTTPS-ссылка.")
    for material in config.get("materials", []):
        for key in material.get("sources", []):
            if key not in config.get("sources", {}): errors.append(f"Материал {material.get('title')}: отсутствует источник {key}.")
    if len(json.dumps(config, ensure_ascii=False)) > 2_000_000: errors.append("Конфигурация превышает 2 МБ.")
    search = config.get("search", {})
    if not 1 <= int(search.get("page_size", 5)) <= 10: errors.append("За один показ — от 1 до 10 вакансий.")
    if not 1 <= int(search.get("scan_pages", 3)) <= 5: errors.append("За запрос просматривается от 1 до 5 страниц API.")
    if not 0 <= int(search.get("min_age",16)) <= 100: errors.append("Возраст для поиска — от 0 до 100.")
    if not 1 <= int(search.get("hh_after",2)) <= 20: errors.append("Предлагать HH после 1–20 неудачных подборов.")
    if errors: raise ScenarioError("\n".join(errors))
    return config


class ScenarioStore:
    def __init__(self, pool): self.pool = pool

    async def initialize(self):
        await self.pool.execute('''
            CREATE TABLE IF NOT EXISTS scenario_versions (
                id BIGSERIAL PRIMARY KEY, config JSONB NOT NULL, note TEXT NOT NULL,
                created_at TIMESTAMPTZ NOT NULL DEFAULT now());
            CREATE TABLE IF NOT EXISTS scenario_state (
                id INTEGER PRIMARY KEY CHECK(id=1), draft JSONB NOT NULL,
                revision INTEGER NOT NULL DEFAULT 1, published_id BIGINT NOT NULL REFERENCES scenario_versions(id));
            CREATE TABLE IF NOT EXISTS bot_event_counts (
                day DATE NOT NULL DEFAULT CURRENT_DATE, kind TEXT NOT NULL, branch TEXT NOT NULL,
                count BIGINT NOT NULL DEFAULT 1, PRIMARY KEY(day,kind,branch));
            CREATE TABLE IF NOT EXISTS evaluation_runs (
                id BIGSERIAL PRIMARY KEY, created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                revision INTEGER NOT NULL, results JSONB NOT NULL);
        ''')
        async with self.pool.acquire() as connection:
            async with connection.transaction():
                await connection.execute("SELECT pg_advisory_xact_lock(74218831)")
                if not await connection.fetchval("SELECT 1 FROM scenario_state WHERE id=1"):
                    from project.admin.seed import initial_config
                    raw = json.dumps(validate(initial_config()), ensure_ascii=False)
                    identifier = await connection.fetchval("INSERT INTO scenario_versions(config,note) VALUES($1::jsonb,$2) RETURNING id", raw, "Исходный сценарий прототипа")
                    await connection.execute("INSERT INTO scenario_state(id,draft,published_id) VALUES(1,$1::jsonb,$2)", raw, identifier)

    async def read(self, published=False):
        if published:
            row = await self.pool.fetchrow("SELECT v.id AS revision,v.config FROM scenario_state s JOIN scenario_versions v ON v.id=s.published_id WHERE s.id=1")
        else:
            row = await self.pool.fetchrow("SELECT revision,draft AS config,published_id FROM scenario_state WHERE id=1")
        config=json.loads(row["config"])
        config.setdefault("bindings",{k:k for k in ("work_age","career","work_search","reminders","reminder_tasks") if any(n["id"]==k for n in config["nodes"])})
        return {**dict(row), "config": config}

    async def save(self, config, revision):
        validate(config)
        result = await self.pool.fetchval("UPDATE scenario_state SET draft=$1::jsonb,revision=revision+1 WHERE id=1 AND revision=$2 RETURNING revision", json.dumps(config, ensure_ascii=False), revision)
        if result is None: raise ScenarioError("Черновик изменён в другой вкладке. Сначала обновите страницу.")
        return result

    async def publish(self, revision, note):
        async with self.pool.acquire() as c:
            async with c.transaction():
                row = await c.fetchrow("SELECT * FROM scenario_state WHERE id=1 FOR UPDATE")
                if row["revision"] != revision: raise ScenarioError("Черновик изменился. Обновите страницу перед публикацией.")
                validate(json.loads(row["draft"]))
                key = await c.fetchval("INSERT INTO scenario_versions(config,note) VALUES($1::jsonb,$2) RETURNING id", row["draft"], note[:300] or "Публикация из редактора")
                await c.execute("UPDATE scenario_state SET published_id=$1 WHERE id=1", key)
        return key

    async def history(self):
        rows = await self.pool.fetch("SELECT id,note,created_at FROM scenario_versions ORDER BY id DESC LIMIT 50")
        return [dict(r) for r in rows]

    async def restore(self, identifier, revision):
        raw = await self.pool.fetchval("SELECT config FROM scenario_versions WHERE id=$1", identifier)
        if raw is None: raise ScenarioError("Версия не найдена.")
        return await self.save(json.loads(raw), revision)

    async def event(self, kind, branch=""):
        # No user id, message, extracted fields or fine-grained timestamps.
        await self.pool.execute("INSERT INTO bot_event_counts(kind,branch) VALUES($1,$2) ON CONFLICT(day,kind,branch) DO UPDATE SET count=bot_event_counts.count+1", kind[:60], branch[:80])
