"""Persistence for jobs, subtitle blocks, run logs and login throttling (SQLite or Postgres).

Every job row carries a revision counter (`rev`). Each block write bumps it inside the same
transaction and stamps the written blocks with the new value, so pollers can ask for
"blocks changed since rev N" and never miss a write.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

from .db import Database, PostgresDatabase, SqliteDatabase
from .models import RUNNING_STATUSES, Bible, Block, JobMeta, JobSummary, LogEntry, Progress, RunConfig, UsageTotals

_SCHEMA = """
CREATE TABLE IF NOT EXISTS jobs (
    id TEXT PRIMARY KEY,
    filename TEXT NOT NULL,
    created_at {FLOAT} NOT NULL,
    updated_at {FLOAT} NOT NULL,
    status TEXT NOT NULL,
    target_language TEXT NOT NULL DEFAULT '',
    context_hints TEXT NOT NULL DEFAULT '',
    line_ending TEXT NOT NULL DEFAULT '{LF}',
    source_encoding TEXT NOT NULL DEFAULT 'utf-8',
    block_count INTEGER NOT NULL DEFAULT 0,
    run_config TEXT,
    bible TEXT,
    bible_approved INTEGER NOT NULL DEFAULT 0,
    progress TEXT NOT NULL,
    usage TEXT NOT NULL,
    error TEXT,
    rev BIGINT NOT NULL DEFAULT 0,
    run_id TEXT,
    pause_requested INTEGER NOT NULL DEFAULT 0,
    lease_owner TEXT,
    lease_until {FLOAT}
);
CREATE TABLE IF NOT EXISTS blocks (
    job_id TEXT NOT NULL REFERENCES jobs(id) ON DELETE CASCADE,
    pos INTEGER NOT NULL,
    id TEXT NOT NULL,
    idx INTEGER NOT NULL,
    timestamp TEXT NOT NULL,
    start_sec {FLOAT} NOT NULL,
    end_sec {FLOAT} NOT NULL,
    duration_sec {FLOAT} NOT NULL,
    source_text TEXT NOT NULL,
    draft_translation TEXT NOT NULL DEFAULT '',
    final_translation TEXT NOT NULL DEFAULT '',
    cps {FLOAT} NOT NULL DEFAULT 0,
    qa_fixed INTEGER NOT NULL DEFAULT 0,
    qa_reason TEXT NOT NULL DEFAULT '',
    flags TEXT NOT NULL DEFAULT '[]',
    user_edited INTEGER NOT NULL DEFAULT 0,
    rev BIGINT NOT NULL DEFAULT 0,
    PRIMARY KEY (job_id, id)
);
CREATE INDEX IF NOT EXISTS blocks_job_pos ON blocks(job_id, pos);
CREATE TABLE IF NOT EXISTS job_logs (
    id {SERIAL},
    job_id TEXT NOT NULL,
    ts {FLOAT} NOT NULL,
    level TEXT NOT NULL,
    message TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS job_logs_job ON job_logs(job_id, id);
CREATE TABLE IF NOT EXISTS login_attempts (
    key TEXT PRIMARY KEY,
    window_start {FLOAT} NOT NULL,
    failures INTEGER NOT NULL
);
"""

_DIALECT = {
    "sqlite": {"FLOAT": "REAL", "SERIAL": "INTEGER PRIMARY KEY AUTOINCREMENT", "LF": "\n"},
    "postgres": {"FLOAT": "DOUBLE PRECISION", "SERIAL": "BIGSERIAL PRIMARY KEY", "LF": "\n"},
}

# Columns added after the first release; older local databases get them via ALTER TABLE.
_MIGRATIONS = {
    "jobs": [
        ("rev", "BIGINT NOT NULL DEFAULT 0"),
        ("run_id", "TEXT"),
        ("pause_requested", "INTEGER NOT NULL DEFAULT 0"),
        ("lease_owner", "TEXT"),
        ("lease_until", "{FLOAT}"),
    ],
    "blocks": [("rev", "BIGINT NOT NULL DEFAULT 0")],
}

_META_COLUMNS = (
    "id, filename, created_at, updated_at, status, target_language, context_hints, line_ending, source_encoding, "
    "block_count, run_config, bible, bible_approved, progress, usage, error"
)
_BLOCK_COLUMNS = (
    "pos, id, idx, timestamp, start_sec, end_sec, duration_sec, source_text, draft_translation, "
    "final_translation, cps, qa_fixed, qa_reason, flags, user_edited"
)
MAX_LOGS_PER_JOB = 200


@dataclass
class RunControl:
    status: str
    run_id: str | None
    pause_requested: bool
    lease_owner: str | None
    lease_until: float | None
    updated_at: float

    def lease_active(self, now: float) -> bool:
        return self.lease_until is not None and self.lease_until > now


def _block_row(job_id: str, b: Block) -> tuple[Any, ...]:
    return (
        job_id,
        b.pos,
        b.id,
        b.index,
        b.timestamp,
        b.start_sec,
        b.end_sec,
        b.duration_sec,
        b.source_text,
        b.draft_translation,
        b.final_translation,
        b.cps,
        int(b.qa_fixed),
        b.qa_reason,
        json.dumps(b.flags),
        int(b.user_edited),
    )


def _row_to_block(row: dict[str, Any]) -> Block:
    return Block(
        id=row["id"],
        index=row["idx"],
        timestamp=row["timestamp"],
        start_sec=row["start_sec"],
        end_sec=row["end_sec"],
        duration_sec=row["duration_sec"],
        source_text=row["source_text"],
        draft_translation=row["draft_translation"],
        final_translation=row["final_translation"],
        cps=row["cps"],
        qa_fixed=bool(row["qa_fixed"]),
        qa_reason=row["qa_reason"],
        flags=json.loads(row["flags"]),
        user_edited=bool(row["user_edited"]),
    )


def _row_to_meta(row: dict[str, Any]) -> JobMeta:
    return JobMeta(
        id=row["id"],
        filename=row["filename"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
        status=row["status"],
        target_language=row["target_language"],
        context_hints=row["context_hints"],
        line_ending=row["line_ending"],
        source_encoding=row["source_encoding"],
        block_count=row["block_count"],
        run_config=RunConfig.model_validate_json(row["run_config"]) if row["run_config"] else None,
        bible=Bible.model_validate_json(row["bible"]) if row["bible"] else None,
        bible_approved=bool(row["bible_approved"]),
        progress=Progress.model_validate_json(row["progress"]),
        usage=UsageTotals.model_validate_json(row["usage"]),
        error=row["error"],
    )


def open_database(path_or_dsn: str | Path) -> Database:
    target = str(path_or_dsn)
    if target.startswith(("postgres://", "postgresql://")):
        return PostgresDatabase(target)
    return SqliteDatabase(target)


class JobStore:
    def __init__(self, path_or_dsn: str | Path | None = None, *, db: Database | None = None):
        self.db: Database = db if db is not None else open_database(path_or_dsn or ":memory:")

    @property
    def path(self) -> str:
        return getattr(self.db, "path", getattr(self.db, "dsn", ""))

    async def init(self) -> None:
        await self.db.connect()  # type: ignore[attr-defined]
        types = _DIALECT[self.db.dialect]
        await self.db.executescript(_SCHEMA.format(**types))  # type: ignore[attr-defined]
        for table, columns in _MIGRATIONS.items():
            existing = await self._columns(table)
            for name, decl in columns:
                if name not in existing:
                    await self.db.execute(f"ALTER TABLE {table} ADD COLUMN {name} {decl.format(**types)}")

    async def _columns(self, table: str) -> set[str]:
        if self.db.dialect == "sqlite":
            rows = await self.db.fetchall(f"PRAGMA table_info({table})")
            return {r["name"] for r in rows}
        rows = await self.db.fetchall("SELECT column_name FROM information_schema.columns WHERE table_name = ?", (table,))
        return {r["column_name"] for r in rows}

    async def close(self) -> None:
        await self.db.close()

    # ------------------------------------------------------------------ jobs
    @staticmethod
    def _meta_values(meta: JobMeta) -> tuple[Any, ...]:
        return (
            meta.id,
            meta.filename,
            meta.created_at,
            meta.updated_at,
            meta.status,
            meta.target_language,
            meta.context_hints,
            meta.line_ending,
            meta.source_encoding,
            meta.block_count,
            meta.run_config.model_dump_json() if meta.run_config else None,
            meta.bible.model_dump_json() if meta.bible else None,
            int(meta.bible_approved),
            meta.progress.model_dump_json(),
            meta.usage.model_dump_json(),
            meta.error,
        )

    async def create_job(self, meta: JobMeta, blocks: list[Block]) -> None:
        async with self.db.transaction() as c:
            await c.execute(
                f"INSERT INTO jobs ({_META_COLUMNS}) VALUES ({', '.join('?' * 16)})",
                self._meta_values(meta),
            )
            await c.executemany(
                f"INSERT INTO blocks (job_id, {_BLOCK_COLUMNS}) VALUES ({', '.join('?' * 16)})",
                [_block_row(meta.id, b) for b in blocks],
            )

    async def save_meta(self, meta: JobMeta) -> None:
        values = self._meta_values(meta)
        await self.db.execute(
            "UPDATE jobs SET filename=?, created_at=?, updated_at=?, status=?, target_language=?, "
            "context_hints=?, line_ending=?, source_encoding=?, block_count=?, run_config=?, bible=?, "
            "bible_approved=?, progress=?, usage=?, error=? WHERE id=?",
            (*values[1:], values[0]),
        )

    async def save_blocks(self, job_id: str, blocks: Iterable[Block], *, keep_user_edits: bool = False) -> None:
        """Persist translation fields. keep_user_edits=True is for pipeline writes: a line a person
        edited (possibly from another server instance mid-run) keeps its text, CPS and QA state."""
        blocks = list(blocks)
        if not blocks:
            return
        async with self.db.transaction() as c:
            row = await c.fetchone("UPDATE jobs SET rev = rev + 1 WHERE id=? RETURNING rev", (job_id,))
            if row is None:
                return  # job deleted mid-run
            rev = row["rev"]
            if keep_user_edits:
                sql = (
                    "UPDATE blocks SET draft_translation=?, "
                    "final_translation=CASE WHEN user_edited=1 THEN final_translation ELSE ? END, "
                    "cps=CASE WHEN user_edited=1 THEN cps ELSE ? END, "
                    "qa_fixed=CASE WHEN user_edited=1 THEN qa_fixed ELSE ? END, "
                    "qa_reason=CASE WHEN user_edited=1 THEN qa_reason ELSE ? END, "
                    "flags=?, rev=? WHERE job_id=? AND id=?"
                )
                rows = [
                    (b.draft_translation, b.final_translation, b.cps, int(b.qa_fixed), b.qa_reason, json.dumps(b.flags), rev, job_id, b.id)
                    for b in blocks
                ]
            else:
                sql = (
                    "UPDATE blocks SET draft_translation=?, final_translation=?, cps=?, qa_fixed=?, qa_reason=?, "
                    "flags=?, user_edited=?, rev=? WHERE job_id=? AND id=?"
                )
                rows = [
                    (
                        b.draft_translation,
                        b.final_translation,
                        b.cps,
                        int(b.qa_fixed),
                        b.qa_reason,
                        json.dumps(b.flags),
                        int(b.user_edited),
                        rev,
                        job_id,
                        b.id,
                    )
                    for b in blocks
                ]
            await c.executemany(sql, rows)

    async def get_meta(self, job_id: str) -> JobMeta | None:
        row = await self.db.fetchone(f"SELECT {_META_COLUMNS} FROM jobs WHERE id=?", (job_id,))
        return _row_to_meta(row) if row else None

    async def get_rev(self, job_id: str) -> int:
        row = await self.db.fetchone("SELECT rev FROM jobs WHERE id=?", (job_id,))
        return int(row["rev"]) if row else 0

    async def get_blocks(self, job_id: str) -> list[Block]:
        rows = await self.db.fetchall(f"SELECT {_BLOCK_COLUMNS} FROM blocks WHERE job_id=? ORDER BY pos", (job_id,))
        return [_row_to_block(r) for r in rows]

    async def get_blocks_since(self, job_id: str, rev: int) -> list[Block]:
        rows = await self.db.fetchall(
            f"SELECT {_BLOCK_COLUMNS} FROM blocks WHERE job_id=? AND rev > ? ORDER BY pos", (job_id, rev)
        )
        return [_row_to_block(r) for r in rows]

    async def get_block(self, job_id: str, block_id: str) -> Block | None:
        row = await self.db.fetchone(f"SELECT {_BLOCK_COLUMNS} FROM blocks WHERE job_id=? AND id=?", (job_id, block_id))
        return _row_to_block(row) if row else None

    async def list_jobs(self) -> list[JobSummary]:
        rows = await self.db.fetchall(f"SELECT {_META_COLUMNS} FROM jobs ORDER BY updated_at DESC")
        return [JobSummary.from_meta(_row_to_meta(r)) for r in rows]

    async def delete_job(self, job_id: str) -> None:
        async with self.db.transaction() as c:
            await c.execute("DELETE FROM job_logs WHERE job_id=?", (job_id,))
            await c.execute("DELETE FROM blocks WHERE job_id=?", (job_id,))
            await c.execute("DELETE FROM jobs WHERE id=?", (job_id,))

    async def mark_interrupted(self) -> int:
        """Local server startup: jobs left mid-run by a crash/restart become resumable."""
        placeholders = ", ".join("?" * len(RUNNING_STATUSES))
        return await self.db.execute(
            f"UPDATE jobs SET status='interrupted', run_id=NULL, lease_owner=NULL, lease_until=NULL "
            f"WHERE status IN ({placeholders})",
            tuple(RUNNING_STATUSES),
        )

    # ------------------------------------------------------------------ run control
    async def get_control(self, job_id: str) -> RunControl | None:
        row = await self.db.fetchone(
            "SELECT status, run_id, pause_requested, lease_owner, lease_until, updated_at FROM jobs WHERE id=?", (job_id,)
        )
        if row is None:
            return None
        return RunControl(
            status=row["status"],
            run_id=row["run_id"],
            pause_requested=bool(row["pause_requested"]),
            lease_owner=row["lease_owner"],
            lease_until=row["lease_until"],
            updated_at=row["updated_at"],
        )

    async def set_run(self, job_id: str, run_id: str | None) -> None:
        await self.db.execute("UPDATE jobs SET run_id=?, pause_requested=0 WHERE id=?", (run_id, job_id))

    async def request_pause(self, job_id: str) -> None:
        await self.db.execute("UPDATE jobs SET pause_requested=1 WHERE id=?", (job_id,))

    async def acquire_lease(self, job_id: str, owner: str, ttl: float, now: float | None = None) -> bool:
        now = time.time() if now is None else now
        n = await self.db.execute(
            "UPDATE jobs SET lease_owner=?, lease_until=? WHERE id=? AND (lease_until IS NULL OR lease_until < ? OR lease_owner=?)",
            (owner, now + ttl, job_id, now, owner),
        )
        return n == 1

    async def release_lease(self, job_id: str, owner: str) -> None:
        await self.db.execute(
            "UPDATE jobs SET lease_owner=NULL, lease_until=NULL WHERE id=? AND lease_owner=?", (job_id, owner)
        )

    # ------------------------------------------------------------------ logs
    async def append_logs(self, job_id: str, entries: list[tuple[float, str, str]]) -> None:
        if not entries:
            return
        await self.db.executemany(
            "INSERT INTO job_logs (job_id, ts, level, message) VALUES (?, ?, ?, ?)",
            [(job_id, ts, level, message) for ts, level, message in entries],
        )
        await self.db.execute(
            "DELETE FROM job_logs WHERE job_id=? AND id < "
            "(SELECT id FROM job_logs WHERE job_id=? ORDER BY id DESC LIMIT 1 OFFSET ?)",
            (job_id, job_id, MAX_LOGS_PER_JOB),
        )

    async def get_logs(self, job_id: str, after_id: int = 0, limit: int = 80) -> list[LogEntry]:
        rows = await self.db.fetchall(
            "SELECT id, ts, level, message FROM job_logs WHERE job_id=? AND id > ? ORDER BY id DESC LIMIT ?",
            (job_id, after_id, limit),
        )
        return [LogEntry(id=r["id"], ts=r["ts"], level=r["level"], message=r["message"]) for r in reversed(rows)]

    # ------------------------------------------------------------------ login throttling
    async def login_failures(self, key: str, window: float, now: float) -> int:
        row = await self.db.fetchone("SELECT window_start, failures FROM login_attempts WHERE key=?", (key,))
        if row is None or row["window_start"] < now - window:
            return 0
        return int(row["failures"])

    async def record_login_failure(self, key: str, window: float, now: float) -> None:
        async with self.db.transaction() as c:
            row = await c.fetchone("SELECT window_start FROM login_attempts WHERE key=?", (key,))
            if row is None:
                await c.execute("INSERT INTO login_attempts (key, window_start, failures) VALUES (?, ?, 1)", (key, now))
            elif row["window_start"] < now - window:
                await c.execute("UPDATE login_attempts SET window_start=?, failures=1 WHERE key=?", (now, key))
            else:
                await c.execute("UPDATE login_attempts SET failures=failures+1 WHERE key=?", (key,))

    async def clear_login_failures(self, key: str) -> None:
        await self.db.execute("DELETE FROM login_attempts WHERE key=?", (key,))

