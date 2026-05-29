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


def _strip_code_fence(text: str) -> str:
    """Tolerate models that wrap JSON in ```json fences."""
    cleaned = text.strip()
    if cleaned.startswith("```"):
        cleaned = cleaned.split("\n", 1)[-1] if "\n" in cleaned else cleaned
        if cleaned.endswith("```"):
            cleaned = cleaned[: -3]
    return cleaned.strip()


class DeepSeekClient:
    def __init__(self) -> None:
        settings = get_settings()
        self._settings = settings
        self._client = None
        if settings.llm_enabled:
            # Imported lazily so the package imports even if `openai` is absent.
            from openai import AsyncOpenAI

            self._client = AsyncOpenAI(
                api_key=settings.deepseek_api_key,
                base_url=settings.deepseek_base_url,
                timeout=settings.llm_timeout_s,
                max_retries=settings.llm_max_retries,
            )

    @property
    def enabled(self) -> bool:
        return self._client is not None

    async def check_health(self) -> dict:
        """Tiny live probe used by /api/config so the UI can distinguish
        "key present" from "the model endpoint actually works"."""
        model = self._settings.deepseek_model
        base_url = self._settings.deepseek_base_url
        if self._client is None:
            return {
                "configured": False,
                "ok": False,
                "model": model,
                "base_url": base_url,
                "message": "未配置 DeepSeek API Key",
            }
        try:
            resp = await self._client.chat.completions.create(
                model=model,
                messages=[
                    {
                        "role": "system",
                        "content": "你是连通性检查。只输出 JSON。",
                    },
                    {
                        "role": "user",
                        "content": '请只输出 {"ok": true}',
                    },
                ],
                temperature=0,
                max_tokens=20,
                response_format={"type": "json_object"},
            )
            raw = resp.choices[0].message.content or "{}"
            data = json.loads(_strip_code_fence(raw))
            ok = bool(data.get("ok"))
            return {
                "configured": True,
                "ok": ok,
                "model": model,
                "base_url": base_url,
                "message": "LLM 连接正常" if ok else f"模型响应异常：{raw[:120]}",
            }
        except Exception as exc:
            return {
                "configured": True,
                "ok": False,
                "model": model,
                "base_url": base_url,
                "message": f"{type(exc).__name__}: {str(exc)[:180]}",
            }

    async def complete_json(
        self, system: str, user: str, temperature: Optional[float] = None
    ) -> dict:
        """Return parsed JSON. One repair attempt on malformed output."""
        assert self._client is not None
        temp = self._settings.llm_temperature_cold if temperature is None else temperature
        messages = [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ]
        resp = await self._client.chat.completions.create(
            model=self._settings.deepseek_model,
            messages=messages,
            temperature=temp,
            response_format={"type": "json_object"},
        )
        raw = resp.choices[0].message.content or "{}"
        try:
            return json.loads(_strip_code_fence(raw))
        except json.JSONDecodeError:
            # Self-repair: feed the bad output back once.
            messages.append({"role": "assistant", "content": raw})
            messages.append(
                {"role": "user", "content": "上面的输出不是合法 JSON，请只重新输出合法 JSON。"}
            )
            resp = await self._client.chat.completions.create(
                model=self._settings.deepseek_model,
                messages=messages,
                temperature=temp,
                response_format={"type": "json_object"},
            )
            return json.loads(_strip_code_fence(resp.choices[0].message.content or "{}"))

    async def complete_text(
        self, system: str, user: str, temperature: Optional[float] = None
    ) -> str:
        assert self._client is not None
        temp = self._settings.llm_temperature_warm if temperature is None else temperature
        resp = await self._client.chat.completions.create(
            model=self._settings.deepseek_model,
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            temperature=temp,
        )
        return resp.choices[0].message.content or ""

    async def stream_text(
        self, system: str, user: str, temperature: Optional[float] = None
    ) -> AsyncIterator[str]:
        assert self._client is not None
        temp = self._settings.llm_temperature_warm if temperature is None else temperature
        stream = await self._client.chat.completions.create(
            model=self._settings.deepseek_model,
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            temperature=temp,
            stream=True,
        )
        async for chunk in stream:
            delta = chunk.choices[0].delta.content if chunk.choices else None
            if delta:
                yield delta


@lru_cache
def get_llm() -> DeepSeekClient:
    return DeepSeekClient()
