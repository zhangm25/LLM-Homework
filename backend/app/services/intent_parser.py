import json
import os
import re
import urllib.error
import urllib.request
from typing import Any

from pydantic import ValidationError

from app.schemas import IntentParseResponse, TaskStep

DEFAULT_LLM_API_BASE_URL = "https://api.openai.com/v1"
DEFAULT_LLM_MODEL = "gpt-4o-mini"


def parse_user_intent(query: str) -> IntentParseResponse:
    cleaned_query = query.strip()
    if not cleaned_query:
        return _fallback_parse(query)

    llm_result = _try_parse_with_llm(cleaned_query)
    if llm_result is not None:
        return llm_result

    return _fallback_parse(cleaned_query)


def _try_parse_with_llm(query: str) -> IntentParseResponse | None:
    api_key = _get_env("LLM_API_KEY")
    if not api_key:
        return None

    api_base_url = _get_env("LLM_API_BASE_URL") or DEFAULT_LLM_API_BASE_URL
    model = _get_env("LLM_MODEL") or DEFAULT_LLM_MODEL
    endpoint = f"{api_base_url.rstrip('/')}/chat/completions"

    payload = {
        "model": model,
        "temperature": 0.1,
        "messages": [
            {
                "role": "system",
                "content": (
                    "You parse vague user errands or travel requests into a task-chain JSON. "
                    "Return JSON only. No markdown. The JSON schema is: "
                    "{"
                    "\"tasks\":[{\"order\":1,\"action\":\"eat\",\"category\":\"food\","
                    "\"query_hint\":\"restaurant near current location\","
                    "\"required_resource\":\"poi_search\",\"notes\":\"optional note\"}],"
                    "\"task_chain\":[\"eat\"],"
                    "\"assumptions\":[\"short assumption\"]"
                    "}. "
                    "Use concise English action/category/query_hint values even when input is Chinese."
                ),
            },
            {"role": "user", "content": query},
        ],
    }

    try:
        response_json = _post_json(endpoint, payload, api_key)
        content = response_json["choices"][0]["message"]["content"]
        parsed = _extract_json_object(content)
        return _validate_llm_result(query, parsed)
    except (KeyError, TypeError, ValueError, urllib.error.URLError, TimeoutError, ValidationError):
        return None


def _post_json(endpoint: str, payload: dict[str, Any], api_key: str) -> dict[str, Any]:
    request = urllib.request.Request(
        endpoint,
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=20) as response:
        return json.loads(response.read().decode("utf-8"))


def _extract_json_object(content: str) -> dict[str, Any]:
    text = content.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?", "", text).strip()
        text = re.sub(r"```$", "", text).strip()

    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        start = text.find("{")
        end = text.rfind("}")
        if start == -1 or end == -1 or end <= start:
            raise
        parsed = json.loads(text[start : end + 1])

    if not isinstance(parsed, dict):
        raise ValueError("LLM response must be a JSON object.")
    return parsed


def _validate_llm_result(query: str, parsed: dict[str, Any]) -> IntentParseResponse:
    payload = {
        "original_query": query,
        "source": "llm",
        "tasks": parsed.get("tasks", []),
        "task_chain": parsed.get("task_chain", []),
        "assumptions": parsed.get("assumptions", []),
    }
    result = IntentParseResponse.model_validate(payload)
    if not result.tasks:
        raise ValueError("At least one task is required.")
    if not result.task_chain:
        result.task_chain = [task.action for task in result.tasks]
    return result


def _fallback_parse(query: str) -> IntentParseResponse:
    parts = _split_query(query)
    tasks = [_build_fallback_task(index, part) for index, part in enumerate(parts, start=1)]
    return IntentParseResponse(
        original_query=query,
        source="fallback",
        tasks=tasks,
        task_chain=[task.action for task in tasks],
        assumptions=["LLM parsing was unavailable or invalid, so rule-based parsing was used."],
    )


def _split_query(query: str) -> list[str]:
    separators = [
        r"\+",
        r"->",
        r",",
        r";",
        r"\s+then\s+",
        r"\s+and\s+",
        "\uff0c",
        "\u3001",
        "\uff1b",
        "\u7136\u540e",
        "\u518d",
        "\u548c",
    ]
    parts = re.split("|".join(separators), query, flags=re.IGNORECASE)
    cleaned = [part.strip() for part in parts if part.strip()]
    return cleaned or [query.strip() or "general task"]


def _build_fallback_task(order: int, text: str) -> TaskStep:
    normalized = text.lower()
    category, action, query_hint, resource = _classify_task(normalized, text)
    return TaskStep(
        order=order,
        action=action,
        category=category,
        query_hint=query_hint,
        required_resource=resource,
        notes="Parsed by keyword fallback.",
    )


def _classify_task(normalized: str, original: str) -> tuple[str, str, str, str]:
    if _contains_any(normalized, ["eat", "meal", "restaurant", "food", "\u5403\u996d", "\u5403", "\u7f8e\u98df"]):
        return "food", "eat", "restaurant or dining place", "poi_search"
    if _contains_any(normalized, ["basketball", "badminton", "ball", "sport", "\u6253\u7403", "\u7bee\u7403", "\u7fbd\u6bdb\u7403"]):
        return "sports", "play ball", "sports court or gym", "poi_search"
    if _contains_any(normalized, ["coffee", "cafe", "\u5496\u5561"]):
        return "cafe", "drink coffee", "coffee shop", "poi_search"
    if _contains_any(normalized, ["library", "study", "\u56fe\u4e66\u9986", "\u5b66\u4e60"]):
        return "study", "go to library", "library or study space", "poi_search"
    if _contains_any(normalized, ["package", "parcel", "delivery", "pickup", "\u5feb\u9012", "\u53d6\u4ef6", "\u53d6\u5feb\u9012"]):
        return "errand", "pick up package", "package pickup point", "poi_search"
    if _contains_any(normalized, ["dorm", "home", "return", "\u5bbf\u820d", "\u56de\u5bb6", "\u56de"]):
        return "return", "return", "destination routing", "route_planning"
    return "general", original, original, "task_planning"


def _contains_any(text: str, candidates: list[str]) -> bool:
    return any(candidate in text for candidate in candidates)


def _get_env(name: str) -> str | None:
    value = os.getenv(name)
    if value:
        return value

    env_path = os.path.join(os.getcwd(), ".env")
    if not os.path.exists(env_path):
        return None

    with open(env_path, encoding="utf-8") as env_file:
        for line in env_file:
            stripped = line.strip()
            if not stripped or stripped.startswith("#") or "=" not in stripped:
                continue
            key, raw_value = stripped.split("=", 1)
            if key.strip() == name:
                return raw_value.strip().strip("\"'")
    return None
