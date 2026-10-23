"""Taste-Fit HTTP API and static UI.

Routes
  GET  /api/health              configuration summary (never secrets) + quota status
  GET  /api/presets             demo pairs that resolve in both live and demo data
  POST /api/resolve             entity type-ahead: {"query": "...", "type": "brand|any"}
  POST /api/runs                start a run: {"mode": "fit|scout", "a", "b", "city"}
  GET  /api/runs/{id}           current state (steps + result when done)
  GET  /api/runs/{id}/events    Server-Sent Events stream of the run's progress
  GET  /                        the single-page UI
"""

from __future__ import annotations

import asyncio
import json
import queue
import threading
import time
import uuid
from collections import defaultdict, deque
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any, Optional

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from .agent.llm.openai_compat import build_provider
from .agent.pipeline import RunFailed, execute
from .agent.tools import RunCapExceeded
from .config import settings
from .qloo import get_client

WEB_DIR = Path(__file__).resolve().parent.parent / "web"
TYPE_PATTERN = "^(brand|artist|place|podcast|movie|tv_show|book|person|destination|videogame|any)$"
MAX_LISTENERS_PER_RUN = 5

PRESETS = [
    {"label": "Patagonia x Bon Iver in New York", "mode": "fit", "a": "Patagonia", "b": "Bon Iver", "city": "New York"},
    {"label": "Glossier x Blue Bottle Coffee in San Francisco", "mode": "fit", "a": "Glossier", "b": "Blue Bottle Coffee", "city": "San Francisco"},
    {"label": "Supreme x Tyler, the Creator in Los Angeles", "mode": "fit", "a": "Supreme", "b": "Tyler, the Creator", "city": "Los Angeles"},
    {"label": "REI x The Dirtbag Diaries in Portland", "mode": "fit", "a": "REI", "b": "The Dirtbag Diaries", "city": "Portland"},
    {"label": "Patagonia x Supreme (a mismatch)", "mode": "fit", "a": "Patagonia", "b": "Supreme", "city": ""},
    {"label": "Scout partners for Allbirds", "mode": "scout", "a": "Allbirds", "b": "", "city": "San Francisco"},
]


class RunRequest(BaseModel):
    mode: str = Field(default="fit", pattern="^(fit|scout)$")
    a: str = Field(min_length=1, max_length=120)
    b: Optional[str] = Field(default=None, max_length=120)
    city: Optional[str] = Field(default=None, max_length=80)
    a_type: Optional[str] = Field(default=None, pattern=TYPE_PATTERN)
    b_type: Optional[str] = Field(default=None, pattern=TYPE_PATTERN)
    partner_type: Optional[str] = Field(default="brand", pattern=TYPE_PATTERN)


class ResolveRequest(BaseModel):
    query: str = Field(min_length=1, max_length=120)
    type: Optional[str] = Field(default=None, pattern=TYPE_PATTERN)


class Run:
    def __init__(self, request: RunRequest):
        self.id = uuid.uuid4().hex[:12]
        self.request = request
        self.status = "queued"  # queued | running | done | failed
        self.created = time.time()
        self.events: list[dict[str, Any]] = []
        self.result: Optional[dict[str, Any]] = None
        self.error: Optional[str] = None
        self.suggestions: list[dict[str, Any]] = []
        self.listeners: list[queue.Queue] = []
        self.lock = threading.Lock()

    def emit(self, kind: str, payload: dict[str, Any]) -> None:
        with self.lock:
            event = {"kind": kind, "seq": len(self.events) + 1, "t": round(time.time() - self.created, 2), **payload}
            self.events.append(event)
            for q in self.listeners:
                q.put(event)

    def subscribe(self, after_seq: int = 0) -> tuple[queue.Queue, list[dict[str, Any]]]:
        q: queue.Queue = queue.Queue()
        with self.lock:
            if len(self.listeners) >= MAX_LISTENERS_PER_RUN:
                raise HTTPException(429, "too many open event streams for this run")
            self.listeners.append(q)
            return q, [e for e in self.events if e["seq"] > after_seq]

    def unsubscribe(self, q: queue.Queue) -> None:
        with self.lock:
            if q in self.listeners:
                self.listeners.remove(q)

    def finished(self) -> bool:
        return self.status in ("done", "failed")

    def state(self) -> dict[str, Any]:
        return {
            "id": self.id, "status": self.status, "request": self.request.model_dump(),
            "events": list(self.events), "result": self.result, "error": self.error, "suggestions": self.suggestions,
        }


class RateLimiter:
    def __init__(self, limit: int, window_s: int = 600, max_keys: int = 5000):
        self.limit, self.window_s, self.max_keys = limit, window_s, max_keys
        self.hits: dict[str, deque] = defaultdict(deque)
        self.lock = threading.Lock()

    def allow(self, key: str) -> bool:
        now = time.time()
        with self.lock:
            dq = self.hits[key]
            while dq and dq[0] < now - self.window_s:
                dq.popleft()
            if len(dq) >= self.limit:
                return False
            dq.append(now)
            if len(self.hits) > self.max_keys:  # drop the stalest keys
                for stale in [k for k, v in self.hits.items() if not v or v[-1] < now - self.window_s][:1000]:
                    self.hits.pop(stale, None)
            return True


app = FastAPI(title="Taste-Fit", version=settings.app_version, docs_url=None, redoc_url=None)
runs: dict[str, Run] = {}
runs_lock = threading.Lock()
executor = ThreadPoolExecutor(max_workers=3)
run_limiter = RateLimiter(settings.runs_per_ip_per_10min)
lookup_limiter = RateLimiter(settings.lookups_per_ip_per_10min)
_provider_cache: dict[str, Any] = {}


