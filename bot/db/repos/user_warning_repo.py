from __future__ import annotations

from datetime import datetime, timedelta

from bot.db.database import Database, database
from bot.models.user_warning import UserWarning
from bot.utils.settings import settings


class UserWarningRepo:
    def __init__(self, database: Database):
        self.database = database

    async def init_schema(self) -> None:
        await self.database.execute(
            """
            CREATE TABLE IF NOT EXISTS warning (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                warning TEXT NOT NULL,
                updated_at INTEGER NOT NULL
            );
            """.strip(),
            auto_commit=False,
        )
        await self.database.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_warning_user_id_updated_at
            ON warning (user_id, updated_at DESC);
            """.strip(),
            auto_commit=False,
        )
        await self.database.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_warning_updated_at
            ON warning (updated_at DESC);
            """.strip(),
            auto_commit=False,
        )
        await self.database.commit()

    async def add(self, record: UserWarning) -> None:
        await self.database.execute(
            """
            INSERT INTO warning (user_id, warning, updated_at)
            VALUES (?, ?, ?);
            """.strip(),
            (
                int(record.user_id),
                str(record.warning),
                int(record.updated_at.timestamp()),
            ),
            auto_commit=True,
        )

    async def get_since(
        self,
        user_id: int,
        period_seconds: int,
        *,
        limit: int = 500,
        offset: int = 0,
    ) -> list[UserWarning]:
        cutoff = datetime.now(settings.bot_time_zone) - timedelta(seconds=int(period_seconds))
        cutoff_ts = int(cutoff.timestamp())

        cursor = await self.database.execute(
            """
            SELECT user_id, warning, updated_at
            FROM warning
            WHERE user_id = ?
              AND updated_at >= ?
            ORDER BY updated_at DESC, id DESC
            LIMIT ? OFFSET ?;
            """.strip(),
            (int(user_id), cutoff_ts, int(limit), int(offset)),
        )
        rows = await cursor.fetchall()
        return [
            UserWarning(
                user_id=row[0],
                warning=row[1],
                updated_at=datetime.fromtimestamp(row[2], tz=settings.bot_time_zone),
            )
            for row in rows
        ]

    async def purge_older_than(self, period_seconds: int) -> int:
        cutoff = datetime.now(settings.bot_time_zone) - timedelta(seconds=int(period_seconds))
        cutoff_ts = int(cutoff.timestamp())

        cursor = await self.database.execute(
            "DELETE FROM warning WHERE updated_at < ?;",
            (cutoff_ts,),
            auto_commit=True,
        )
        return int(getattr(cursor, "rowcount", 0) or 0)


user_warning_repo = UserWarningRepo(database=database)
