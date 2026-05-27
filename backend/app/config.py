"""Application configuration.

All secrets live in a single project-root ``.env`` file (see ``.env.example``).
The backend only reads the server-side keys from it; the ``VITE_*`` entries in
the same file are consumed by the frontend build and ignored here
(``extra="ignore"``).
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

# backend/app/config.py -> parents[2] == repository root
PROJECT_ROOT = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        # Search order: root .env (the single file the user fills in), then a
        # backend-local override if present. Absolute paths so it works
        # regardless of the process working directory.
        env_file=(PROJECT_ROOT / ".env", PROJECT_ROOT / "backend" / ".env"),
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # --- DeepSeek (OpenAI-compatible) -------------------------------------
    deepseek_api_key: str = ""
    deepseek_base_url: str = "https://api.deepseek.com"
    # `deepseek-chat` maps to DeepSeek-V4-Flash today; switch to
    # `deepseek-v4-flash` / `deepseek-v4-pro` explicitly if you prefer.
    deepseek_model: str = "deepseek-chat"
    llm_temperature_cold: float = 0.1  # P1/P3/P4 extraction & selection
    llm_temperature_warm: float = 0.6  # P5 narration

    # --- AMap (Web Service API, server side) ------------------------------
    amap_web_service_key: str = ""

    # --- Runtime knobs ----------------------------------------------------
    request_timeout_s: float = 15.0  # AMap HTTP calls (should be fast)
    # DeepSeek-V4 "thinking" extraction can legitimately take 20-30s. A 15s
    # timeout kills it mid-generation and the SDK retries (≈45s wasted) before
    # falling back. A longer single-shot window finishes in one attempt — faster
    # overall, and it lets the replanning patch actually complete.
    llm_timeout_s: float = 40.0
    llm_max_retries: int = 1
    default_city: str = "北京"
    # Comma-separated allowed CORS origins. "*" = allow any origin (handy when
    # the frontend is opened from another machine by IP). Lock it down in prod,
    # e.g. CORS_ORIGINS=http://101.6.68.101:5173
    cors_origins: str = "*"

    @property
    def llm_enabled(self) -> bool:
        return bool(self.deepseek_api_key)

    @property
    def amap_enabled(self) -> bool:
        return bool(self.amap_web_service_key)

    @property
    def cors_origin_list(self) -> list[str]:
        return [origin.strip() for origin in self.cors_origins.split(",") if origin.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()