def provider():
    if "p" not in _provider_cache:
        try:
            _provider_cache["p"] = build_provider(settings)
        except Exception:  # missing optional dependency or bad config: run without an LLM
            _provider_cache["p"] = None
    return _provider_cache["p"]


def _client_ip(request: Request) -> str:
    # Behind a reverse proxy the proxy appends the real client as the LAST hop; earlier entries are client-supplied.
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        hops = [h.strip() for h in forwarded.split(",") if h.strip()]
        if hops:
            return hops[-1]
    return request.client.host if request.client else "unknown"


def _work(run: Run) -> None:
    run.status = "running"
    try:
        run.result = execute(run.request.model_dump(), run.emit, settings, provider=provider())
        run.status = "done"
    except RunFailed as exc:
        run.status, run.error, run.suggestions = "failed", str(exc), exc.suggestions
        run.emit("error", {"text": str(exc)})
    except RunCapExceeded as exc:
        run.status, run.error = "failed", str(exc)
        run.emit("error", {"text": str(exc)})
    except Exception as exc:  # last resort: never leave a run hanging
        run.status, run.error = "failed", f"unexpected error: {exc.__class__.__name__}"
        run.emit("error", {"text": run.error})
    finally:
        run.emit("end", {"status": run.status})
        _prune()


def _prune(max_runs: int = 200) -> None:
    with runs_lock:
        if len(runs) <= max_runs:
            return
        finished = sorted((r for r in runs.values() if r.finished()), key=lambda r: r.created)
        for run in finished[: max(0, len(runs) - max_runs)]:
            runs.pop(run.id, None)


def _active_runs() -> int:
    with runs_lock:
        return sum(1 for r in runs.values() if not r.finished())


def _qloo_source() -> str:
    client = get_client(settings)
    budget = client.budget_status() if hasattr(client, "budget_status") else {}
    if settings.qloo_live and budget.get("daily_budget") and budget.get("calls_today", 0) >= budget["daily_budget"]:
        return "live-exhausted"
    return settings.summary()["qloo_source"]


@app.get("/api/health")
def health() -> dict[str, Any]:
    client = get_client(settings)
    budget = client.budget_status() if hasattr(client, "budget_status") else {}
    with runs_lock:
        in_memory = len(runs)
    return {"ok": True, **settings.summary(), "qloo_source": _qloo_source(), "qloo_budget": budget,
            "runs_in_memory": in_memory, "active_runs": _active_runs()}


@app.get("/api/presets")
def presets() -> dict[str, Any]:
    return {"presets": PRESETS, "source": _qloo_source()}


@app.post("/api/resolve")
def resolve(body: ResolveRequest, request: Request) -> dict[str, Any]:
    if not lookup_limiter.allow(_client_ip(request)):
        raise HTTPException(429, "Too many lookups; try again in a few minutes.")
    client = get_client(settings)
    types = [body.type] if body.type and body.type != "any" else None
    try:
        found = client.search(body.query, types=types, take=6)
    except Exception as exc:
        raise HTTPException(502, f"Qloo search failed: {exc.__class__.__name__}") from exc
    return {"results": [
        {"entity_id": e.entity_id, "name": e.name, "type": e.type, "disambiguation": e.disambiguation,
         "popularity": e.popularity, "image": e.image} for e in found]}


@app.post("/api/runs", status_code=202)
def create_run(body: RunRequest, request: Request) -> dict[str, Any]:
    if body.mode == "fit" and not (body.b or "").strip():
        raise HTTPException(422, "A fit check needs both A and B.")
    if not run_limiter.allow(_client_ip(request)):
        raise HTTPException(429, "This demo allows a few runs per 10 minutes per visitor. Please try again shortly.")
    if _active_runs() >= settings.max_active_runs:
        raise HTTPException(503, "The demo is busy right now; please try again in a minute.")
    run = Run(body)
    with runs_lock:
        runs[run.id] = run
    executor.submit(_work, run)
    return {"run_id": run.id, "status": run.status}


def _get_run(run_id: str) -> Run:
    with runs_lock:
        run = runs.get(run_id)
    if not run:
        raise HTTPException(404, "run not found")
    return run


@app.get("/api/runs/{run_id}")
def get_run(run_id: str) -> dict[str, Any]:
    return _get_run(run_id).state()


@app.get("/api/runs/{run_id}/events")
async def run_events(run_id: str, request: Request) -> StreamingResponse:
    run = _get_run(run_id)
    try:
        after = int(request.headers.get("last-event-id") or 0)
    except ValueError:
        after = 0
    q, backlog = run.subscribe(after)

    async def stream():
        try:
            for event in backlog:
                yield _sse(event)
                if event["kind"] == "end":
                    return
            idle = 0.0
            while True:
                try:
                    event = q.get_nowait()
                except queue.Empty:
                    await asyncio.sleep(0.25)  # async wait: no worker thread is held per stream
                    idle += 0.25
                    if idle >= 15:
                        idle = 0.0
                        yield ": keep-alive\n\n"
                    continue
                idle = 0.0
                yield _sse(event)
                if event["kind"] == "end":
                    return
        finally:
            run.unsubscribe(q)

    return StreamingResponse(stream(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


def _sse(event: dict[str, Any]) -> str:
    return f"id: {event['seq']}\nevent: {event['kind']}\ndata: {json.dumps(event, ensure_ascii=False, default=str)}\n\n"


@app.get("/")
def index() -> FileResponse:
    return FileResponse(WEB_DIR / "index.html")


@app.get("/healthz")
def healthz() -> JSONResponse:
    return JSONResponse({"ok": True})


app.mount("/static", StaticFiles(directory=str(WEB_DIR)), name="static")
