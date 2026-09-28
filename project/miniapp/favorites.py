"""Saved result snapshots, scoped to the verified MAX identity."""
import hashlib
import json


def card_key(section, card):
    links = [a['url'] for a in card.get('actions', []) if a.get('url')]
    # One organisation can have several help addresses sharing the same website.
    source = links[0] if links and section != 'help' else card['title'] + '\n' + card['text']
    return hashlib.sha256((section + '\n' + source).encode()).hexdigest()


class Favorites:
    def __init__(self, pool):
        self.pool = pool

    async def initialize(self):
        await self.pool.execute('''CREATE TABLE IF NOT EXISTS miniapp_favorites (
            owner TEXT NOT NULL, card_id TEXT NOT NULL, card JSONB NOT NULL,
            saved_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            PRIMARY KEY (owner, card_id))''')

    async def list(self, owner):
        rows = await self.pool.fetch('''SELECT card_id, card, saved_at FROM miniapp_favorites
            WHERE owner=$1 ORDER BY saved_at DESC, card_id''', str(owner))
        return [dict(json.loads(row['card']), favorite_id=row['card_id'],
                     saved_at=row['saved_at'].isoformat()) for row in rows]

    async def save(self, owner, card_id, card, connection=None):
        await (connection or self.pool).execute('''INSERT INTO miniapp_favorites (owner, card_id, card)
            VALUES ($1, $2, $3::jsonb) ON CONFLICT (owner, card_id)
            DO UPDATE SET card=EXCLUDED.card''', str(owner), card_id,
            json.dumps(card, ensure_ascii=False))

    async def remove(self, owner, card_id, connection=None):
        await (connection or self.pool).execute('DELETE FROM miniapp_favorites WHERE owner=$1 AND card_id=$2',
                                str(owner), card_id)

    async def delete_account(self, owner, connection=None):
        if connection is None:
            async with self.pool.acquire() as connection:
                async with connection.transaction():
                    await self.delete_account(owner, connection)
            return
        await connection.execute('DELETE FROM miniapp_favorites WHERE owner=$1', str(owner))
        if str(owner).isdigit():
            await connection.execute('DELETE FROM reminders WHERE user_id=$1', int(owner))
