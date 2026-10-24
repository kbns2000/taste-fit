"""Qloo tool box used by the scripted planner and exposed to the LLM.

Each method does one product-level job, records the underlying Qloo calls
in the ledger, and returns plain dicts (what the LLM sees). The per-run cap
prevents an over-eager model from burning quota.
"""

from __future__ import annotations

import math
from typing import Any, Optional

from ..qloo.client import QlooClient
from ..qloo.models import Demographics, Entity, HeatCell, QlooError, Tag
from ..scoring import FitScore, compute_fit, vectors_from_tags
from .ledger import Ledger

ACTIVATION_TYPES = {
    "artist": "co-branded playlist, live session or tour tie-in",
    "podcast": "host-read sponsorship or guest episode",
    "place": "pop-up, co-hosted event or retail placement",
    "movie": "screening night or content tie-in",
    "tv_show": "content tie-in or watch party",
    "destination": "travel collab or trip giveaway",
    "book": "reading list, author event or in-store tie-in",
    "videogame": "in-game drop or streamer campaign",
}


class RunCapExceeded(Exception):
    pass


class ToolBox:
    def __init__(self, client: QlooClient, ledger: Ledger, max_calls: int = 16):
        self.client = client
        self.ledger = ledger
        self.max_calls = max_calls
        self.entities: dict[str, Entity] = {}  # id -> resolved entity
        self._demo_cache: dict[str, tuple[Optional[Demographics], list[str]]] = {}
        self._tags_cache: dict[str, tuple[list[Tag], list[str]]] = {}
        self.alternatives: dict[str, list[dict[str, Any]]] = {}  # resolved id -> other search hits

    # -- guard -------------------------------------------------------------------
    def _check_cap(self) -> None:
        if self.ledger.calls() >= self.max_calls:
            raise RunCapExceeded(f"per-run Qloo call cap of {self.max_calls} reached")

    def _absorb(self, purpose: str, names: list[str]) -> list[str]:
        return self.ledger.absorb(purpose, names)

    def _call(self, purpose: str, fn):
        """Run a client call; a failed call is still filed in the ledger under its own purpose."""
        try:
            return fn()
        except QlooError:
            self._absorb(f"{purpose} (failed)", [])
            raise

    # -- entity lookup -----------------------------------------------------------
    def resolve(self, query: str, type_hint: Optional[str] = None) -> Optional[Entity]:
        """Find the best-matching Qloo entity for a free-text name."""
        self._check_cap()
        types = [type_hint] if type_hint and type_hint != "any" else None
        label = f"resolve '{query}'"
        results = self._call(label, lambda: self.client.search(query, types=types, take=5, trace=self.ledger.trace))
        self._absorb(label, [e.name for e in results])
        if not results:
            return None
        preferred = ("brand", "artist", "place", "podcast", "destination", "tv_show", "movie", "book", "person", "videogame")
        results.sort(key=lambda e: (preferred.index(e.type) if e.type in preferred else 99, -(e.popularity or 0)))
        best = results[0]
        self.entities[best.entity_id] = best
        self.alternatives[best.entity_id] = [
            {"name": e.name, "type": e.type, "disambiguation": e.disambiguation} for e in results[1:4]]
        return best

    def candidates(self, query: str, type_hint: Optional[str] = None, take: int = 5) -> list[Entity]:
        self._check_cap()
        types = [type_hint] if type_hint and type_hint != "any" else None
        label = f"search '{query}'"
        results = self._call(label, lambda: self.client.search(query, types=types, take=take, trace=self.ledger.trace))
        self._absorb(label, [e.name for e in results])
        for e in results:
            self.entities[e.entity_id] = e
        return results

    # -- taste profile -----------------------------------------------------------
    def taste_tags(self, entity_ids: list[str], take: int = 30) -> tuple[list[Tag], list[str]]:
        self._check_cap()
        label = f"taste tags for {self._names(entity_ids)}"
        rows = self._call(label, lambda: self.client.taste_tags(entity_ids, take=take, trace=self.ledger.trace))
        ids = self._absorb(label, [t.name for t in rows])
        return rows, ids

    def demographics(self, entity_id: str) -> tuple[Optional[Demographics], list[str]]:
        if entity_id in self._demo_cache:  # same run, same entity: reuse (and re-cite) the call
            return self._demo_cache[entity_id]
        self._check_cap()
        label = f"demographics for {self._names([entity_id])}"
        rows = self._call(label, lambda: self.client.demographics([entity_id], trace=self.ledger.trace))
        ids = self._absorb(label, [])
        self._demo_cache[entity_id] = ((rows[0] if rows else None), ids)
        return self._demo_cache[entity_id]

    def audience_tags(self, a_id: str, b_id: str) -> tuple[dict[str, float], dict[str, float], dict[str, str], list[str]]:
        """What each audience over-indexes on: one `urn:tag` insight per entity (cross-domain by design)."""
        a_rows, ids_a = self._audience_tags_for(a_id)
        b_rows, ids_b = self._audience_tags_for(b_id)
        a, b, names = vectors_from_tags(a_rows, b_rows)
        return a, b, names, ids_a + ids_b

    def _audience_tags_for(self, entity_id: str) -> tuple[list[Tag], list[str]]:
        # Partner scout scores several candidates against the same A: fetch (and cite) A's tags once per run.
        if entity_id not in self._tags_cache:
            self._tags_cache[entity_id] = self.taste_tags([entity_id], take=50)
        return self._tags_cache[entity_id]

    def catalog_overlap(self, a_id: str, b_id: str) -> tuple[dict[str, Any], list[str]]:
        """`/v2/analysis/compare`: catalog tags the two entities themselves share (what they are, not who likes them)."""
        self._check_cap()
        label = f"compare catalog tags {self._names([a_id])} vs {self._names([b_id])}"
        result = self._call(label, lambda: self.client.compare([a_id], [b_id], trace=self.ledger.trace))
        ids = self._absorb(label, [t.name for t in result.common][:20])
        summary = {
            "common": [t.name for t in result.common[:12]],
            "common_count": len(result.common),
            "a_count": len(result.a_tags),
            "b_count": len(result.b_tags),
        }
        return summary, ids

    def affinity_among(self, signal_id: str, target_ids: list[str]) -> tuple[dict[str, float], list[str]]:
        """Qloo affinity of each target among fans of `signal_id` (`filter.results.entities`).

        Targets must share one entity type (one query per type). On failure the call is filed as
        "(failed)", nothing is cited, and an empty map is returned, so the score falls back to tag overlap.
        """
        if not target_ids:
            return {}, []
        target_type = self.entities[target_ids[0]].type if target_ids[0] in self.entities else "brand"
        self._check_cap()
        label = f"affinity of {self._names(target_ids)} among fans of {self._names([signal_id])}"
        try:
            out = self._call(label, lambda: self.client.affinity_among([signal_id], target_ids, target_type,
                                                                       trace=self.ledger.trace))
        except QlooError as exc:
            if exc.status in (401, 403):
                raise
            return {}, []
        ids = self._absorb(label, [self.entities[t].name for t in out if t in self.entities])
        return out, ids

    def fit(self, a_id: str, b_id: str, a_to_b: Optional[tuple[Optional[float], list[str]]] = None
            ) -> tuple[FitScore, list[str]]:
        score, groups = self.fit_with_groups(a_id, b_id, a_to_b)
        return score, groups["affinity"] + groups["tags"] + groups["demographics"]

    def fit_with_groups(self, a_id: str, b_id: str, a_to_b: Optional[tuple[Optional[float], list[str]]] = None
                        ) -> tuple[FitScore, dict[str, list[str]]]:
        """Fit score plus the ledger ids behind each input (cross-affinity, tags, demographics).

        `a_to_b` lets partner scout pass B's affinity among A's fans from one batched call.
        """
        a_vec, b_vec, names, ids = self.audience_tags(a_id, b_id)
        if a_to_b is None:
            m, ids_ab = self.affinity_among(a_id, [b_id])
            a_to_b = (m.get(b_id), ids_ab)
        m, ids_ba = self.affinity_among(b_id, [a_id])
        a_demo, ids_a = self.demographics(a_id)
        b_demo, ids_b = self.demographics(b_id)
        score = compute_fit(a_vec, b_vec, names, a_demo, b_demo, a_to_b=a_to_b[0], b_to_a=m.get(a_id))
        return score, {"affinity": a_to_b[1] + ids_ba, "tags": ids, "demographics": ids_a + ids_b}

    # -- activations -------------------------------------------------------------
    def recommend(self, entity_ids: list[str], target_type: str, take: int = 8,
                  city: Optional[str] = None, filter_tags: Optional[list[str]] = None,
                  exclude_ids: Optional[list[str]] = None) -> tuple[list[Entity], list[str]]:
        self._check_cap()
        filter_location = city if target_type == "place" and city else None
        signal_location = city if target_type != "place" and city else None
        label = f"{target_type}s for fans of {self._names(entity_ids)}" + (f" in {city}" if city else "")
        rows = self._call(label, lambda: self.client.recommend(
            entity_ids, target_type, take=take, filter_location=filter_location,
            signal_location=signal_location, filter_tags=filter_tags,
            explainability=len(entity_ids) > 1, exclude_ids=exclude_ids, trace=self.ledger.trace))
        ids = self._absorb(label, [e.name for e in rows])
        for e in rows:
            self.entities.setdefault(e.entity_id, e)
        return rows, ids

    def heatmap(self, entity_ids: list[str], city: str) -> tuple[list[HeatCell], list[str]]:
        self._check_cap()
        label = f"heatmap of {self._names(entity_ids)} fans in {city}"
        cells = self._call(label, lambda: self.client.heatmap(entity_ids, city, trace=self.ledger.trace))
        ids = self._absorb(label, [])
        return cells, ids

    def find_tags(self, query: str) -> tuple[list[Tag], list[str]]:
        self._check_cap()
        label = f"find tags '{query}'"
        rows = self._call(label, lambda: self.client.find_tags(query, take=10, trace=self.ledger.trace))
        ids = self._absorb(label, [t.name for t in rows])
        return rows, ids

    # -- helpers -----------------------------------------------------------------
    def _names(self, ids: list[str]) -> str:
        return " + ".join(self.entities[i].name if i in self.entities else i[:8] for i in ids)


