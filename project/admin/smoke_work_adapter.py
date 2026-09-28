"""Run inside the admin image, which supplies FastAPI and asyncpg."""
import asyncio
from types import SimpleNamespace
from unittest.mock import patch

from project.admin import app as admin
from project.admin.seed import initial_config
from project.admin.work_draft import configure_work


async def main():
    class Store:
        async def read(self): return dict(config=configure_work(initial_config()),revision=9)
    admin.app.state.store=Store()
    async def search(query,**kwargs):
        assert query is None
        assert kwargs["region_code"]=="7000000000000"
        return SimpleNamespace(items=[],next_offset=None)
    with patch("project.llm.services.flow.search_vacancies",search):
        result=await admin.search_test(admin.SearchTest(city="Томск",region_code="9900000000000"))
    assert any(t["kind"]=="search" for t in result["trace"])
    print("PASS: admin search supports no profession/age and ignores an unverified region code")


if __name__=="__main__": asyncio.run(main())
