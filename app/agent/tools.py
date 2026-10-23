"""Qloo tool box used by the scripted planner and exposed to the LLM.

Each method does one product-level job, records the underlying Qloo calls
in the ledger, and returns plain dicts (what the LLM sees). The per-run cap
prevents an over-eager model from burning quota.
"""

from __future__ import annotations

from typing import Any, Optional

from ..qloo.client import QlooClient
from ..qloo.models import Demographics, Entity, HeatCell, QlooError, Tag
from ..scoring import FitScore, compute_fit, vectors_from_compare, vectors_from_tags
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

    def compare(self, a_id: str, b_id: str) -> tuple[dict[str, float], dict[str, float], dict[str, str], list[str], str]:
        """Tag vectors for A and B. Prefers /v2/analysis/compare; falls back to two tag insights."""
        self._check_cap()
        label = f"compare audiences {self._names([a_id])} vs {self._names([b_id])}"
        try:
            rows = self.client.compare([a_id], [b_id], trace=self.ledger.trace)
            ids = self._absorb(label, [t.name for t in rows])
            a, b, names = vectors_from_compare(rows)
            if a and b:
                return a, b, names, ids, "compare"
        except QlooError as exc:
            if exc.status == 400 or getattr(exc, "status", None) in (401, 403):
                self._absorb(f"{label} (failed)", [])
                raise
            self._absorb(f"{label} (unavailable)", [])
        a_rows, ids_a = self.taste_tags([a_id], take=50)
        b_rows, ids_b = self.taste_tags([b_id], take=50)
        a, b, names = vectors_from_tags(a_rows, b_rows)
        return a, b, names, ids_a + ids_b, "tags"

    def fit(self, a_id: str, b_id: str) -> tuple[FitScore, list[str]]:
        score, groups = self.fit_with_groups(a_id, b_id)
        return score, groups["tags"] + groups["demographics"]

    def fit_with_groups(self, a_id: str, b_id: str) -> tuple[FitScore, dict[str, list[str]]]:
        """Fit score plus the ledger ids behind each input (tags vs demographics)."""
        a_vec, b_vec, names, ids, _ = self.compare(a_id, b_id)
        a_demo, ids_a = self.demographics(a_id)
        b_demo, ids_b = self.demographics(b_id)
        score = compute_fit(a_vec, b_vec, names, a_demo, b_demo)
        return score, {"tags": ids, "demographics": ids_a + ids_b}

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


def classify_pick(entity: Entity, a_id: str, b_id: str) -> str:
    """Use feature.explainability to say who drives a joint recommendation."""
    ex = entity.explainability or {}
    a, b = ex.get(a_id, 0.0), ex.get(b_id, 0.0)
    if not ex or (a == 0 and b == 0):
        return "joint"
    if a >= 0.65:
        return "mostly_a"
    if b >= 0.65:
        return "mostly_b"
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