# Live explainability splits for two signals cluster near 50/50 (0.41-0.59 seen on 2026-10-24),
# so a 55/45 split already means one audience pulls ~20% harder than the other.
LEAN_SHARE = 0.55
MOSTLY_SHARE = 0.65


def classify_pick(entity: Entity, a_id: str, b_id: str) -> str:
    """Use feature.explainability to say who drives a joint recommendation."""
    ex = entity.explainability or {}
    a, b = ex.get(a_id, 0.0), ex.get(b_id, 0.0)
    if not ex or (a + b) <= 0:
        return "joint"
    share_a = a / (a + b)
    if share_a >= MOSTLY_SHARE:
        return "mostly_a"
    if share_a <= 1 - MOSTLY_SHARE:
        return "mostly_b"
    if share_a >= LEAN_SHARE:
        return "leans_a"
    if share_a <= 1 - LEAN_SHARE:
        return "leans_b"
    return "both"


def pick_summary(entity: Entity, a_id: str, b_id: str) -> dict[str, Any]:
    return {
        "entity_id": entity.entity_id,
        "name": entity.name,
        "type": entity.type,
        "affinity": entity.affinity,
        "popularity": entity.popularity,
        "driver": classify_pick(entity, a_id, b_id),
        "explainability": entity.explainability,
        "tags": entity.tags[:4],
        "locality": entity.locality,
        "address": entity.address,
        "lat": entity.lat,
        "lon": entity.lon,
        "image": entity.image,
        "format": ACTIVATION_TYPES.get(entity.type, "partnership tie-in"),
    }


