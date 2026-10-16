"""Server settings. API keys and provider base URLs come ONLY from the environment / backend/.env."""

from __future__ import annotations

from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

BACKEND_DIR = Path(__file__).resolve().parent.parent
PROJECT_DIR = BACKEND_DIR.parent

# Env var names per provider, surfaced to the UI so users know what to add to backend/.env.
KEY_ENV_NAMES: dict[str, str] = {
    "deepseek": "DEEPSEEK_API_KEY",
    "claude": "ANTHROPIC_API_KEY",
    "gemini": "GEMINI_API_KEY",
    "openrouter": "OPENROUTER_API_KEY",
}


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=BACKEND_DIR / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    deepseek_api_key: str = ""
    anthropic_api_key: str = ""
    gemini_api_key: str = ""
    openrouter_api_key: str = ""

    deepseek_base_url: str = "https://api.deepseek.com/v1"
    gemini_base_url: str = "https://generativelanguage.googleapis.com/v1beta/openai/"
    openrouter_base_url: str = "https://openrouter.ai/api/v1"

    subtrans_db_path: str = str(BACKEND_DIR / "data" / "subtrans.db")
    # Postgres connection string (set by the Neon integration on Vercel). Wins over the SQLite path.
    database_url: str = ""
    postgres_url: str = ""

    # Password login. Required whenever APP_PASSWORD is set, and always on Vercel.
    app_password: str = ""
    # Optional; defaults to a key derived from APP_PASSWORD, so changing the password signs everyone out.
    subtrans_session_secret: str = ""
    # "local" runs jobs in this process; "queue" hands each step to Vercel Queues. Default: queue on Vercel.
    subtrans_runner: str = ""
    # Queue mode: seconds a step may keep starting new LLM calls before handing over to the next step.
    subtrans_step_budget_sec: float = 150.0
    vercel: str = ""
    subtrans_cors_origins: str = "http://localhost:5173,http://127.0.0.1:5173"
    subtrans_request_timeout_sec: float = 240.0
    subtrans_max_upload_mb: int = 20

    def key_for(self, provider: str) -> str:
        return {
            "deepseek": self.deepseek_api_key,
            "claude": self.anthropic_api_key,
            "gemini": self.gemini_api_key,
            "openrouter": self.openrouter_api_key,
        }[provider].strip()

    @property
    def database_target(self) -> str:
        return (self.database_url or self.postgres_url or self.subtrans_db_path).strip()

    @property
    def on_vercel(self) -> bool:
        return bool(self.vercel)

    @property
    def runner_mode(self) -> str:
        return self.subtrans_runner or ("queue" if self.on_vercel else "local")

    @property
    def auth_required(self) -> bool:
        return bool(self.app_password) or self.on_vercel

    def key_status(self) -> dict[str, bool]:
        return {provider: bool(self.key_for(provider)) for provider in KEY_ENV_NAMES}


def get_settings() -> Settings:
    """Re-read on every call so edits to backend/.env are picked up without a restart."""
    return Settings()
