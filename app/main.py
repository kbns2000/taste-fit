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
    a_type: Optional[str] = Field(default=None, max_length=20)
    b_type: Optional[str] = Field(default=None, max_length=20)
    partner_type: Optional[str] = Field(default="brand", max_length=20)


class ResolveRequest(BaseModel):
    query: str = Field(min_length=1, max_length=120)
    type: Optional[str] = Field(default=None, max_length=20)


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
        event = {"kind": kind, "t": round(time.time() - self.created, 2), **payload}
        with self.lock:
            self.events.append(event)
            for q in self.listeners:
                q.put(event)

    def subscribe(self) -> tuple[queue.Queue, list[dict[str, Any]]]:
        q: queue.Queue = queue.Queue()
        with self.lock:
            self.listeners.append(q)
            return q, list(self.events)

    def unsubscribe(self, q: queue.Queue) -> None:
        with self.lock:
            if q in self.listeners:
                self.listeners.remove(q)

    def state(self) -> dict[str, Any]:
        return {
            "id": self.id, "status": self.status, "request": self.request.model_dump(),
            "events": self.events, "result": self.result, "error": self.error, "suggestions": self.suggestions,
        }


class RateLimiter:
    def __init__(self, limit: int, window_s: int = 600):
        self.limit, self.window_s = limit, window_s
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
            return True


app = FastAPI(title="Taste-Fit", version=settings.app_version, docs_url=None, redoc_url=None)
runs: dict[str, Run] = {}
executor = ThreadPoolExecutor(max_workers=3)
limiter = RateLimiter(settings.runs_per_ip_per_10min)
_provider_cache: dict[str, Any] = {}


def provider():
    if "p" not in _provider_cache:
        try:
            _provider_cache["p"] = build_provider(settings)
        except Exception:  # missing optional dependency or bad config: run without an LLM
            _provider_cache["p"] = None
    return _provider_cache["p"]


def _client_ip(request: Request) -> str:
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        return forwarded.split(",")[0].strip()
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
    if len(runs) <= max_runs:
        return
    for rid in sorted(runs, key=lambda r: runs[r].created)[: len(runs) - max_runs]:
        runs.pop(rid, None)


@app.get("/api/health")
def health() -> dict[str, Any]:
    client = get_client(settings)
    budget = client.budget_status() if hasattr(client, "budget_status") else {}
    return {"ok": True, **settings.summary(), "qloo_budget": budget, "runs_in_memory": len(runs)}


@app.get("/api/presets")
def presets() -> dict[str, Any]:
    return {"presets": PRESETS, "source": settings.summary()["qloo_source"]}


@app.post("/api/resolve")
def resolve(body: ResolveRequest, request: Request) -> dict[str, Any]:
    if not limiter.allow("resolve:" + _client_ip(request)):
        raise HTTPException(429, "Too many requests; try again in a few minutes.")
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
    if not limiter.allow(_client_ip(request)):
        raise HTTPException(429, "This demo allows a few runs per 10 minutes per visitor. Please try again shortly.")
    run = Run(body)
    runs[run.id] = run
    executor.submit(_work, run)
    return {"run_id": run.id, "status": run.status}


@app.get("/api/runs/{run_id}")
def get_run(run_id: str) -> dict[str, Any]:
    run = runs.get(run_id)
    if not run:
        raise HTTPException(404, "run not found")
    return run.state()


@app.get("/api/runs/{run_id}/events")
def run_events(run_id: str) -> StreamingResponse:
    run = runs.get(run_id)
    if not run:
        raise HTTPException(404, "run not found")

    def stream():
        q, backlog = run.subscribe()
        try:
            for event in backlog:
                yield _sse(event)
                if event["kind"] == "end":
                    return
            while True:
                try:
                    event = q.get(timeout=15)
                except queue.Empty:
                    yield ": keep-alive\n\n"
                    continue
                yield _sse(event)
                if event["kind"] == "end":
                    return
        finally:
            run.unsubscribe(q)

    return StreamingResponse(stream(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


def _sse(event: dict[str, Any]) -> str:
    return f"event: {event['kind']}\ndata: {json.dumps(event, ensure_ascii=False, default=str)}\n\n"


@app.get("/")
def index() -> FileResponse:
    return FileResponse(WEB_DIR / "index.html")


@app.get("/healthz")
def healthz() -> JSONResponse:
    return JSONResponse({"ok": True})


app.mount("/static", StaticFiles(directory=str(WEB_DIR)), name="static")
