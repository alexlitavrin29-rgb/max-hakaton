"""Generation captured when a bot action starts, for deletion races."""

from contextvars import ContextVar

EXPECTED_EPOCH = ContextVar('expected_account_epoch', default=None)


async def read_epoch(pool, owner):
    return await pool.fetchval('SELECT epoch FROM account_epochs WHERE owner=$1', str(owner)) or 0
