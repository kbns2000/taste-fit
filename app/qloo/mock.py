"""Deterministic mock of the Qloo Insights API.

It serves the same `QlooClient` contract as the live adapter, from the small
demo world in `fixtures.json`. Affinities are cosine similarities over tag
vectors, demographics come straight from the fixtures, and the heatmap is a
grid around each neighbourhood of the requested city. The point is to make
the whole agent flow reproducible without a key (tests, CI, offline demos)
while keeping the *shape* of every answer identical to the live API so the
rest of the product cannot tell the difference except for the `source`
badge.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
import time
from functools import lru_cache
from pathlib import Path
from typing import Any, Callable, Optional

from .client import CallRecord
from .models import Demographics, Entity, HeatCell, Tag, TrendPoint, short_type, to_urn

FIXTURES_PATH = Path(__file__).with_name("fixtures.json")

_GEOHASH_BASE32 = "0123456789bcdefghjkmnpqrstuvwxyz"


def geohash_encode(lat: float, lon: float, precision: int = 7) -> str:
    """Minimal geohash encoder (enough to label heatmap cells like the API does)."""
    lat_range = [-90.0, 90.0]
    lon_range = [-180.0, 180.0]
    bits = []
    even = True
    while len(bits) < precision * 5:
        if even:
            mid = (lon_range[0] + lon_range[1]) / 2
            if lon >= mid:
                bits.append(1)
                lon_range[0] = mid
            else:
                bits.append(0)
                lon_range[1] = mid
        else:
            mid = (lat_range[0] + lat_range[1]) / 2
            if lat >= mid:
                bits.append(1)
                lat_range[0] = mid
            else:
                bits.append(0)
                lat_range[1] = mid
        even = not even
    out = []
    for i in range(0, len(bits), 5):
        chunk = bits[i:i + 5]
        value = 0
        for b in chunk:
            value = (value << 1) | b
        out.append(_GEOHASH_BASE32[value])
    return "".join(out)


def _norm(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", text.lower()).strip()


def _cosine(a: dict[str, float], b: dict[str, float]) -> float:
    if not a or not b:
        return 0.0
    dot = sum(w * b.get(k, 0.0) for k, w in a.items())
    na = math.sqrt(sum(w * w for w in a.values()))
    nb = math.sqrt(sum(w * w for w in b.values()))
    if na == 0 or nb == 0:
        return 0.0
    return dot / (na * nb)


def _jitter(*parts: str, scale: float = 0.02) -> float:
    """Stable tiny perturbation so ties resolve the same way on every run."""
    digest = hashlib.md5("|".join(parts).encode("utf-8")).hexdigest()
    return (int(digest[:8], 16) / 0xFFFFFFFF - 0.5) * scale


def _mean_vec(vecs: list[dict[str, float]]) -> dict[str, float]:
    out: dict[str, float] = {}
    if not vecs:
        return out
    for vec in vecs:
        for k, w in vec.items():
            out[k] = out.get(k, 0.0) + w / len(vecs)
    return out


@lru_cache(maxsize=1)
def load_world(path: str = str(FIXTURES_PATH)) -> dict[str, Any]:
    with open(path, encoding="utf-8") as fh:
        world = json.load(fh)
    world["by_id"] = {e["entity_id"]: e for e in world["entities"]}
    return world


class MockQlooClient:
    """Offline stand-in for `LiveQlooClient` with identical method signatures."""

    source = "mock"

    def __init__(self, world: Optional[dict[str, Any]] = None, latency_ms: int = 0):
        self.world = world or load_world()
        self.latency_ms = latency_ms
        self.calls = 0

    # -- bookkeeping -----------------------------------------------------------
    def budget_status(self) -> dict[str, int]:
        return {"calls_today": self.calls, "daily_budget": 0}

    def _record(self, endpoint: str, params: dict[str, Any], items: list, trace: Optional[list],
                started: float, preview: Callable[[list], list[str]]) -> None:
        self.calls += 1
        if trace is not None:
            trace.append(CallRecord(
                endpoint=endpoint,
                params=params,
                status="ok" if items else "empty",
                count=len(items),
                duration_ms=int((time.perf_counter() - started) * 1000) + self.latency_ms,
                cached=False,
                source=self.source,
                preview=preview(items)[:6],
            ))

    # -- helpers -----------------------------------------------------------------
    def _raw(self, entity_id: str) -> Optional[dict[str, Any]]:
        return self.world["by_id"].get(entity_id)

    def _signal_vec(self, entity_ids: list[str]) -> dict[str, float]:
        vecs = [self._raw(i)["tags"] for i in entity_ids if self._raw(i)]
        return _mean_vec(vecs)

    def _tag_meta(self, tag_id: str) -> dict[str, Any]:
        return self.world["tags"].get(tag_id) or {"name": tag_id.split(":")[-1].replace("_", " ").title(),
                                                  "type": ":".join(tag_id.split(":")[:-1])}

    def _entity(self, raw: dict[str, Any], affinity: Optional[float] = None,
                explain: Optional[dict[str, float]] = None) -> Entity:
        top_tags = sorted(raw["tags"].items(), key=lambda kv: -kv[1])[:5]
        return Entity(
            entity_id=raw["entity_id"],
            name=raw["name"],
            type=raw["type"],
            popularity=raw.get("popularity"),
            affinity=None if affinity is None else round(max(0.0, min(1.0, affinity)), 4),
            disambiguation=raw.get("neighborhood") or raw.get("locality") or None,
            description=raw.get("description") or None,
            address=raw.get("address"),
            locality=raw.get("locality"),
            lat=raw.get("lat"),
            lon=raw.get("lon"),
            tags=[self._tag_meta(t)["name"] for t, _ in top_tags],
            explainability=explain or {},
        )

    def _locality(self, query: Optional[str]) -> Optional[dict[str, Any]]:
        if not query:
            return None
        q = _norm(query)
        for key, loc in self.world["localities"].items():
            names = [key, _norm(loc["name"])] + [_norm(a) for a in loc.get("akas", [])]
            if q in names or any(q.startswith(n) or n.startswith(q) for n in names if len(n) > 2):
                return loc
        return None

    # -- lookup routes -----------------------------------------------------------
    def search(self, query: str, types: Optional[list[str]] = None, take: int = 8,
               trace: Optional[list] = None) -> list[Entity]:
        started = time.perf_counter()
        q = _norm(query)
        wanted = {short_type(to_urn(t)) for t in types} if types else None
        scored: list[tuple[float, dict[str, Any]]] = []
        for raw in self.world["entities"]:
            if wanted and raw["type"] not in wanted:
                continue
            names = [_norm(raw["name"])] + [_norm(a) for a in raw.get("akas", [])]
            score = 0.0
            for n in names:
                if not n:
                    continue
                if n == q:
                    score = max(score, 1.0)
                elif n.startswith(q) or q.startswith(n):
                    score = max(score, 0.8)
                elif q in n or n in q:
                    score = max(score, 0.6)
                else:
                    qt, nt = set(q.split()), set(n.split())
                    if qt and nt and len(qt & nt) >= max(1, len(qt) / 2):
                        score = max(score, 0.4 * len(qt & nt) / len(qt | nt))
            if score > 0:
                scored.append((score + 0.05 * (raw.get("popularity") or 0), raw))
        scored.sort(key=lambda sr: -sr[0])
        items = [self._entity(raw) for _, raw in scored[:max(1, min(take, 20))]]
        params: dict[str, Any] = {"query": query, "take": take}
        if types:
            params["types"] = [to_urn(t) for t in types]
        self._record("/search", params, items, trace, started, lambda xs: [e.name for e in xs])
        return items

    def entities(self, entity_ids: list[str], trace: Optional[list] = None) -> list[Entity]:
        started = time.perf_counter()
        items = [self._entity(self._raw(i)) for i in entity_ids if self._raw(i)]
        self._record("/entities", {"entity_ids": entity_ids}, items, trace, started, lambda xs: [e.name for e in xs])
        return items

    def find_tags(self, query: str, take: int = 10, trace: Optional[list] = None) -> list[Tag]:
        started = time.perf_counter()
        q = _norm(query)
        items = []
        for tag_id, meta in self.world["tags"].items():
            name = _norm(meta["name"])
            if q and (q in name or name in q or q in tag_id):
                items.append(Tag(tag_id=tag_id, name=meta["name"], type=meta["type"]))
        items = items[:min(take, 50)]
        self._record("/v2/tags", {"filter.query": query, "take": take}, items, trace, started,
                     lambda xs: [t.name for t in xs])
        return items

    # -- insights ----------------------------------------------------------------
    def taste_tags(self, entity_ids: list[str], take: int = 30, tag_types: Optional[list[str]] = None,
                   trace: Optional[list] = None) -> list[Tag]:
        started = time.perf_counter()
        vec = self._signal_vec(entity_ids)
        rows = []
        for tag_id, weight in vec.items():
            meta = self._tag_meta(tag_id)
            if tag_types and not any(meta["type"].startswith(t) for t in tag_types):
                continue
            affinity = max(0.0, min(1.0, 0.55 + 0.45 * weight + _jitter(tag_id, *entity_ids)))
            rows.append(Tag(tag_id=tag_id, name=meta["name"], type=meta["type"], affinity=round(affinity, 4)))
        rows.sort(key=lambda t: -(t.affinity or 0))
        rows = rows[:min(take, 50)]
        params: dict[str, Any] = {"filter.type": "urn:tag", "signal.interests.entities": entity_ids, "take": take}
        if tag_types:
            params["filter.tag.types"] = tag_types
        self._record("/v2/insights", params, rows, trace, started, lambda xs: [t.name for t in xs])
        return rows

    def demographics(self, entity_ids: list[str], trace: Optional[list] = None) -> list[Demographics]:
        started = time.perf_counter()
        items = []
        for eid in entity_ids:
            raw = self._raw(eid)
            if not raw:
                continue
            dm = raw.get("demographics") or {}
            items.append(Demographics(entity_id=eid, age=dict(dm.get("age") or {}), gender=dict(dm.get("gender") or {})))
        params = {"filter.type": "urn:demographics", "signal.interests.entities": entity_ids}
        self._record("/v2/insights", params, items, trace, started, lambda xs: [d.entity_id[:8] for d in xs])
        return items

    def recommend(self, entity_ids: list[str], target_type: str, take: int = 10,
                  filter_location: Optional[str] = None, signal_location: Optional[str] = None,
                  filter_tags: Optional[list[str]] = None, explainability: bool = True,
                  exclude_ids: Optional[list[str]] = None,
                  trace: Optional[list] = None) -> list[Entity]:
        started = time.perf_counter()
        target = short_type(to_urn(target_type))
        signal_vecs = {i: self._raw(i)["tags"] for i in entity_ids if self._raw(i)}
        vec = _mean_vec(list(signal_vecs.values()))
        excluded = set(entity_ids) | set(exclude_ids or [])
        loc_filter = self._locality(filter_location)
        loc_signal = self._locality(signal_location)
        want_explain = explainability and len(signal_vecs) > 1
        scored: list[tuple[float, dict[str, Any], dict[str, float]]] = []
        for raw in self.world["entities"]:
            if raw["type"] != target or raw["entity_id"] in excluded:
                continue
            if filter_location:
                if not loc_filter or _norm(raw.get("locality") or "") != _norm(loc_filter["name"]):
                    continue
            if filter_tags and not any(t in raw["tags"] for t in filter_tags):
                continue
            # map cosine (0..1) onto the 0.45..1 band the live API tends to return
            affinity = 0.45 + 0.55 * _cosine(vec, raw["tags"])
            if loc_signal and _norm(raw.get("locality") or "") == _norm(loc_signal["name"]):
                affinity = min(1.0, affinity + 0.05)
            affinity = max(0.0, min(1.0, affinity + 0.1 * (raw.get("popularity") or 0) * 0.3 + _jitter(raw["entity_id"], *entity_ids)))
            explain: dict[str, float] = {}
            if want_explain:
                parts = {sid: max(0.0, _cosine(svec, raw["tags"])) for sid, svec in signal_vecs.items()}
                total = sum(parts.values()) or 1.0
                explain = {sid: round(v / total, 3) for sid, v in parts.items()}
            scored.append((affinity, raw, explain))
        scored.sort(key=lambda sre: -sre[0])
        items = [self._entity(raw, affinity=aff, explain=ex) for aff, raw, ex in scored[:min(take, 50)]]
        params: dict[str, Any] = {"filter.type": to_urn(target_type), "signal.interests.entities": entity_ids, "take": take}
        if filter_location:
            params["filter.location.query"] = filter_location
        if signal_location:
            params["signal.location.query"] = signal_location
        if filter_tags:
            params["filter.tags"] = filter_tags
        if exclude_ids:
            params["filter.exclude.entities"] = exclude_ids
        if want_explain:
            params["feature.explainability"] = True
        self._record("/v2/insights", params, items, trace, started, lambda xs: [e.name for e in xs])
        return items

    def heatmap(self, entity_ids: list[str], location_query: str,
                trace: Optional[list] = None) -> list[HeatCell]:
        started = time.perf_counter()
        loc = self._locality(location_query)
        vec = self._signal_vec(entity_ids)
        cells: list[HeatCell] = []
        if loc:
            for hood in loc["neighborhoods"]:
                base = 0.3 + 0.7 * max(0.0, _cosine(vec, hood["tags"]))
                # a 3x3 block of ~900 m cells around each neighbourhood centre
                for dy in (-1, 0, 1):
                    for dx in (-1, 0, 1):
                        decay = 1.0 - 0.18 * (abs(dx) + abs(dy))
                        lat = hood["lat"] + dy * 0.008
                        lon = hood["lon"] + dx * 0.010
                        affinity = max(0.0, min(1.0, base * decay + _jitter(hood["name"], str(dx), str(dy), *entity_ids, scale=0.04)))
                        cells.append(HeatCell(
                            lat=round(lat, 5), lon=round(lon, 5), geohash=geohash_encode(lat, lon),
                            affinity=round(affinity, 4), popularity=round(hood["popularity"] * decay, 4),
                        ))
            ranked = sorted(cells, key=lambda c: c.affinity)
            for rank, cell in enumerate(ranked):
                cell.affinity_rank = round((rank + 1) / len(ranked), 4)
        params = {"filter.type": "urn:heatmap", "filter.location.query": location_query,
                  "signal.interests.entities": entity_ids}
        self._record("/v2/insights", params, cells, trace, started, lambda xs: [f"{len(xs)} cells"])
        return cells

    def compare(self, a_ids: list[str], b_ids: list[str], trace: Optional[list] = None) -> list[Tag]:
        started = time.perf_counter()
        a_vec, b_vec = self._signal_vec(a_ids), self._signal_vec(b_ids)
        rows = []
        for tag_id in sorted(set(a_vec) | set(b_vec)):
            meta = self._tag_meta(tag_id)
            a_aff = max(0.0, min(1.0, 0.55 + 0.45 * a_vec.get(tag_id, 0.0) + _jitter(tag_id, *a_ids))) if tag_id in a_vec else 0.0
            b_aff = max(0.0, min(1.0, 0.55 + 0.45 * b_vec.get(tag_id, 0.0) + _jitter(tag_id, *b_ids))) if tag_id in b_vec else 0.0
            rows.append(Tag(tag_id=tag_id, name=meta["name"], type=meta["type"],
                            affinity=round((a_aff + b_aff) / 2, 4), a_affinity=round(a_aff, 4),
                            b_affinity=round(b_aff, 4), delta=round(a_aff - b_aff, 4)))
        rows.sort(key=lambda t: -(t.affinity or 0))
        params = {"a.signal.interests.entities": a_ids, "b.signal.interests.entities": b_ids}
        self._record("/v2/analysis/compare", params, rows, trace, started, lambda xs: [t.name for t in xs])
        return rows

    def trending(self, entity_ids: list[str], target_type: str, start_date: str, end_date: str,
                 take: int = 10, trace: Optional[list] = None) -> list[TrendPoint]:
        started = time.perf_counter()
        recs = self.recommend(entity_ids, target_type, take=take, explainability=False)
        items = [TrendPoint(entity_id=e.entity_id, name=e.name, date=end_date,
                            value=round(0.5 + 0.5 * (e.affinity or 0) * (e.popularity or 0.5), 4))
                 for e in recs]
        params = {"signal.interests.entities": entity_ids, "filter.type": to_urn(target_type),
                  "filter.start_date": start_date, "filter.end_date": end_date, "take": take}
        self._record("/v2/trending", params, items, trace, started, lambda xs: [p.name or "" for p in xs])
        return items