# Qloo can resolve a city name to a wider area ("New York" -> the whole state, 3,557 cells on 2026-10-24),
# so the map keeps the cells within this distance of the city centre.
CITY_RADIUS_KM = 30.0


def _km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Great-circle distance in km."""
    p1, p2 = math.radians(lat1), math.radians(lat2)
    h = math.sin((p2 - p1) / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(math.radians(lon2 - lon1) / 2) ** 2
    return 2 * 6371.0 * math.asin(math.sqrt(min(1.0, h)))


def city_center(cells: list[HeatCell], places: list[Entity]) -> Optional[tuple[float, float]]:
    """Median of the recommended places (3 or more), else the busiest heatmap cell."""
    pts = [(p.lat, p.lon) for p in places if p.lat is not None and p.lon is not None]
    if len(pts) >= 3:
        lats, lons = sorted(x for x, _ in pts), sorted(y for _, y in pts)
        return lats[len(lats) // 2], lons[len(lons) // 2]
    if cells:
        top = max(cells, key=lambda c: (c.popularity, c.affinity))
        return top.lat, top.lon
    return None


def clip_to_city(cells: list[HeatCell], places: list[Entity],
                 radius_km: float = CITY_RADIUS_KM) -> tuple[list[HeatCell], dict[str, Any]]:
    """Heatmap cells within `radius_km` of the city centre, plus what was kept (shown under the map)."""
    center = city_center(cells, places)
    kept = [c for c in cells if center and _km(c.lat, c.lon, *center) <= radius_km] or cells
    return kept, {"total": len(cells), "kept": len(kept), "radius_km": radius_km if center else None,
                  "center": [round(center[0], 4), round(center[1], 4)] if center else None}


def hot_zones(cells: list[HeatCell], places: list[Entity], top: int = 3) -> list[dict[str, Any]]:
    """Cluster the strongest heatmap cells and name them by the nearest recommended place."""
    if not cells:
        return []
    ranked = sorted(cells, key=lambda c: -c.affinity)
    zones: list[dict[str, Any]] = []
    for cell in ranked:
        if any(abs(cell.lat - z["lat"]) < 0.02 and abs(cell.lon - z["lon"]) < 0.025 for z in zones):
            continue
        nearest = None
        best = 1e9
        for p in places:
            if p.lat is None or p.lon is None:
                continue
            d = (p.lat - cell.lat) ** 2 + (p.lon - cell.lon) ** 2
            if d < best:
                best, nearest = d, p
        zones.append({
            "lat": cell.lat, "lon": cell.lon, "affinity": cell.affinity, "popularity": cell.popularity,
            "geohash": cell.geohash,
            "near": nearest.name if nearest and best < 0.0009 else None,
            "near_area": (nearest.disambiguation if nearest and best < 0.0009 else None),
        })
        if len(zones) >= top:
            break
    return zones


# JSON schemas the LLM sees (strict: every property required, no extras) ---------
def _schema(props: dict[str, Any]) -> dict[str, Any]:
    return {"type": "object", "properties": props, "required": list(props), "additionalProperties": False}


LLM_TOOL_SPECS: list[dict[str, Any]] = [
    {
        "name": "recommend",
        "description": ("Ask Qloo for entities that fans of the given signal entities over-index on. "
                        "Use it to find extra activation partners of one type (artist, podcast, place, movie, "
                        "tv_show, destination, book, videogame). With a city, places are limited to that city."),
        "input_schema": _schema({
            "target_type": {"type": "string", "enum": list(ACTIVATION_TYPES)},
            "city": {"type": ["string", "null"], "description": "City name or null"},
            "take": {"type": "integer", "minimum": 1, "maximum": 10},
        }),
    },
    {
        "name": "heatmap",
        "description": "Where in a city the joint audience concentrates (Qloo heatmap). Returns the top zones.",
        "input_schema": _schema({"city": {"type": "string"}}),
    },
    {
        "name": "find_tags",
        "description": "Look up Qloo taste tags matching a keyword (to check whether a theme exists in Qloo's vocabulary).",
        "input_schema": _schema({"query": {"type": "string"}}),
    },
]

FINALIZE_TOOL: dict[str, Any] = {
    "name": "finalize_brief",
    "description": ("Submit the final partnership brief. Every claim must cite ledger ids like Q3. "
                    "Only name entities that appear in the evidence you were given or fetched."),
    "input_schema": _schema({
        "headline": {"type": "string", "description": "One sentence verdict, <= 20 words"},
        "verdict": {"type": "string", "enum": ["go", "go_with_conditions", "no_go"]},
        "why": {"type": "array", "minItems": 2, "maxItems": 5, "items": _schema({
            "text": {"type": "string"},
            "cites": {"type": "array", "items": {"type": "string"}},
        })},
        "target_segments": {"type": "array", "maxItems": 4, "items": {"type": "string"}},
        "message_angles": {"type": "array", "minItems": 2, "maxItems": 4, "items": {"type": "string"}},
        "channels": {"type": "array", "minItems": 2, "maxItems": 5, "items": {"type": "string"}},
        "activations": {"type": "array", "minItems": 2, "maxItems": 5, "items": _schema({
            "title": {"type": "string"},
            "format": {"type": "string"},
            "entities": {"type": "array", "items": {"type": "string"}, "description": "Exact entity names from evidence"},
            "rationale": {"type": "string"},
            "cites": {"type": "array", "items": {"type": "string"}},
        })},
        "risks": {"type": "array", "minItems": 1, "maxItems": 4, "items": {"type": "string"}},
        "next_steps": {"type": "array", "minItems": 2, "maxItems": 4, "items": {"type": "string"}},
    }),
}
