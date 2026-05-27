"""Function-calling tool schemas (PROPOSAL §6.3).

The default pipeline is deterministic (it calls AMap directly at fixed steps),
which is more reproducible than a free-form tool-calling loop. These OpenAI-
format schemas document the contract and are ready to pass as ``tools=`` if you
later want the LLM to drive tool calls autonomously.
"""

from __future__ import annotations

SEARCH_POI_TOOL = {
    "type": "function",
    "function": {
        "name": "search_poi",
        "description": "高德真实 POI 检索（反幻觉核心，地点只能来自这里）。关键字搜索或周边搜索。",
        "parameters": {
            "type": "object",
            "properties": {
                "keywords": {"type": "string", "description": "检索关键词，如「咖啡馆」「烤鸭」"},
                "city": {"type": "string", "description": "城市名，不写死，默认由定位/话语推断"},
                "types": {"type": "string", "description": "POI 类型编码，可选"},
                "location": {
                    "type": "array",
                    "items": {"type": "number"},
                    "description": "[lng, lat]，给定则做周边搜索",
                },
                "radius": {"type": "integer", "description": "周边搜索半径（米）"},
                "sortrule": {"type": "string", "enum": ["distance", "weight"]},
                "page_size": {"type": "integer"},
            },
            "required": ["keywords"],
        },
    },
}

PLAN_ROUTE_TOOL = {
    "type": "function",
    "function": {
        "name": "plan_route",
        "description": "高德路径规划 2.0。出行方式按距离自动选（步行/驾车/公交），支持多途经点，返回耗时/距离/费用/polyline。",
        "parameters": {
            "type": "object",
            "properties": {
                "origin": {"type": "array", "items": {"type": "number"}},
                "destination": {"type": "array", "items": {"type": "number"}},
                "waypoints": {
                    "type": "array",
                    "items": {"type": "array", "items": {"type": "number"}},
                },
                "mode": {"type": "string", "enum": ["auto", "walking", "driving", "transit"]},
                "depart_time": {"type": "string", "description": "该段出发时刻，供时间依赖估时"},
            },
            "required": ["origin", "destination"],
        },
    },
}

DISTANCE_MATRIX_TOOL = {
    "type": "function",
    "function": {
        "name": "distance_matrix",
        "description": "批量取多点两两耗时，用于组合定序时的可行性与排序（便宜）。",
        "parameters": {
            "type": "object",
            "properties": {
                "origins": {"type": "array", "items": {"type": "array", "items": {"type": "number"}}},
                "destinations": {"type": "array", "items": {"type": "array", "items": {"type": "number"}}},
            },
            "required": ["origins", "destinations"],
        },
    },
}

BUILD_NAV_URI_TOOL = {
    "type": "function",
    "function": {
        "name": "build_amap_nav_uri",
        "description": "生成高德导航深链（App 多途经点）与 Web 兜底链接。",
        "parameters": {
            "type": "object",
            "properties": {
                "origin": {"type": "array", "items": {"type": "number"}},
                "destination": {"type": "array", "items": {"type": "number"}},
                "waypoints": {
                    "type": "array",
                    "items": {"type": "array", "items": {"type": "number"}},
                },
                "platform": {"type": "string", "enum": ["android", "ios", "web"]},
            },
            "required": ["origin", "destination"],
        },
    },
}

ALL_TOOLS = [SEARCH_POI_TOOL, PLAN_ROUTE_TOOL, DISTANCE_MATRIX_TOOL, BUILD_NAV_URI_TOOL]
