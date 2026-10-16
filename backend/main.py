"""SubTrans Studio API: FastAPI app and REST routes.

Run locally:  uvicorn main:app --reload --port 8000   (from the backend/ directory)
On Vercel the same app is loaded through ../app.py; job steps run in ../worker.py.
"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, AsyncIterator, Callable
from urllib.parse import quote

from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel, Field

from subtrans import auth
from subtrans.config import KEY_ENV_NAMES, PROJECT_DIR, get_settings
from subtrans.models import (
    BibleUpdate,
    Block,
    BlockEdit,
    JobChanges,
    JobDetail,
    JobSummary,
    RerunRequest,
    RunConfig,
    RunRequest,
)
from subtrans.pipeline.orchestrator import JobConflict, JobManager, JobNotFound, MissingKeys
from subtrans.runtime import build_manager
from subtrans.srt_parser import parse_srt_bytes
from subtrans.store import JobStore

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("subtrans")

# Vercel rejects request bodies over 4.5 MB before they reach the function.
VERCEL_MAX_UPLOAD_MB = 4


class LoginRequest(BaseModel):
    password: str = Field(max_length=512)


def create_app(manager_factory: Callable[[JobStore], JobManager] | None = None) -> FastAPI:
    """manager_factory(store) -> JobManager lets tests inject a fake LLM client factory."""

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        settings = get_settings()
        store = JobStore(getattr(app.state, "db_path", None) or settings.database_target)
        await store.init()
        manager = manager_factory(store) if manager_factory else build_manager(store)
        recovered = await manager.startup()
        if recovered:
            log.info("Marked %s interrupted job(s) as resumable", recovered)
        app.state.manager = manager
        try:
            yield
        finally:
            await manager.shutdown()
            await store.close()

    app = FastAPI(title="SubTrans Studio", version="2.0.0", lifespan=lifespan, docs_url=None, redoc_url=None)
    settings = get_settings()
    app.add_middleware(
        CORSMiddleware,
        allow_origins=[o.strip() for o in settings.subtrans_cors_origins.split(",") if o.strip()],
        allow_methods=["*"],
        allow_headers=["*"],
        allow_credentials=True,
    )

    def manager(request: Request) -> JobManager:
        return request.app.state.manager

    # ------------------------------------------------------------------ auth gate
    @app.middleware("http")
    async def require_login(request: Request, call_next: Callable[[Request], Any]) -> Response:
        path = request.url.path
        if path.startswith("/api/") and not path.startswith("/api/auth/"):
            s = get_settings()
            if s.auth_required:
                if not s.app_password:
                    return JSONResponse(status_code=503, content={"detail": "Login is not configured: set APP_PASSWORD."})
                if not auth.session_valid(s, request.cookies.get(auth.COOKIE_NAME)):
                    return JSONResponse(status_code=401, content={"detail": "Sign in required."})
        response: Response = await call_next(request)
        if path.startswith("/api/"):
            response.headers["Cache-Control"] = "no-store"
        return response

    def client_key(request: Request) -> str:
        # Behind Vercel the platform sets x-real-ip; locally there is no proxy to trust.
        if get_settings().on_vercel:
            return request.headers.get("x-real-ip") or request.headers.get("x-forwarded-for", "").split(",")[0].strip() or "unknown"
        return request.client.host if request.client else "unknown"

    def secure_cookie(request: Request) -> bool:
        return request.url.scheme == "https" or request.headers.get("x-forwarded-proto") == "https"

    @app.get("/api/auth/session")
    async def session(request: Request) -> dict[str, Any]:
        s = get_settings()
        return {
            "auth_required": s.auth_required,
            "configured": bool(s.app_password),
            "authenticated": (not s.auth_required) or auth.session_valid(s, request.cookies.get(auth.COOKIE_NAME)),
        }

    @app.post("/api/auth/login")
    async def login(body: LoginRequest, request: Request) -> Response:
        s = get_settings()
        if not s.app_password:
            return JSONResponse(status_code=503, content={"detail": "Login is not configured: set APP_PASSWORD."})
        try:
            ok = await auth.check_login(manager(request).store, s, client_key(request), body.password)
        except auth.LoginThrottled:
            return JSONResponse(status_code=429, content={"detail": "Too many attempts. Try again in 15 minutes."})
        if not ok:
            return JSONResponse(status_code=401, content={"detail": "Wrong password."})
        response = JSONResponse({"authenticated": True})
        response.set_cookie(
            auth.COOKIE_NAME,
            auth.make_session(s),
            max_age=auth.SESSION_TTL,
            httponly=True,
            secure=secure_cookie(request),
            samesite="strict",
            path="/",
        )
        return response

    @app.post("/api/auth/logout")
    async def logout(request: Request) -> Response:
        response = JSONResponse({"authenticated": False})
        response.delete_cookie(auth.COOKIE_NAME, path="/", httponly=True, secure=secure_cookie(request), samesite="strict")
        return response

    # ------------------------------------------------------------------ errors
    @app.exception_handler(JobNotFound)
    async def _not_found(_: Request, exc: JobNotFound) -> JSONResponse:
        return JSONResponse(status_code=404, content={"detail": f"Not found: {exc}"})

    @app.exception_handler(JobConflict)
    async def _conflict(_: Request, exc: JobConflict) -> JSONResponse:
        return JSONResponse(status_code=409, content={"detail": str(exc)})

    @app.exception_handler(MissingKeys)
    async def _missing_keys(_: Request, exc: MissingKeys) -> JSONResponse:
        return JSONResponse(status_code=400, content={"detail": str(exc), "missing_keys": exc.keys})

    # ------------------------------------------------------------------ config
    @app.get("/api/config")
    async def get_config() -> dict[str, Any]:
        s = get_settings()
        return {
            "keys": s.key_status(),
            "key_env_names": KEY_ENV_NAMES,
            "defaults": RunConfig().model_dump(),
            "base_urls": {
                "deepseek": s.deepseek_base_url,
                "gemini": s.gemini_base_url,
                "openrouter": s.openrouter_base_url,
            },
        }

    # ------------------------------------------------------------------ jobs
    @app.post("/api/jobs", response_model=JobDetail)
    async def create_job(
        request: Request,
        file: UploadFile = File(...),
        target_language: str = Form(""),
        context_hints: str = Form(""),
    ) -> JobDetail:
        s = get_settings()
        limit_mb = min(s.subtrans_max_upload_mb, VERCEL_MAX_UPLOAD_MB) if s.on_vercel else s.subtrans_max_upload_mb
        name = Path(file.filename or "subtitles.srt").name
        if not name.lower().endswith(".srt"):
            raise HTTPException(400, "Only .srt files are supported.")
        data = await file.read(limit_mb * 1024 * 1024 + 1)
        if len(data) > limit_mb * 1024 * 1024:
            raise HTTPException(413, f"File exceeds {limit_mb} MB.")
        parsed = parse_srt_bytes(data)
        if not parsed.blocks:
            raise HTTPException(400, "No subtitle blocks found — is this a valid .srt file?")
        mgr = manager(request)
        live = await mgr.create(name, parsed, target_language, context_hints)
        for warning in parsed.warnings:
            log.info("[job %s] parse warning: %s", live.meta.id, warning)
        return await mgr.detail(live)

    @app.get("/api/jobs", response_model=list[JobSummary])
    async def list_jobs(request: Request) -> list[JobSummary]:
        return await manager(request).list_jobs()

    @app.get("/api/jobs/{job_id}", response_model=JobDetail)
    async def get_job(job_id: str, request: Request) -> JobDetail:
        return await manager(request).detail_by_id(job_id)

    @app.get("/api/jobs/{job_id}/changes", response_model=JobChanges)
    async def job_changes(job_id: str, request: Request, rev: int = 0, log: int = 0) -> JobChanges:
        return await manager(request).changes(job_id, rev, log)

    @app.delete("/api/jobs/{job_id}", status_code=204)
    async def delete_job(job_id: str, request: Request) -> Response:
        await manager(request).delete(job_id)
        return Response(status_code=204)

    @app.post("/api/jobs/{job_id}/estimate")
    async def estimate(job_id: str, body: RunRequest, request: Request) -> dict[str, Any]:
        return await manager(request).estimate(job_id, body)

    @app.post("/api/jobs/{job_id}/run", response_model=JobSummary)
    async def run_job(job_id: str, body: RunRequest, request: Request) -> JobSummary:
        live = await manager(request).start(job_id, body)
        return JobSummary.from_meta(live.meta)

    @app.post("/api/jobs/{job_id}/pause", response_model=JobSummary)
    async def pause_job(job_id: str, request: Request) -> JobSummary:
        live = await manager(request).pause(job_id)
        return JobSummary.from_meta(live.meta)

    @app.post("/api/jobs/{job_id}/rerun", response_model=JobSummary)
    async def rerun_job(job_id: str, body: RerunRequest, request: Request) -> JobSummary:
        live = await manager(request).rerun(job_id, body)
        return JobSummary.from_meta(live.meta)

    @app.put("/api/jobs/{job_id}/bible", response_model=JobSummary)
    async def put_bible(job_id: str, body: BibleUpdate, request: Request) -> JobSummary:
        live = await manager(request).update_bible(job_id, body)
        return JobSummary.from_meta(live.meta)

    @app.patch("/api/jobs/{job_id}/blocks/{block_id}", response_model=Block)
    async def edit_block(job_id: str, block_id: str, body: BlockEdit, request: Request) -> Block:
        return await manager(request).edit_block(job_id, block_id, body.text)

    @app.post("/api/jobs/{job_id}/blocks/{block_id}/revert", response_model=Block)
    async def revert_block(job_id: str, block_id: str, request: Request) -> Block:
        return await manager(request).revert_block(job_id, block_id)

    @app.get("/api/jobs/{job_id}/export")
    async def export_job(job_id: str, request: Request) -> Response:
        filename, content, untranslated = await manager(request).export(job_id)
        ascii_name = filename.encode("ascii", "replace").decode().replace("?", "_")
        return Response(
            content=content.encode("utf-8"),
            media_type="application/x-subrip; charset=utf-8",
            headers={
                "Content-Disposition": f"attachment; filename=\"{ascii_name}\"; filename*=UTF-8''{quote(filename)}",
                "X-Untranslated-Lines": str(untranslated),
                "Access-Control-Expose-Headers": "Content-Disposition, X-Untranslated-Lines",
            },
        )

    # ------------------------------------------------------------------ static frontend (production build)
    # On Vercel, app.frontend() files are promoted to the CDN at build time (see pyproject.toml);
    # API routes always take priority over them, and the login gate above only covers /api.
    dist = PROJECT_DIR / "frontend" / "dist"
    if dist.is_dir():
        app.frontend("/", directory=dist, fallback="index.html")

    return app


app = create_app()
