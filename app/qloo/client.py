"""Qloo Insights API adapter (live REST client) and the shared client contract.

Design rules (from the hackathon kit's API_ACCESS and SAFE_USE docs):
- the key only ever travels in the `X-Api-Key` header from this server;
- requests are the smallest that answer the product question (`take` is
  bounded, explainability is requested only where it is used);
- retries are bounded and 429s honour `Retry-After`;
- a per-process daily budget and a per-run call cap keep the hosted demo
  inside the event quota;
- responses are cached in memory for a few hours so repeated demo runs do
  not re-spend quota.

Every public method accepts a `trace` list. When provided, a `CallRecord`
describing the request (endpoint, redacted params, result count, duration,
cache hit) is appended, which is how the agent builds its evidence ledger.
"""

from __future__ import annotations

import datetime as _dt
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Iterable, Optional, Protocol

import httpx

from .models import (
    Demographics,
    Entity,
    HeatCell,
    QlooError,
    QuotaExceeded,
    Tag,
    TrendPoint,
    short_type,
    to_urn,
)


@dataclass
class CallRecord:
    endpoint: str
    params: dict[str, Any]
    status: str = "ok"  # ok | empty | error | cached
    count: int = 0
    duration_ms: int = 0
    cached: bool = False
    source: str = "live"
    error: Optional[str] = None
    preview: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            "endpoint": self.endpoint,
            "params": self.params,
            "status": self.status,
            "count": self.count,
            "duration_ms": self.duration_ms,
            "cached": self.cached,
            "source": self.source,
            "error": self.error,
            "preview": self.preview,
        }


class QlooClient(Protocol):
    """Contract shared by the live client and the mock."""

    source: str

    def search(self, query: str, types: Optional[list[str]] = None, take: int = 8,
               trace: Optional[list] = None) -> list[Entity]: ...

    def entities(self, entity_ids: list[str], trace: Optional[list] = None) -> list[Entity]: ...

    def taste_tags(self, entity_ids: list[str], take: int = 30,
                   tag_types: Optional[list[str]] = None,
                   trace: Optional[list] = None) -> list[Tag]: ...

    def demographics(self, entity_ids: list[str], trace: Optional[list] = None) -> list[Demographics]: ...

    def recommend(self, entity_ids: list[str], target_type: str, take: int = 10,
                  filter_location: Optional[str] = None, signal_location: Optional[str] = None,
                  filter_tags: Optional[list[str]] = None, explainability: bool = True,
                  exclude_ids: Optional[list[str]] = None,
                  trace: Optional[list] = None) -> list[Entity]: ...

    def heatmap(self, entity_ids: list[str], location_query: str,
                trace: Optional[list] = None) -> list[HeatCell]: ...

    def compare(self, a_ids: list[str], b_ids: list[str], trace: Optional[list] = None) -> list[Tag]: ...

    def find_tags(self, query: str, take: int = 10, trace: Optional[list] = None) -> list[Tag]: ...

    def trending(self, entity_ids: list[str], target_type: str, start_date: str, end_date: str,
                 take: int = 10, trace: Optional[list] = None) -> list[TrendPoint]: ...


# --------------------------------------------------------------------------
# Response normalisation (shared by live client; the mock builds models directly)
# --------------------------------------------------------------------------

def _num(value: Any) -> Optional[float]:
    try:
        if value is None:
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def _first_image(props: dict) -> Optional[str]:
    image = props.get("image")
    if isinstance(image, dict):
        return image.get("url")
    if isinstance(image, str):
        return image
    return None


