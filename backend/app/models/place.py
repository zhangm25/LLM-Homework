"""Intermediate place-resolution state.

The pipeline uses this before scheduling so "what are the actual places?" is
answered separately from "how should the trip move through them?".
"""

from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel, Field


PlaceRole = Literal["start", "end", "fixed", "waypoint", "activity_poi"]
PlaceStatus = Literal["selected", "candidates_ready", "unresolved", "skipped"]


class PlaceCandidate(BaseModel):
    id: str
    name: str
    address: str = ""
    location: list[float]
    rating: Optional[float] = None
    cost: Optional[str] = None
    distance_m: Optional[int] = None
    type: str = ""


class PlaceSlot(BaseModel):
    id: str
    role: PlaceRole
    source_text: str = ""
    query: str = ""
    city: str = ""
    anchor_label: Optional[str] = None
    anchor_location: Optional[list[float]] = None
    around: bool = False
    search_scope: Optional[str] = None
    anchor_policy: Optional[str] = None
    ranking_policy: Optional[str] = None
    status: PlaceStatus = "unresolved"
    candidates: list[PlaceCandidate] = Field(default_factory=list)
    selected: Optional[PlaceCandidate] = None
    needs_user_choice: bool = False
    reason: str = ""


class PlaceResolution(BaseModel):
    status: Literal["places_ready", "partial", "unresolved"] = "unresolved"
    slots: list[PlaceSlot] = Field(default_factory=list)
