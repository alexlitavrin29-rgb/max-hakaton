import asyncio

from project.llm import bot
from project.llm.services import llm
from project.llm.integrations import reefapi, trudvsem, headhunter


def test_network_shutdown_closes_each_managed_service_once(monkeypatch):
    calls=[]
    async def mark(name):calls.append(name)
    monkeypatch.setattr(llm,'close',lambda:mark('llm'))
    monkeypatch.setattr(reefapi,'close',lambda:mark('reef'))
    monkeypatch.setattr(trudvsem,'close',lambda:mark('work'))
    monkeypatch.setattr(headhunter,'close',lambda:mark('hh'))
    asyncio.run(bot.close_network_clients())
    assert calls==['llm','reef','work','hh']