def _entity_from_raw(raw: dict, signal_ids: Optional[Iterable[str]] = None) -> Optional[Entity]:
    if not isinstance(raw, dict):
        return None
    entity_id = raw.get("entity_id") or raw.get("id")
    name = raw.get("name")
    if not entity_id or not name:
        return None
    subtype = raw.get("subtype")
    if not subtype:
        types = raw.get("types") or []
        subtype = next((t for t in types if isinstance(t, str) and t.startswith("urn:entity:")), None)
    props = raw.get("properties") or {}
    query = raw.get("query") or {}
    geocode = props.get("geocode") or {}
    loc = raw.get("location") or props.get("location") or {}
    lat = _num(loc.get("lat") if isinstance(loc, dict) else None)
    lon = _num(loc.get("lon") if isinstance(loc, dict) else None)
    if lon is None and isinstance(loc, dict):
        lon = _num(loc.get("lng"))
    tags = []
    for tag in raw.get("tags") or []:
        if isinstance(tag, dict) and tag.get("name"):
            tags.append(str(tag["name"]))
        if len(tags) >= 8:
            break
    explain: dict[str, float] = {}
    exp = query.get("explainability")
    if isinstance(exp, dict):
        # Shapes seen in the wild: {"signal.interests.entities": {id: score}}
        # or {id: score}. Pick numeric leaves keyed by entity ids.
        candidates = [exp] + [v for v in exp.values() if isinstance(v, dict)]
        wanted = set(s.lower() for s in (signal_ids or []))
        for cand in candidates:
            for key, value in cand.items():
                score = _num(value)
                if score is None:
                    continue
                if not wanted or key.lower() in wanted:
                    explain[key] = score
            if explain:
                break
    return Entity(
        entity_id=str(entity_id),
        name=str(name),
        type=short_type(subtype),
        popularity=_num(raw.get("popularity")),
        affinity=_num(query.get("affinity")),
        disambiguation=raw.get("disambiguation"),
        description=(props.get("description") or props.get("short_description") or None),
        image=_first_image(props),
        address=props.get("address"),
        locality=geocode.get("name") if isinstance(geocode, dict) else None,
        lat=lat,
        lon=lon,
        tags=tags,
        explainability=explain,
    )


def _tag_from_raw(raw: dict) -> Optional[Tag]:
    if not isinstance(raw, dict):
        return None
    tag_id = raw.get("tag_id") or raw.get("id") or raw.get("tag_value")
    name = raw.get("name")
    if not tag_id or not name:
        return None
    query = raw.get("query") or {}
    a = query.get("a") if isinstance(query.get("a"), dict) else None
    b = query.get("b") if isinstance(query.get("b"), dict) else None
    return Tag(
        tag_id=str(tag_id),
        name=str(name),
        type=raw.get("subtype") or raw.get("type"),
        affinity=_num(query.get("affinity")),
        a_affinity=_num((a or {}).get("affinity") if a else query.get("a_affinity")),
        b_affinity=_num((b or {}).get("affinity") if b else query.get("b_affinity")),
        delta=_num(query.get("delta")),
    )


def _results(payload: Any, key: str) -> list:
    """Return the list under results.<key>, tolerating list-shaped results."""
    if not isinstance(payload, dict):
        return []
    results = payload.get("results")
    if isinstance(results, list):
        return results
    if isinstance(results, dict):
        value = results.get(key)
        if isinstance(value, list):
            return value
        # Some routes nest one level deeper (e.g. {"results": {"entities": {...}}})
        for v in results.values():
            if isinstance(v, list) and v and isinstance(v[0], dict):
                return v
    value = payload.get(key)
    return value if isinstance(value, list) else []


# --------------------------------------------------------------------------
# Live client
# --------------------------------------------------------------------------

