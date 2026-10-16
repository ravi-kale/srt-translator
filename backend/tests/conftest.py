"""Shared fixtures: a deterministic fake LLM client injected through the client factory."""

from __future__ import annotations

import asyncio
import json
import os
import re
from dataclasses import dataclass, field
from typing import Any

import pytest

from subtrans.llm.base import LLMError, LLMRefusalError, LLMResult, Usage
from subtrans.models import Block, RunConfig
from subtrans.pipeline.orchestrator import JobManager
from subtrans.srt_parser import ParsedSrt, parse_srt
from subtrans.store import JobStore

_BATCH_RE = re.compile(r"TARGET BATCH TO TRANSLATE:\n(.*?)\n\nUPCOMING CONTEXT", re.DOTALL)
_QA_LINE_RE = re.compile(r"^(\d+) \| [\d.]+s \| (.*?) \| SRC: (.*) \| TRG: (.*)$", re.MULTILINE)


@dataclass
class Call:
    stage: int
    system: str
    user: str
    ids: list[str] = field(default_factory=list)


@dataclass
class FakeLLMClient:
    stage: int
    provider: str = "fake"
    model: str = "deepseek-flash"
    structured: bool = False
    calls: list[Call] = field(default_factory=list)
    delay: float = 0.0
    # Stage 2 knobs
    drop_ids_once: set[str] = field(default_factory=set)
    drop_ids_always: set[str] = field(default_factory=set)
    # Stage 3 knobs
    fix_ids: set[str] = field(default_factory=set)
    refuse: bool = False
    fatal_error: str | None = None
    bible: dict[str, Any] = field(
        default_factory=lambda: {
            "synopsis_and_tone": "Light comedy; casual register.",
            "speaker_pronoun_rules": [{"speakers": "Ana -> Ben", "rule": "Informal tú."}],
            "glossary": [{"source_term": "Ben", "target_term": "Ben", "notes": "name"}],
        }
    )

    async def complete_json(self, *, system: str, user: str, schema: Any, schema_name: str, max_tokens: int) -> LLMResult:
        if self.delay:
            await asyncio.sleep(self.delay)
        usage = Usage(input_tokens=1000, cache_hit_tokens=600, output_tokens=200, cost_usd=0.001)
        if self.fatal_error:
            raise LLMError(self.fatal_error, retryable=False, fatal=True)
        if self.stage == 1:
            self.calls.append(Call(1, system, user))
            return LLMResult(data=self.bible, usage=usage, model=self.model)
        if self.stage == 2:
            match = _BATCH_RE.search(user)
            assert match, "stage 2 user message must contain the target batch"
            batch = json.loads(match.group(1))
            ids = list(batch)
            self.calls.append(Call(2, system, user, ids))
            translations = {}
            for bid, item in batch.items():
                if bid in self.drop_ids_always:
                    continue
                if bid in self.drop_ids_once:
                    self.drop_ids_once.discard(bid)
                    continue
                translations[bid] = f"[ES] {item['text']}"
            return LLMResult(data={"translations": translations}, usage=usage, model=self.model)
        # stage 3
        rows = _QA_LINE_RE.findall(user)
        ids = [r[0] for r in rows]
        self.calls.append(Call(3, system, user, ids))
        if self.refuse:
            raise LLMRefusalError("declined", usage=usage)
        fixes = {
            rid: {"corrected_text": f"{json.loads(trg)} (fixed)" if trg != '""' else "relleno", "reason": f"Fixed #{rid}"}
            for rid, flags, _src, trg in rows
            if rid in self.fix_ids
        }
        return LLMResult(data={"fixes": fixes}, usage=usage, model=self.model)


class FakeFactory:
    def __init__(self, clients: dict[int, FakeLLMClient] | None = None, missing: list[str] | None = None):
        self.clients = clients or {s: FakeLLMClient(stage=s) for s in (1, 2, 3)}
        self.missing = missing or []

    def missing_keys(self, config: RunConfig, stages: set[int] | None = None) -> list[str]:
        return list(self.missing)

    def key_status(self) -> dict[str, bool]:
        return {"deepseek": True, "claude": True, "gemini": True, "openrouter": False}

    def build(self, stage: int, config: RunConfig) -> FakeLLMClient:
        return self.clients[stage]


def make_srt(n: int, *, terminal_every: int = 1, gap_after: set[int] | None = None, line_ending: str = "\n") -> str:
    """n cues of 2 s each; every `terminal_every`-th line ends with a period."""
    gap_after = gap_after or set()
    out = []
    t = 0.0
    for i in range(1, n + 1):
        start, end = t, t + 2.0
        punct = "." if i % terminal_every == 0 else ","
        out.append(f"{i}{line_ending}{_ts(start)} --> {_ts(end)}{line_ending}Line number {i}{punct}")
        t = end + (2.0 if i in gap_after else 0.2)
    return (line_ending * 2).join(out) + line_ending


def _ts(sec: float) -> str:
    ms = int(round(sec * 1000))
    h, ms = divmod(ms, 3_600_000)
    m, ms = divmod(ms, 60_000)
    s, ms = divmod(ms, 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def blocks_from(text: str) -> list[Block]:
    return parse_srt(text).blocks


@pytest.fixture(autouse=True)
def isolated_settings(monkeypatch):
    """Tests never read real secrets, databases or Vercel settings from backend/.env."""
    for name in ("APP_PASSWORD", "SUBTRANS_SESSION_SECRET", "DATABASE_URL", "POSTGRES_URL", "VERCEL", "SUBTRANS_RUNNER"):
        monkeypatch.setenv(name, "")


_PG_URL = os.environ.get("TEST_DATABASE_URL", "")


@pytest.fixture(params=["sqlite", "postgres"] if _PG_URL else ["sqlite"])
async def store(request, tmp_path):
    """Every store-backed test runs on SQLite, and also on Postgres when TEST_DATABASE_URL is set."""
    if request.param == "postgres":
        s = JobStore(_PG_URL)
        await s.db.connect()  # type: ignore[attr-defined]
        await s.db.executescript("DROP TABLE IF EXISTS blocks, jobs, job_logs, login_attempts CASCADE")  # type: ignore[attr-defined]
        await s.db.close()
        s = JobStore(_PG_URL)
    else:
        s = JobStore(tmp_path / "test.db")
    await s.init()
    yield s
    await s.close()


@pytest.fixture
def factory() -> FakeFactory:
    return FakeFactory()


@pytest.fixture
async def manager(store, factory):
    mgr = JobManager(store, factory, watch_interval=0.02)  # type: ignore[arg-type]
    yield mgr
    await mgr.shutdown()


async def wait_done(manager: JobManager, job_id: str, timeout: float = 10.0):
    """Wait for the local runner to finish the job's run, then return a fresh copy from the DB."""
    await manager.runner.wait(job_id, timeout)  # type: ignore[attr-defined]
    return await manager.get(job_id)


def parsed(n: int, **kw: Any) -> ParsedSrt:
    return parse_srt(make_srt(n, **kw))
