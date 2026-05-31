"""Application configuration.

All secrets live in a single project-root ``.env`` file (see ``.env.example``)
or in the external file pointed to by ``ROAMMIND_ENV_FILE``. Server-side keys
stay in the backend; the browser-side AMap JS config is exposed explicitly via
``/api/config`` so release builds can be configured after packaging.
"""

from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

# backend/app/config.py -> parents[2] == repository root
PROJECT_ROOT = Path(__file__).resolve().parents[2]
_EXTERNAL_ENV_FILE = os.environ.get("ROAMMIND_ENV_FILE")
_ENV_FILES = tuple(
    Path(p)
    for p in (
        _EXTERNAL_ENV_FILE,
        PROJECT_ROOT / ".env",
        PROJECT_ROOT / "backend" / ".env",
    )
    if p
)


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        # Search order: an explicit external file, then root .env, then a
        # backend-local override if present. Absolute paths so it works
        # regardless of the process working directory.
        env_file=_ENV_FILES,
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # --- LLM (OpenAI-compatible) ------------------------------------------
    # Prefer the generic LLM_* variables. The older DEEPSEEK_* variables are
    # kept as a compatibility fallback so existing local .env files still work.
    llm_provider: str = "deepseek"
    llm_api_key: str = ""
    llm_base_url: str = ""
    llm_model: str = ""

    # --- Legacy DeepSeek names (fallback only) -----------------------------
    deepseek_api_key: str = ""
    deepseek_base_url: str = "https://api.deepseek.com"
    # `deepseek-chat` maps to DeepSeek-V4-Flash today; switch to
    # `deepseek-v4-flash` / `deepseek-v4-pro` explicitly if you prefer.
    deepseek_model: str = "deepseek-chat"
    llm_temperature_cold: float = 0.1  # P1/P3/P4 extraction & selection
    llm_temperature_warm: float = 0.6  # P5 narration
    # Print LLM prompts and raw model responses to the backend console. Intended
    # for local debugging only; leave off during demos with private user data.
    llm_debug_log: bool = False
    llm_debug_log_file: str = str(PROJECT_ROOT.parent / "_local_work" / "logs" / "llm_debug.log")

    # --- AMap (Web Service API, server side) ------------------------------
    amap_web_service_key: str = ""
    # --- AMap JS API (public browser-side config) -------------------------
    # These are not backend secrets. They are exposed via /api/config so a
    # release build can be configured by an external .env file after packaging.
    vite_amap_js_key: str = ""
    vite_amap_js_security_code: str = ""

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
        return bool(self.effective_llm_api_key)

    @property
    def effective_llm_provider(self) -> str:
        return (self.llm_provider or "deepseek").strip().lower()

    @property
    def effective_llm_api_key(self) -> str:
        return self.llm_api_key or self.deepseek_api_key

    @property
    def effective_llm_base_url(self) -> str:
        if self.llm_base_url:
            return self.llm_base_url
        if self.effective_llm_provider == "zhipu":
            return "https://open.bigmodel.cn/api/paas/v4"
        return self.deepseek_base_url

    @property
    def effective_llm_model(self) -> str:
        if self.llm_model:
            return self.llm_model
        if self.effective_llm_provider == "zhipu":
            return "glm-4-flash-250414"
        return self.deepseek_model

    @property
    def amap_enabled(self) -> bool:
        return bool(self.amap_web_service_key)

    @property
    def cors_origin_list(self) -> list[str]:
        return [origin.strip() for origin in self.cors_origins.split(",") if origin.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()
