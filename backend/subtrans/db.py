"""Minimal async database adapter: one API over SQLite (local) and Postgres (Vercel / any server).

SQL is written once with `?` placeholders; the Postgres adapter rewrites them to `$1, $2, ...`.
Keep `?` out of string literals in queries passed here.
"""

from __future__ import annotations

import asyncio
import re
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, AsyncIterator, Iterable, Protocol, Sequence
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

import aiosqlite


class Conn(Protocol):
    dialect: str

    async def execute(self, sql: str, params: Sequence[Any] = ()) -> int: ...
    async def executemany(self, sql: str, rows: Iterable[Sequence[Any]]) -> None: ...
    async def fetchone(self, sql: str, params: Sequence[Any] = ()) -> dict[str, Any] | None: ...
    async def fetchall(self, sql: str, params: Sequence[Any] = ()) -> list[dict[str, Any]]: ...


class Database(Conn, Protocol):
    def transaction(self) -> Any: ...  # async context manager yielding a Conn
    async def close(self) -> None: ...


# ----------------------------------------------------------------------------- SQLite


class _SqliteConn:
    dialect = "sqlite"

    def __init__(self, db: aiosqlite.Connection):
        self._db = db

    async def execute(self, sql: str, params: Sequence[Any] = ()) -> int:
        cur = await self._db.execute(sql, tuple(params))
        return cur.rowcount

    async def executemany(self, sql: str, rows: Iterable[Sequence[Any]]) -> None:
        await self._db.executemany(sql, [tuple(r) for r in rows])

    async def fetchone(self, sql: str, params: Sequence[Any] = ()) -> dict[str, Any] | None:
        async with self._db.execute(sql, tuple(params)) as cur:
            row = await cur.fetchone()
        return dict(row) if row is not None else None

    async def fetchall(self, sql: str, params: Sequence[Any] = ()) -> list[dict[str, Any]]:
        async with self._db.execute(sql, tuple(params)) as cur:
            rows = await cur.fetchall()
        return [dict(r) for r in rows]


class SqliteDatabase:
    """One shared connection; every write (or transaction) is serialised and committed."""

    dialect = "sqlite"

    def __init__(self, path: str | Path):
        self.path = str(path)
        self._db: aiosqlite.Connection | None = None
        self._lock = asyncio.Lock()

    async def connect(self) -> None:
        if self.path != ":memory:":
            Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        self._db = await aiosqlite.connect(self.path)
        self._db.row_factory = aiosqlite.Row
        await self._db.execute("PRAGMA journal_mode=WAL")
        await self._db.execute("PRAGMA foreign_keys=ON")
        await self._db.commit()

    @property
    def _conn(self) -> _SqliteConn:
        if self._db is None:
            raise RuntimeError("Database not connected")
        return _SqliteConn(self._db)

    async def execute(self, sql: str, params: Sequence[Any] = ()) -> int:
        async with self._lock:
            n = await self._conn.execute(sql, params)
            await self._db.commit()  # type: ignore[union-attr]
            return n

    async def executemany(self, sql: str, rows: Iterable[Sequence[Any]]) -> None:
        async with self._lock:
            await self._conn.executemany(sql, rows)
            await self._db.commit()  # type: ignore[union-attr]

    async def fetchone(self, sql: str, params: Sequence[Any] = ()) -> dict[str, Any] | None:
        return await self._conn.fetchone(sql, params)

    async def fetchall(self, sql: str, params: Sequence[Any] = ()) -> list[dict[str, Any]]:
        return await self._conn.fetchall(sql, params)

    async def executescript(self, script: str) -> None:
        async with self._lock:
            await self._db.executescript(script)  # type: ignore[union-attr]
            await self._db.commit()  # type: ignore[union-attr]

    @asynccontextmanager
    async def transaction(self) -> AsyncIterator[_SqliteConn]:
        async with self._lock:
            assert self._db is not None
            await self._db.execute("BEGIN IMMEDIATE")
            try:
                yield self._conn
            except BaseException:
                await self._db.rollback()
                raise
            await self._db.commit()

    async def close(self) -> None:
        if self._db is not None:
            await self._db.close()
            self._db = None


