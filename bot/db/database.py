import aiosqlite
from aiosqlite import Row

from bot.utils.settings import settings

class Database:
    def __init__(self, path: str):
        self.path = path
        self.conn: aiosqlite.Connection | None = None

    async def connect(self):
        self.conn = await aiosqlite.connect(self.path)

        await self.execute("PRAGMA foreign_keys = ON;", auto_commit=False)
        await self.execute("PRAGMA journal_mode = WAL;", auto_commit=False)
        await self.execute("PRAGMA synchronous = NORMAL;", auto_commit=True)

    async def close(self):
        if self.conn:
            await self.conn.close()

    async def execute(self, query: str, params: tuple = (), auto_commit: bool = True) -> aiosqlite.Cursor:
        if not self.conn:
            raise Exception("Database is not connected")

        cursor = await self.conn.execute(query, params)

        if auto_commit:
            await self.commit()

        return cursor

    async def execute_fetchone(self, query: str, params: tuple = ()) -> Row | None:
        if not self.conn:
            raise Exception("Database is not connected")

        async with self.conn.execute(query, params) as cursor:
            return await cursor.fetchone()

    async def commit(self):
        if self.conn:
            await self.conn.commit()

database = Database(settings.sqlite_db_path)
