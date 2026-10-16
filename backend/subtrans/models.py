"""Pydantic v2 domain models shared by the parser, pipeline, store and API."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field, model_validator

# ---------------------------------------------------------------------------
# Subtitle blocks
# ---------------------------------------------------------------------------

FLAG_HIGH_CPS = "HIGH_CPS"
FLAG_EMPTY = "EMPTY"
FLAG_IDENTICAL = "IDENTICAL"
FLAG_MISSING = "MISSING"


class Block(BaseModel):
    # `id` is the 1-based position as a string: always unique, the only key the LLM ever sees.
    id: str
    # `index` is the counter written in the original file (preserved on export).
    index: int
    # The original timing line, verbatim. Never sent to an LLM.
    timestamp: str
    start_sec: float
    end_sec: float
    duration_sec: float
    source_text: str
    draft_translation: str = ""
    final_translation: str = ""
    cps: float = 0.0
    qa_fixed: bool = False
    qa_reason: str = ""
    flags: list[str] = Field(default_factory=list)
    user_edited: bool = False

    @property
    def pos(self) -> int:
        return int(self.id) - 1

    @property
    def current_text(self) -> str:
        return self.final_translation or self.draft_translation


# ---------------------------------------------------------------------------
# Translation Bible
# ---------------------------------------------------------------------------


class GlossaryEntry(BaseModel):
    source_term: str
    target_term: str
    notes: str = ""


class PronounRule(BaseModel):
    speakers: str
    rule: str


class Bible(BaseModel):
    synopsis_and_tone: str = ""
    speaker_pronoun_rules: list[PronounRule] = Field(default_factory=list)
    glossary: list[GlossaryEntry] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Run configuration (never contains secrets; keys live in backend/.env)
# ---------------------------------------------------------------------------

PipelineMode = Literal["hybrid", "all_deepseek", "all_openrouter"]
Stage1Provider = Literal["gemini", "deepseek"]
Stage3Provider = Literal["claude", "gemini"]
Effort = Literal["low", "medium", "high"]


class RunConfig(BaseModel):
    mode: PipelineMode = "all_deepseek"
    stage1_provider: Stage1Provider = "gemini"
    stage3_provider: Stage3Provider = "claude"

    deepseek_model: str = "deepseek-flash"
    gemini_model: str = "gemini-4-argon"
    claude_model: str = "claude-sonnet-5-5"
    openrouter_stage1_model: str = "google/gemini-4-argon"
    openrouter_stage2_model: str = "deepseek/deepseek-v4.1-flash"
    openrouter_stage3_model: str = "anthropic/claude-sonnet-5.5"

    # DeepSeek thinking-off flags: official API flag + vLLM/SGLang chat-template flag.
    deepseek_send_thinking_flag: bool = True
    deepseek_send_chat_template_flag: bool = True
    stage2_temperature: float = Field(0.3, ge=0.0, le=2.0)

    stage1_reasoning_effort: Literal["minimal", "low", "medium", "high"] = "low"
    qa_effort: Effort = "medium"
    # Stage 3 is optional: off, a run completes right after Stage 2 (drafts become the final text).
    qa_enabled: bool = True
    claude_refusal_fallback: bool = True

    lanes: int = Field(4, ge=1, le=16)
    target_size: int = Field(30, ge=5, le=100)
    max_size: int = Field(42, ge=5, le=150)
    retry_chunk_size: int = Field(15, ge=3, le=60)
    gap_sec: float = Field(1.5, ge=0.0, le=30.0)
    context_window: int = Field(6, ge=0, le=30)
    bible_max_lines: int = Field(1200, ge=50, le=5000)

    cps_threshold: float = Field(20.0, ge=5.0, le=60.0)
    qa_window: int = Field(120, ge=10, le=400)
    qa_concurrency: int = Field(3, ge=1, le=10)

    pause_after_bible: bool = False

    @model_validator(mode="after")
    def _sizes(self) -> "RunConfig":
        if self.max_size < self.target_size:
            raise ValueError("max_size must be >= target_size")
        return self


# ---------------------------------------------------------------------------
# Job state
# ---------------------------------------------------------------------------

JobStatus = Literal[
    "ready",
    "bible_running",
    "awaiting_review",
    "translating",
    "qa_running",
    "completed",
    "paused",
    "interrupted",
    "failed",
]
RUNNING_STATUSES: frozenset[str] = frozenset({"bible_running", "translating", "qa_running"})


class Progress(BaseModel):
    stage: int = 0  # 0 = not started, 1..3 = active/last stage, 4 = done
    batches_total: int = 0
    batches_done: int = 0
    qa_windows_total: int = 0
    qa_windows_done: list[int] = Field(default_factory=list)
    qa_window_size: int = 0
    qa_fixes: int = 0
    last_message: str = ""
    started_at: float | None = None
    finished_at: float | None = None


class StageUsage(BaseModel):
    calls: int = 0
    input_tokens: int = 0  # total prompt tokens, including cached
    cache_hit_tokens: int = 0
    cache_write_tokens: int = 0
    output_tokens: int = 0
    cost_usd: float = 0.0


class UsageTotals(BaseModel):
    stage1: StageUsage = Field(default_factory=StageUsage)
    stage2: StageUsage = Field(default_factory=StageUsage)
    stage3: StageUsage = Field(default_factory=StageUsage)

    @property
    def total_cost_usd(self) -> float:
        return round(self.stage1.cost_usd + self.stage2.cost_usd + self.stage3.cost_usd, 6)

    def stage(self, n: int) -> StageUsage:
        return {1: self.stage1, 2: self.stage2, 3: self.stage3}[n]


class JobMeta(BaseModel):
    id: str
    filename: str
    created_at: float
    updated_at: float
    status: JobStatus = "ready"
    target_language: str = ""
    context_hints: str = ""
    line_ending: str = "\n"
    source_encoding: str = "utf-8"
    block_count: int = 0
    run_config: RunConfig | None = None
    bible: Bible | None = None
    bible_approved: bool = False
    progress: Progress = Field(default_factory=Progress)
    usage: UsageTotals = Field(default_factory=UsageTotals)
    error: str | None = None


class JobSummary(BaseModel):
    id: str
    filename: str
    created_at: float
    updated_at: float
    status: JobStatus
    target_language: str
    context_hints: str
    block_count: int
    progress: Progress
    total_cost_usd: float
    error: str | None
    has_bible: bool
    bible_approved: bool

    @classmethod
    def from_meta(cls, meta: JobMeta) -> "JobSummary":
        return cls(
            id=meta.id,
            filename=meta.filename,
            created_at=meta.created_at,
            updated_at=meta.updated_at,
            status=meta.status,
            target_language=meta.target_language,
            context_hints=meta.context_hints,
            block_count=meta.block_count,
            progress=meta.progress,
            total_cost_usd=meta.usage.total_cost_usd,
            error=meta.error,
            has_bible=meta.bible is not None,
            bible_approved=meta.bible_approved,
        )


class LogEntry(BaseModel):
    id: int = 0
    message: str
    level: str = "info"
    ts: float


class JobDetail(BaseModel):
    job: JobSummary
    run_config: RunConfig | None
    bible: Bible | None
    usage: UsageTotals
    total_cost_usd: float
    blocks: list[Block]
    # Polling cursors: pass them back to GET /api/jobs/{id}/changes.
    rev: int = 0
    logs: list[LogEntry] = Field(default_factory=list)


class JobChanges(BaseModel):
    """Incremental poll result: the small job state in full, plus blocks/logs changed since the cursors."""

    job: JobSummary
    run_config: RunConfig | None
    bible: Bible | None
    usage: UsageTotals
    total_cost_usd: float
    blocks: list[Block]
    rev: int
    logs: list[LogEntry]


# ---------------------------------------------------------------------------
# API request bodies
# ---------------------------------------------------------------------------


class RunRequest(BaseModel):
    config: RunConfig = Field(default_factory=RunConfig)
    target_language: str | None = None
    context_hints: str | None = None


class RerunRequest(RunRequest):
    stage: Literal[2, 3]


class BibleUpdate(BaseModel):
    bible: Bible
    approve: bool = False


class BlockEdit(BaseModel):
    text: str = Field(max_length=2000)
