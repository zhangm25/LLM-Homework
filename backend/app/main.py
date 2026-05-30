"""FastAPI entrypoint for RoamMind.

Exposes a single streaming chat endpoint plus small health/config helpers.
Run from the repo root:  uvicorn backend.app.main:app --reload --port 8000
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from typing import AsyncIterator
from urllib.parse import unquote

from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from fastapi.staticfiles import StaticFiles

from .agent.pipeline import plan_stream
from .config import PROJECT_ROOT, get_settings
from .llm.client import get_llm
from .mock.demo_data import SCENARIO_KEYWORDS
from .models.plan import ChatRequest, Plan, RouteSwapRequest
from .planner.scheduler import recompute_plan
from .tools.amap_client import get_amap
from .tools.file_parser import MAX_FILE_BYTES, parse_attachment


@asynccontextmanager
async def lifespan(app: FastAPI):
    yield
    # Tidy the shared AMap HTTP client on shutdown.
    await get_amap().aclose()


settings = get_settings()
app = FastAPI(title="RoamMind", version="0.1.0", lifespan=lifespan)

_origins = settings.cors_origin_list
_allow_all = "*" in _origins
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"] if _allow_all else _origins,
    # "*" + credentials is invalid per the CORS spec; we don't use cookies anyway.
    allow_credentials=not _allow_all,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/api/health")
async def health() -> dict:
    return {"status": "ok"}


@app.get("/api/config")
async def config() -> dict:
    """Lets the frontend show honest capability badges (mock vs live)."""
    llm_health = await get_llm().check_health()
    return {
        "llm_enabled": settings.llm_enabled,
        "llm": llm_health,
        "amap_web_enabled": settings.amap_enabled,
        "default_city": settings.default_city,
        "scenarios": list(SCENARIO_KEYWORDS.keys()),
    }


@app.get("/api/regeo")
async def regeo(lng: float, lat: float) -> dict:
    """Reverse-geocode browser coordinates for the top-bar location label."""
    address = await get_amap().reverse_geocode([lng, lat])
    return {"address": address}


@app.get("/api/geocode")
async def geocode(q: str, city: str | None = None) -> dict:
    """Resolve a typed start location when browser geolocation is unavailable."""
    poi = await get_amap().geocode_place(q, city or settings.default_city)
    if not poi:
        return {"found": False}
    return {
        "found": True,
        "lng": poi.location[0],
        "lat": poi.location[1],
        "label": poi.name,
        "address": poi.address,
    }


@app.get("/api/places")
async def places(q: str, city: str | None = None) -> dict:
    """Candidate places for the manual start picker — the user chooses one,
    instead of us guessing a single (sometimes wrong) geocode result."""
    pois = await get_amap().search_poi_text(q, region=city or settings.default_city, page_size=6)
    return {
        "candidates": [
            {"lng": p.location[0], "lat": p.location[1], "label": p.name, "address": p.address}
            for p in pois
        ]
    }


@app.post("/api/files/parse")
async def parse_file(
    request: Request,
    x_filename: str | None = Header(default=None),
    content_type: str | None = Header(default=None),
) -> dict:
    """Parse an itinerary attachment into compact text/table context.

    The frontend sends raw file bytes instead of multipart so the backend does
    not need python-multipart. Parsed context is passed to /api/chat; files are
    not stored server-side.
    """
    data = await request.body()
    if not data:
        raise HTTPException(status_code=400, detail="文件为空")
    if len(data) > MAX_FILE_BYTES * 2:
        raise HTTPException(status_code=413, detail="文件过大，请上传 12MB 以内的文件")
    ctx = parse_attachment(unquote(x_filename or "attachment"), data, content_type)
    return ctx.model_dump()


@app.post("/api/route")
async def route(req: RouteSwapRequest) -> Plan:
    """Recompute a plan after the user swaps a stop to an alternative — fast,
    deterministic re-route (no LLM). Returns the updated Plan."""
    return await recompute_plan(req.timeline, req.city or settings.default_city, req.intent)


@app.post("/api/chat")
async def chat(req: ChatRequest) -> StreamingResponse:
    """Stream the planning process as SSE (`data: <json>\\n\\n` per event)."""

    async def event_source() -> AsyncIterator[bytes]:
        async for event in plan_stream(req):
            yield f"data: {event.model_dump_json()}\n\n".encode("utf-8")

    return StreamingResponse(
        event_source(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


# Single-port deploy: if the frontend has been built (`npm run build`), serve it
# from "/". API routes above are registered first, so /api/* still wins.
# Mounted last so it only catches everything else (the SPA + its assets).
_DIST = PROJECT_ROOT / "frontend" / "dist"
if _DIST.is_dir():
    app.mount("/", StaticFiles(directory=str(_DIST), html=True), name="frontend")
