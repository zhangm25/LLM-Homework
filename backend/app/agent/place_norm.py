"""Shared place-name normalization.

LLM extraction is the primary path, but model outputs still cross a system
boundary. Normalize only obvious conversational residue here; do not infer new
places or reorder anything.
"""

from __future__ import annotations

_PLACE_SUFFIXES = [
    "接一下我", "接一下", "接下我", "接下", "接上我", "接上", "接我", "接女朋友", "接男朋友",
    "睡觉", "休息",
    "散散步", "喝咖啡", "找朋友", "自习", "学习", "上课", "散步", "吃饭", "喝茶", "找人",
    "逛逛", "玩玩", "看看", "待着", "休息", "吃", "喝", "逛", "玩", "看", "找", "待", "散",
]
_PLACE_SPLIT_MARKERS = (
    "接一下", "接下", "接上", "接我", "接女朋友", "接男朋友", "接朋友",
    "吃个饭", "吃顿饭", "吃饭", "吃点饭", "吃点", "找朋友", "找人", "看电影",
    "带上", "带我", "和我", "跟我", "一起", "然后", "再去", "顺便",
)
_PLACE_PREFIXES = (
    "要不我中途去", "我中途去", "中途去",
    "打算走路去一下", "打算走路去下", "走路去一下", "走路去下",
    "打算去一下", "打算去下", "去一下", "去下",
    "要不去", "我去", "去", "到",
)
_PLACE_LIKE_TOKENS = ("大学", "公寓", "楼", "中心", "公园", "商场", "门", "园", "街", "站")


def normalize_place_name(name: str | None) -> str | None:
    s = (name or "").strip()
    if not s:
        return None
    for prefix in _PLACE_PREFIXES:
        if s.startswith(prefix) and len(s) - len(prefix) >= 2:
            s = s[len(prefix):]
            break
    if s.startswith("下") and len(s) > 2 and any(token in s for token in _PLACE_LIKE_TOKENS):
        s = s[1:]
    for marker in _PLACE_SPLIT_MARKERS:
        if marker in s:
            s = s.split(marker, 1)[0]
    changed = True
    while changed and len(s) > 2:
        changed = False
        for suffix in _PLACE_SUFFIXES:
            if s.endswith(suffix) and len(s) - len(suffix) >= 2:
                s = s[: -len(suffix)]
                changed = True
                break
    s = s.strip(" 的")
    return s or None
