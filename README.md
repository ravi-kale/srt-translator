# SubTrans Studio

An `.srt` subtitle localization and QA studio built on a 3-stage hybrid LLM pipeline ("Combo B"):

| Stage | Default model | What it does |
|---|---|---|
| 1 · Translation Bible | Gemini 4 Argon (`reasoning_effort="low"`) or DeepSeek V4.1 Flash | Reads up to 1,200 lines (IDs + text only) and extracts the synopsis and tone, speaker/pronoun formality rules and a glossary. You can stop here to review and edit it. |
| 2 · Sliding-window translation | DeepSeek V4.1 Flash (`deepseek-flash`) | Scene-aware batches of about 30 lines (max 42), with 6 lines of read-only context before and after. A strict 1-to-1 ID lock retries a batch in chunks of 15 if any ID is missing. |
| 3 · Hybrid QA critic | Claude Sonnet 5.5 or Gemini 4 Argon | A deterministic CPS/empty/identical pre-check, then 120-line windows that come back as a **sparse patch** of fixes. Each fix records a reason and keeps the original draft for the diff view. |

Timestamps never leave the backend. The LLMs see only IDs, durations and text, and export writes the original timing lines back byte-for-byte.

## Quick start

```bash
cd backend
python3.11 -m venv .venv
.venv/bin/pip install -r requirements.txt
cp .env.example .env        # then add your keys
.venv/bin/uvicorn main:app --port 8000
```

```bash
cd frontend
npm install
npm run dev                 # http://localhost:5173 (proxies /api to :8000)
```

For a production build, run `npm run build` in `frontend/`. The backend then serves `frontend/dist` at `http://localhost:8000`.

### API keys

Keys are read **only** from `backend/.env` (or the process environment):

| Variable | Needed for |
|---|---|
| `DEEPSEEK_API_KEY` | Stage 2 (and every stage in "All DeepSeek" mode) |
| `ANTHROPIC_API_KEY` | Stage 3 when the critic is Claude Sonnet 5.5 |
| `GEMINI_API_KEY` | Stage 1 and/or Stage 3 when Gemini 4 Argon is selected |
| `OPENROUTER_API_KEY` | "All OpenRouter" mode |

Keys never pass through the browser. The Settings modal only shows which ones exist, and **Re-check .env** picks up edits without a restart. Provider base URLs are also server-side only (`DEEPSEEK_BASE_URL`, `GEMINI_BASE_URL`, `OPENROUTER_BASE_URL`), so a browser client can't send your keys to a different host.

### Try it without keys (mock LLMs)

`backend/tests/mock_llm_server.py` is an OpenAI-compatible mock. It produces pseudo-translations, sometimes drops an ID (to exercise the retry), makes some lines too long to read in time (HIGH CPS) and returns critic patches:

```bash
backend/.venv/bin/python backend/tests/mock_llm_server.py      # :8099
env DEEPSEEK_API_KEY=mock GEMINI_API_KEY=mock \
    DEEPSEEK_BASE_URL=http://127.0.0.1:8099/v1 GEMINI_BASE_URL=http://127.0.0.1:8099/v1 \
    SUBTRANS_DB_PATH=backend/data/mock.db \
    backend/.venv/bin/uvicorn main:app --app-dir backend --port 8000
```

In Settings, set Stage 3 to **Gemini 4 Argon** (there is no Claude mock). `.claude/launch.json` has `mock-llm`, `backend-mock`, `backend` and `frontend` entries. `backend/tests/fixtures/` has a 40-line sample plus `make_large_srt.py`, which generates a file of 3,000+ lines.

## API notes and choices

- **DeepSeek thinking off.** Stage 2 sends `extra_body={"thinking": {"type": "disabled"}, "chat_template_kwargs": {"enable_thinking": False}}` together with `response_format={"type": "json_object"}` and `temperature=0.3`. The first flag is the official api.deepseek.com switch. The second is the vLLM/SGLang flag from the original spec. You can turn either off in Settings, for example if an endpoint rejects it.
- **Prompt caching.** Each job builds the system prompt (rules plus the deterministically rendered Bible) once, so it is byte-identical for every batch and DeepSeek's prefix cache hits. Claude gets the same prefix with `cache_control`.
- **Claude critic.** Uses `client.beta.messages.create` with `output_config={"effort": …, "format": {"type": "json_schema", …}}`. Sonnet 5.5 rejects forced tool use and non-default `temperature`, so neither is sent. The server-side refusal fallback (`fallbacks="default"`, beta `server-side-fallback-2026-07-01`) is on by default and can be turned off in Settings.
- **Patch shape.** JSON-schema providers (Claude, Gemini) return `{"fixes": [{id, corrected_text, reason}]}`, because a strict schema can't express arbitrary map keys. `json_object` providers return the spec's `{"fixes": {"<id>": {…}}}`. The backend accepts both.
- **Parallel lanes.** Stage 2 splits the file into N contiguous lanes (default 4; 1 = strictly sequential). Inside a lane, batches run in order, so every batch except the first one in a lane sees the previous translated drafts.
- **Persistence.** SQLite (WAL) in `backend/data/subtrans.db`. Each batch and QA window is committed as it finishes. On startup, jobs that were mid-run become `interrupted`, and Resume continues with only the unfinished batches and windows.
- **CPS.** `visible characters / duration_sec`, where formatting tags and line breaks are not counted. The UI recomputes it live while you type, using the same formula.
- **Pricing.** `backend/subtrans/pricing.py` holds the list prices checked on 2026-10-16. That includes DeepSeek's peak-hour 2× rate and the Gemini 4 Argon introductory price through 2026-11-30. With OpenRouter, the cost OpenRouter reports is used.

## Layout

```
backend/
  main.py                    FastAPI app: REST + SSE, startup recovery, serves frontend/dist
  subtrans/
    srt_parser.py            decode (UTF-8/BOM/UTF-16/cp1252), parse, build (verbatim timestamps)
    batching.py              create_smart_batches (sentence end or ≥1.5 s gap), split_into_lanes
    text_metrics.py          CPS and deterministic flags (HIGH_CPS / EMPTY / IDENTICAL / MISSING)
    store.py                 aiosqlite job + block store
    llm/                     OpenAI-compatible (DeepSeek/Gemini/OpenRouter) + Anthropic clients, factory, retries
    pipeline/                prompts, stage1_bible, stage2_translate, stage3_qa, events, orchestrator
    pricing.py, estimate.py  cost table and pre-run estimator
  tests/                     pytest suite, mock LLM server, fixtures
frontend/src/
  App.tsx                    workspace shell and actions
  hooks/useJob.ts            reducer + SSE (auto-reconnect, snapshot resync)
  components/                TopBar, SettingsModal, PipelineProgress, BibleDrawer, SubtitleGrid/Row (virtualized), …
```

## Tests

```bash
cd backend && .venv/bin/python -m pytest -q
cd frontend && npm run build
```
