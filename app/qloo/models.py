"""Normalized views of Qloo API responses.

Qloo returns rich entity objects. The agent only needs a stable subset, so
every client (live or mock) converts responses into these small models. Raw
payloads never reach the LLM or the browser; the ledger stores compact
previews instead.
"""

from __future__ import annotations

from typing import Any, Optional

from pydantic import BaseModel, Field

ENTITY_TYPES = {
    "brand": "urn:entity:brand",
    "artist": "urn:entity:artist",
    "place": "urn:entity:place",
    "podcast": "urn:entity:podcast",
    "movie": "urn:entity:movie",
    "tv_show": "urn:entity:tv_show",
    "book": "urn:entity:book",
    "person": "urn:entity:person",
    "destination": "urn:entity:destination",
    "videogame": "urn:entity:videogame",
}
URN_TO_SHORT = {v: k for k, v in ENTITY_TYPES.items()}


def short_type(urn: Optional[str]) -> str:
    if not urn:
        return "entity"
    return URN_TO_SHORT.get(urn, urn.split(":")[-1])


def to_urn(type_name: str) -> str:
    if type_name.startswith("urn:"):
        return type_name
    return ENTITY_TYPES.get(type_name, f"urn:entity:{type_name}")


class Entity(BaseModel):
    entity_id: str
    name: str
    type: str = "entity"  # short type: brand, artist, place, ...
    popularity: Optional[float] = None
    affinity: Optional[float] = None  # 0..1 when returned by Insights
    disambiguation: Optional[str] = None
    description: Optional[str] = None
    image: Optional[str] = None
    address: Optional[str] = None
    locality: Optional[str] = None
    lat: Optional[float] = None
    lon: Optional[float] = None
    tags: list[str] = Field(default_factory=list)  # tag names (first few)
    # feature.explainability: input entity id -> normalized contribution (0..1)
    explainability: dict[str, float] = Field(default_factory=dict)

    def label(self) -> str:
        return f"{self.name} ({self.type})"


class Tag(BaseModel):
    tag_id: str
    name: str
    type: Optional[str] = None  # e.g. urn:tag:keyword:brand
    affinity: Optional[float] = None
    # Filled by compare_audiences
    a_affinity: Optional[float] = None
    b_affinity: Optional[float] = None
    delta: Optional[float] = None


class Demographics(BaseModel):
    entity_id: str
    age: dict[str, float] = Field(default_factory=dict)
    gender: dict[str, float] = Field(default_factory=dict)


class HeatCell(BaseModel):
    lat: float
    lon: float
    geohash: Optional[str] = None
    affinity: float = 0.0
    affinity_rank: float = 0.0
    popularity: float = 0.0


class TrendPoint(BaseModel):
    entity_id: str
    name: Optional[str] = None
    date: Optional[str] = None
    value: Optional[float] = None
    extra: dict[str, Any] = Field(default_factory=dict)


class QlooError(Exception):
    """Raised for non-retryable or exhausted-retry Qloo failures."""

    def __init__(self, message: str, status: Optional[int] = None, retryable: bool = False):
        super().__init__(message)
        self.status = status
        self.retryable = retryable


class QuotaExceeded(QlooError):
    """Raised by the quota guard before a request is sent."""
