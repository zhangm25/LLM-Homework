"""DeepSeek transport (OpenAI-compatible).

Thin async wrapper around the ``openai`` SDK with ``base_url`` pointed at
DeepSeek. Exposes three primitives — JSON completion (with one self-repair
retry, §6.4.4), plain text, and streamed text. All prompt-specific glue lives
in the pipeline; this file only knows how to talk to the model.

When no API key is configured, ``enabled`` is False and callers fall back to
the deterministic heuristics in the pipeline.
"""

from __future__ import annotations

import json
from functools import lru_cache
from typing import AsyncIterator, Optional

from ..config import get_settings
from ..debug_log import announce_debug_log_path, write_debug_log


def _strip_code_fence(text: str) -> str:
    """Tolerate models that wrap JSON in ```json fences."""
    cleaned = text.strip()
    if cleaned.startswith("```"):
        cleaned = cleaned.split("\n", 1)[-1] if "\n" in cleaned else cleaned
        if cleaned.endswith("```"):
            cleaned = cleaned[: -3]
    return cleaned.strip()


def _preview(text: str, limit: int = 6000) -> str:
    text = text or ""
    return text if len(text) <= limit else text[:limit] + f"\n...<truncated {len(text) - limit} chars>"


class OpenAICompatibleLLMClient:
    def __init__(self) -> None:
        settings = get_settings()
        self._settings = settings
        self._client = None
        if settings.llm_enabled:
            # Imported lazily so the package imports even if `openai` is absent.
            from openai import AsyncOpenAI

            self._client = AsyncOpenAI(
                api_key=settings.effective_llm_api_key,
                base_url=settings.effective_llm_base_url,
                timeout=settings.llm_timeout_s,
                max_retries=settings.llm_max_retries,
            )
        announce_debug_log_path()

    @property
    def enabled(self) -> bool:
        return self._client is not None

    def _debug_log(self, stage: str, system: str, user: str, raw: str | None = None) -> None:
        if not self._settings.llm_debug_log:
            return
        text = (
            "\n"
            f"========== LLM DEBUG [{stage}] ==========\n"
            f"provider={self._settings.effective_llm_provider} "
            f"model={self._settings.effective_llm_model} "
            f"base_url={self._settings.effective_llm_base_url}\n"
            "----- system -----\n"
            f"{_preview(system)}\n"
            "----- user -----\n"
            f"{_preview(user)}"
        )
        if raw is not None:
            text += (
                "\n"
                "----- raw response -----\n"
                f"{_preview(raw)}\n"
                f"========== END LLM DEBUG [{stage}] ==========\n"
            )
        write_debug_log(text)

    async def check_health(self) -> dict:
        """Tiny live probe used by /api/config so the UI can distinguish
        "key present" from "the model endpoint actually works"."""
        model = self._settings.effective_llm_model
        base_url = self._settings.effective_llm_base_url
        provider = self._settings.effective_llm_provider
        if self._client is None:
            return {
                "configured": False,
                "ok": False,
                "provider": provider,
                "model": model,
                "base_url": base_url,
                "message": "未配置 LLM API Key",
            }
        try:
            system = "你是连通性检查。只输出 JSON。"
            user = '请只输出 {"ok": true}'
            resp = await self._client.chat.completions.create(
                model=model,
                messages=[
                    {
                        "role": "system",
                        "content": system,
                    },
                    {
                        "role": "user",
                        "content": user,
                    },
                ],
                temperature=0,
                max_tokens=20,
                response_format={"type": "json_object"},
            )
            raw = resp.choices[0].message.content or "{}"
            self._debug_log("health", system, user, raw)
            data = json.loads(_strip_code_fence(raw))
            ok = bool(data.get("ok"))
            return {
                "configured": True,
                "ok": ok,
                "provider": provider,
                "model": model,
                "base_url": base_url,
                "message": "LLM 连接正常" if ok else f"模型响应异常：{raw[:120]}",
            }
        except Exception as exc:
            return {
                "configured": True,
                "ok": False,
                "provider": provider,
                "model": model,
                "base_url": base_url,
                "message": f"{type(exc).__name__}: {str(exc)[:180]}",
            }

    async def complete_json(
        self, system: str, user: str, temperature: Optional[float] = None, stage: str = "json"
    ) -> dict:
        """Return parsed JSON. One repair attempt on malformed output."""
        assert self._client is not None
        temp = self._settings.llm_temperature_cold if temperature is None else temperature
        messages = [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ]
        resp = await self._client.chat.completions.create(
            model=self._settings.effective_llm_model,
            messages=messages,
            temperature=temp,
            response_format={"type": "json_object"},
        )
        raw = resp.choices[0].message.content or "{}"
        self._debug_log(stage, system, user, raw)
        try:
            return json.loads(_strip_code_fence(raw))
        except json.JSONDecodeError:
            # Self-repair: feed the bad output back once.
            messages.append({"role": "assistant", "content": raw})
            repair_user = "上面的输出不是合法 JSON，请只重新输出合法 JSON。"
            messages.append({"role": "user", "content": repair_user})
            resp = await self._client.chat.completions.create(
                model=self._settings.effective_llm_model,
                messages=messages,
                temperature=temp,
                response_format={"type": "json_object"},
            )
            repaired = resp.choices[0].message.content or "{}"
            self._debug_log(f"{stage}:repair", system, repair_user, repaired)
            return json.loads(_strip_code_fence(repaired))

    async def complete_text(
        self, system: str, user: str, temperature: Optional[float] = None, stage: str = "text"
    ) -> str:
        assert self._client is not None
        temp = self._settings.llm_temperature_warm if temperature is None else temperature
        resp = await self._client.chat.completions.create(
            model=self._settings.effective_llm_model,
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            temperature=temp,
        )
        raw = resp.choices[0].message.content or ""
        self._debug_log(stage, system, user, raw)
        return raw

    async def stream_text(
        self, system: str, user: str, temperature: Optional[float] = None, stage: str = "stream"
    ) -> AsyncIterator[str]:
        assert self._client is not None
        temp = self._settings.llm_temperature_warm if temperature is None else temperature
        stream = await self._client.chat.completions.create(
            model=self._settings.effective_llm_model,
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            temperature=temp,
            stream=True,
        )
        chunks: list[str] = []
        async for chunk in stream:
            delta = chunk.choices[0].delta.content if chunk.choices else None
            if delta:
                chunks.append(delta)
                yield delta
        self._debug_log(stage, system, user, "".join(chunks))


@lru_cache
def get_llm() -> OpenAICompatibleLLMClient:
    return OpenAICompatibleLLMClient()