# ----------------------------------------------------------------------------- Postgres

_PLACEHOLDER = re.compile(r"\?")


def _pg_sql(sql: str) -> str:
    counter = iter(range(1, 10_000))
    return _PLACEHOLDER.sub(lambda _m: f"${next(counter)}", sql)


def _rowcount(status: str) -> int:
    # asyncpg returns e.g. "UPDATE 3", "INSERT 0 1", "DELETE 0".
    tail = status.rsplit(" ", 1)[-1]
    return int(tail) if tail.isdigit() else 0


def clean_postgres_dsn(dsn: str) -> str:
    """Drop libpq-only query options (Neon adds channel_binding) that asyncpg rejects."""
    parts = urlsplit(dsn)
    query = [(k, v) for k, v in parse_qsl(parts.query) if k not in {"channel_binding", "options"}]
    scheme = "postgresql" if parts.scheme in {"postgres", "postgresql"} else parts.scheme
    return urlunsplit((scheme, parts.netloc, parts.path, urlencode(query), parts.fragment))


class _PgConn:
    dialect = "postgres"

    def __init__(self, conn: Any):
        self._conn = conn

    async def execute(self, sql: str, params: Sequence[Any] = ()) -> int:
        return _rowcount(await self._conn.execute(_pg_sql(sql), *params))

    async def executemany(self, sql: str, rows: Iterable[Sequence[Any]]) -> None:
        rows = [tuple(r) for r in rows]
        if rows:
            await self._conn.executemany(_pg_sql(sql), rows)

    async def fetchone(self, sql: str, params: Sequence[Any] = ()) -> dict[str, Any] | None:
        row = await self._conn.fetchrow(_pg_sql(sql), *params)
        return dict(row) if row is not None else None

    async def fetchall(self, sql: str, params: Sequence[Any] = ()) -> list[dict[str, Any]]:
        return [dict(r) for r in await self._conn.fetch(_pg_sql(sql), *params)]


class PostgresDatabase:
    dialect = "postgres"

    def __init__(self, dsn: str, *, max_size: int = 5):
        self.dsn = clean_postgres_dsn(dsn)
        self.max_size = max_size
        self._pool: Any = None

    async def connect(self) -> None:
        import asyncpg  # imported lazily so local SQLite installs don't need it

        # statement_cache_size=0: works behind PgBouncer-style poolers (Neon's pooled endpoint).
        self._pool = await asyncpg.create_pool(
            self.dsn, min_size=0, max_size=self.max_size, statement_cache_size=0, command_timeout=60
        )

    @asynccontextmanager
    async def _acquire(self) -> AsyncIterator[_PgConn]:
        if self._pool is None:
            raise RuntimeError("Database not connected")
        async with self._pool.acquire() as conn:
            yield _PgConn(conn)

    async def execute(self, sql: str, params: Sequence[Any] = ()) -> int:
        async with self._acquire() as c:
            return await c.execute(sql, params)

    async def executemany(self, sql: str, rows: Iterable[Sequence[Any]]) -> None:
        async with self.transaction() as c:
            await c.executemany(sql, rows)

    async def fetchone(self, sql: str, params: Sequence[Any] = ()) -> dict[str, Any] | None:
        async with self._acquire() as c:
            return await c.fetchone(sql, params)

    async def fetchall(self, sql: str, params: Sequence[Any] = ()) -> list[dict[str, Any]]:
        async with self._acquire() as c:
            return await c.fetchall(sql, params)

    async def executescript(self, script: str) -> None:
        async with self._acquire() as c:
            await c._conn.execute(script)  # simple-query protocol allows several statements

    @asynccontextmanager
    async def transaction(self) -> AsyncIterator[_PgConn]:
        if self._pool is None:
            raise RuntimeError("Database not connected")
        async with self._pool.acquire() as conn:
            async with conn.transaction():
                yield _PgConn(conn)

    async def close(self) -> None:
        if self._pool is not None:
            await self._pool.close()
            self._pool = None