class _TTLCache:
    def __init__(self, ttl_s: int, max_items: int = 2000):
        self.ttl_s = ttl_s
        self.max_items = max_items
        self._items: dict[str, tuple[float, Any]] = {}
        self._lock = threading.Lock()

    def get(self, key: str) -> Any:
        with self._lock:
            hit = self._items.get(key)
            if not hit:
                return None
            expires, value = hit
            if expires < time.time():
                self._items.pop(key, None)
                return None
            return value

    def put(self, key: str, value: Any) -> None:
        with self._lock:
            if len(self._items) >= self.max_items:
                oldest = sorted(self._items.items(), key=lambda kv: kv[1][0])[: self.max_items // 10]
                for k, _ in oldest:
                    self._items.pop(k, None)
            self._items[key] = (time.time() + self.ttl_s, value)


class LiveQlooClient:
    source = "live"

    def __init__(self, api_key: str, base_url: str, timeout_s: float = 20.0,
                 cache_ttl_s: int = 6 * 3600, daily_budget: int = 800,
                 transport: Optional[httpx.BaseTransport] = None):
        if not api_key:
            raise ValueError("QLOO_API_KEY is required for the live client")
        self._base_url = base_url.rstrip("/")
        self._http = httpx.Client(
            base_url=self._base_url,
            headers={"X-Api-Key": api_key, "Accept": "application/json",
                     "User-Agent": "taste-fit/0.1 (+https://github.com/kbns2000/taste-fit)"},
            timeout=timeout_s,
            transport=transport,
        )
        self._cache = _TTLCache(cache_ttl_s)
        self._daily_budget = daily_budget
        self._budget_day = _dt.date.today()
        self._calls_today = 0
        self._lock = threading.Lock()

    # -- quota -----------------------------------------------------------------
    def budget_status(self) -> dict[str, int]:
        with self._lock:
            self._roll_day()
            return {"calls_today": self._calls_today, "daily_budget": self._daily_budget}

    def _roll_day(self) -> None:
        today = _dt.date.today()
        if today != self._budget_day:
            self._budget_day = today
            self._calls_today = 0

    def _spend(self) -> None:
        with self._lock:
            self._roll_day()
            if self._calls_today >= self._daily_budget:
                raise QuotaExceeded("daily Qloo budget exhausted for this deployment")
            self._calls_today += 1

    # -- transport -------------------------------------------------------------
    @staticmethod
    def _encode(params: dict[str, Any]) -> dict[str, str]:
        out: dict[str, str] = {}
        for key, value in params.items():
            if value is None:
                continue
            if isinstance(value, bool):
                out[key] = "true" if value else "false"
            elif isinstance(value, (list, tuple)):
                if not value:
                    continue
                out[key] = ",".join(str(v) for v in value)
            else:
                out[key] = str(value)
        return out

    def _get(self, path: str, params: dict[str, Any], trace: Optional[list], key: str,
             preview_of=None) -> Any:
        encoded = self._encode(params)
        cache_key = path + "?" + "&".join(f"{k}={v}" for k, v in sorted(encoded.items()))
        record = CallRecord(endpoint=path, params=encoded, source=self.source)
        started = time.perf_counter()
        cached = self._cache.get(cache_key)
        if cached is not None:
            record.cached = True
            record.status = "cached"
            payload = cached
        else:
            payload = self._fetch(path, encoded, record)
            if record.status != "error":
                self._cache.put(cache_key, payload)
        record.duration_ms = int((time.perf_counter() - started) * 1000)
        items = _results(payload, key) if payload is not None else []
        record.count = len(items)
        if record.status != "error" and record.count == 0:
            record.status = "empty"
        if preview_of:
            record.preview = preview_of(items)
        if trace is not None:
            trace.append(record)
        if record.status == "error":
            raise QlooError(record.error or "Qloo request failed")
        return payload

    def _fetch(self, path: str, params: dict[str, str], record: CallRecord) -> Any:
        attempts = 0
        delay = 1.0
        while True:
            attempts += 1
            try:
                self._spend()
                response = self._http.get(path, params=params)
            except QuotaExceeded:
                raise
            except httpx.HTTPError as exc:
                if attempts <= 2:
                    time.sleep(delay)
                    delay *= 2
                    continue
                record.status = "error"
                record.error = f"network error: {exc.__class__.__name__}"
                return None
            if response.status_code == 200:
                try:
                    return response.json()
                except ValueError:
                    record.status = "error"
                    record.error = "invalid JSON from Qloo"
                    return None
            if response.status_code == 404:
                # The lookup routes answer 404 for "no results".
                return {"results": []}
            if response.status_code == 429 and attempts <= 2:
                retry_after = response.headers.get("retry-after")
                try:
                    wait = min(float(retry_after), 10.0) if retry_after else delay
                except ValueError:
                    wait = delay
                time.sleep(wait)
                delay *= 2
                continue
            if response.status_code >= 500 and attempts <= 2:
                time.sleep(delay)
                delay *= 2
                continue
            record.status = "error"
            body = response.text[:200].replace("\n", " ")
            if response.status_code in (401, 403):
                record.error = f"QLOO_AUTH ({response.status_code}): key rejected for {self._base_url}"
            elif response.status_code == 429:
                record.error = "QLOO_RATE_LIMIT (429): retry later"
            else:
                record.error = f"HTTP {response.status_code}: {body}"
            return None

    # -- lookup routes -----------------------------------------------------------
    def search(self, query: str, types: Optional[list[str]] = None, take: int = 8,
               trace: Optional[list] = None) -> list[Entity]:
        params = {"query": query, "take": max(1, min(take, 20))}
        if types:
            params["types"] = [to_urn(t) for t in types]
        payload = self._get("/search", params, trace, "entities",
                            preview_of=lambda items: [str(i.get("name")) for i in items[:5] if isinstance(i, dict)])
        out = []
        for raw in _results(payload, "entities"):
            entity = _entity_from_raw(raw)
            if entity:
                out.append(entity)
        return out

    def entities(self, entity_ids: list[str], trace: Optional[list] = None) -> list[Entity]:
        payload = self._get("/entities", {"entity_ids": entity_ids}, trace, "entities",
                            preview_of=lambda items: [str(i.get("name")) for i in items[:5] if isinstance(i, dict)])
        return [e for e in (_entity_from_raw(r) for r in _results(payload, "entities")) if e]

    def find_tags(self, query: str, take: int = 10, trace: Optional[list] = None) -> list[Tag]:
        payload = self._get("/v2/tags", {"filter.query": query, "take": min(take, 50)}, trace, "tags",
                            preview_of=lambda items: [str(i.get("name")) for i in items[:5] if isinstance(i, dict)])
        return [t for t in (_tag_from_raw(r) for r in _results(payload, "tags")) if t]

    # -- insights ----------------------------------------------------------------
    def taste_tags(self, entity_ids: list[str], take: int = 30, tag_types: Optional[list[str]] = None,
                   trace: Optional[list] = None) -> list[Tag]:
        params: dict[str, Any] = {
            "filter.type": "urn:tag",
            "signal.interests.entities": entity_ids,
            "take": min(take, 50),
        }
        if tag_types:
            params["filter.tag.types"] = tag_types
        payload = self._get("/v2/insights", params, trace, "tags",
                            preview_of=lambda items: [str(i.get("name")) for i in items[:6] if isinstance(i, dict)])
        return [t for t in (_tag_from_raw(r) for r in _results(payload, "tags")) if t]

    def demographics(self, entity_ids: list[str], trace: Optional[list] = None) -> list[Demographics]:
        params = {"filter.type": "urn:demographics", "signal.interests.entities": entity_ids}
        payload = self._get("/v2/insights", params, trace, "demographics",
                            preview_of=lambda items: [str(i.get("entity_id")) for i in items[:4] if isinstance(i, dict)])
        out = []
        for raw in _results(payload, "demographics"):
            if not isinstance(raw, dict):
                continue
            query = raw.get("query") or {}
            out.append(Demographics(
                entity_id=str(raw.get("entity_id") or ""),
                age={k: float(v) for k, v in (query.get("age") or {}).items() if _num(v) is not None},
                gender={k: float(v) for k, v in (query.get("gender") or {}).items() if _num(v) is not None},
            ))
        return out

    def recommend(self, entity_ids: list[str], target_type: str, take: int = 10,
                  filter_location: Optional[str] = None, signal_location: Optional[str] = None,
                  filter_tags: Optional[list[str]] = None, explainability: bool = True,
                  exclude_ids: Optional[list[str]] = None,
                  trace: Optional[list] = None) -> list[Entity]:
        params: dict[str, Any] = {
            "filter.type": to_urn(target_type),
            "signal.interests.entities": entity_ids,
            "take": min(take, 50),
        }
        if filter_location:
            params["filter.location.query"] = filter_location
        if signal_location:
            params["signal.location.query"] = signal_location
        if filter_tags:
            params["filter.tags"] = filter_tags
        if exclude_ids:
            params["filter.exclude.entities"] = exclude_ids
        if explainability and len(entity_ids) > 1:
            params["feature.explainability"] = True
        payload = self._get("/v2/insights", params, trace, "entities",
                            preview_of=lambda items: [str(i.get("name")) for i in items[:6] if isinstance(i, dict)])
        return [e for e in (_entity_from_raw(r, entity_ids) for r in _results(payload, "entities")) if e]

    def heatmap(self, entity_ids: list[str], location_query: str,
                trace: Optional[list] = None) -> list[HeatCell]:
        params = {
            "filter.type": "urn:heatmap",
            "filter.location.query": location_query,
            "signal.interests.entities": entity_ids,
        }
        payload = self._get("/v2/insights", params, trace, "heatmap",
                            preview_of=lambda items: [f"{len(items)} cells"])
        out = []
        for raw in _results(payload, "heatmap"):
            if not isinstance(raw, dict):
                continue
            loc = raw.get("location") or {}
            query = raw.get("query") or {}
            lat, lon = _num(loc.get("latitude")), _num(loc.get("longitude"))
            if lat is None or lon is None:
                continue
            out.append(HeatCell(
                lat=lat, lon=lon, geohash=loc.get("geohash"),
                affinity=_num(query.get("affinity")) or 0.0,
                affinity_rank=_num(query.get("affinity_rank")) or 0.0,
                popularity=_num(query.get("popularity")) or 0.0,
            ))
        return out

    def compare(self, a_ids: list[str], b_ids: list[str], trace: Optional[list] = None) -> list[Tag]:
        params = {"a.signal.interests.entities": a_ids, "b.signal.interests.entities": b_ids}
        payload = self._get("/v2/analysis/compare", params, trace, "tags",
                            preview_of=lambda items: [str(i.get("name")) for i in items[:6] if isinstance(i, dict)])
        tags = [t for t in (_tag_from_raw(r) for r in _results(payload, "tags")) if t]
        if not tags:
            raise QlooError("compare returned no tag rows", retryable=False)
        return tags

    def trending(self, entity_ids: list[str], target_type: str, start_date: str, end_date: str,
                 take: int = 10, trace: Optional[list] = None) -> list[TrendPoint]:
        params = {
            "signal.interests.entities": entity_ids,
            "filter.type": to_urn(target_type),
            "filter.start_date": start_date,
            "filter.end_date": end_date,
            "take": min(take, 50),
        }
        payload = self._get("/v2/trending", params, trace, "entities",
                            preview_of=lambda items: [str(i.get("name")) for i in items[:5] if isinstance(i, dict)])
        out = []
        for raw in _results(payload, "entities"):
            if not isinstance(raw, dict):
                continue
            query = raw.get("query") or {}
            value = None
            for key in ("trending", "population_percentile", "velocity", "affinity"):
                value = _num(query.get(key))
                if value is not None:
                    break
            out.append(TrendPoint(
                entity_id=str(raw.get("entity_id") or raw.get("id") or ""),
                name=raw.get("name"), date=raw.get("date"), value=value,
                extra={k: v for k, v in query.items() if isinstance(v, (int, float, str))},
            ))
        return out
