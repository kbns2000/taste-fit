"""Run orchestration: fit check (A x B) and partner scout (A -> candidates).

The core evidence gathering is scripted so every run is complete and cheap;
the writer (LLM or scripted) turns it into a brief; the verifier checks the
brief against the ledger. `emit(kind, payload)` streams progress to the UI.
"""

from __future__ import annotations

import time
from typing import Any, Callable, Optional

from ..config import Settings, settings as default_settings
from ..qloo import get_client, get_mock
from ..qloo.client import QlooClient
from ..qloo.models import Entity, QlooError, QuotaExceeded
from .ledger import Ledger
from .llm.base import LLMProvider
from .tools import RunCapExceeded, ToolBox, hot_zones, pick_summary
from .verifier import verify_brief
from .writer import llm_brief, scripted_brief

Emit = Callable[[str, dict[str, Any]], None]

SCOUT_CANDIDATES = 3
ACTIVATION_PLAN = [("artist", 6), ("podcast", 5)]  # plus places when a city is given


class RunFailed(Exception):
    def __init__(self, message: str, suggestions: Optional[list[dict[str, Any]]] = None):
        super().__init__(message)
        self.suggestions = suggestions or []


def _entity_dict(e: Entity) -> dict[str, Any]:
    return {"entity_id": e.entity_id, "name": e.name, "type": e.type, "description": e.description,
            "image": e.image, "tags": e.tags[:5], "popularity": e.popularity, "disambiguation": e.disambiguation}


def _resolve_or_fail(toolbox: ToolBox, query: str, type_hint: Optional[str], role: str) -> Entity:
    entity = toolbox.resolve(query, type_hint)
    if entity is None:
        raise RunFailed(f"Could not find '{query}' in Qloo. Try a more specific name or pick a suggestion.",
                        suggestions=[])
    return entity


def _city_error(exc: BaseException, city: Optional[str]) -> Optional[str]:
    """Human message when Qloo rejected the request because of the city (HTTP 400)."""
    if city and getattr(exc, "status", None) == 400:
        return f"Qloo could not find the city '{city}'. Check the spelling or try a larger nearby city."
    return None


def _activations(toolbox: ToolBox, a: Entity, b: Entity, city: Optional[str], emit: Emit) -> tuple[dict, dict]:
    picks: dict[str, list[dict[str, Any]]] = {}
    cites: dict[str, list[str]] = {}
    plan = list(ACTIVATION_PLAN) + ([("place", 6)] if city else [])
    city_failed = False
    for kind, take in plan:
        try:
            rows, ids = toolbox.recommend([a.entity_id, b.entity_id], kind, take=take,
                                          city=None if city_failed else city,
                                          exclude_ids=[a.entity_id, b.entity_id])
        except (RunCapExceeded, QlooError) as exc:
            city_msg = _city_error(exc, city)
            if city_msg and not city_failed:
                city_failed = True
                emit("warn", {"text": city_msg + " Continuing without the city."})
                try:  # retry this kind without the city signal
                    rows, ids = toolbox.recommend([a.entity_id, b.entity_id], kind, take=take, city=None,
                                                  exclude_ids=[a.entity_id, b.entity_id])
                except (RunCapExceeded, QlooError) as exc2:
                    emit("warn", {"text": f"{kind} recommendations skipped: {exc2}"})
                    continue
            elif city_msg:
                continue
            else:
                emit("warn", {"text": f"{kind} recommendations skipped: {exc}"})
                continue
        picks[kind] = [pick_summary(e, a.entity_id, b.entity_id) for e in rows]
        cites[kind] = ids
        emit("picks", {"type": kind, "items": picks[kind], "cites": ids})
    return picks, cites, city_failed


def run_fit_check(a_query: str, b_query: str, city: Optional[str], client: QlooClient,
                  provider: Optional[LLMProvider], emit: Emit, cfg: Settings = default_settings,
                  a_type: Optional[str] = None, b_type: Optional[str] = None) -> dict[str, Any]:
    started = time.perf_counter()
    ledger = Ledger()
    toolbox = ToolBox(client, ledger, max_calls=cfg.qloo_max_calls_per_run)

    emit("step", {"name": "resolve", "text": f"Resolving '{a_query}' and '{b_query}' in Qloo"})
    a = _resolve_or_fail(toolbox, a_query, a_type, "A")
    b = _resolve_or_fail(toolbox, b_query, b_type, "B")
    if a.entity_id == b.entity_id:
        raise RunFailed("Both names resolved to the same Qloo entity. Pick two different brands or partners.")
    emit("resolved", {"a": _entity_dict(a), "b": _entity_dict(b),
                      "alternatives": {"a": toolbox.alternatives.get(a.entity_id, []),
                                       "b": toolbox.alternatives.get(b.entity_id, [])}})

    emit("step", {"name": "fit", "text": "Comparing taste tags and demographics"})
    fit, fit_groups = toolbox.fit_with_groups(a.entity_id, b.entity_id)
    fit_cites = fit_groups["tags"] + fit_groups["demographics"]
    fit_dict = fit.model_dump()
    emit("fit", {"fit": fit_dict, "cites": fit_cites})

    emit("step", {"name": "activations", "text": "Asking Qloo what fans of both over-index on"})
    picks, pick_cites, city_failed = _activations(toolbox, a, b, city, emit)
    if city_failed:
        city = None

    zones: list[dict[str, Any]] = []
    zone_cites: list[str] = []
    heat: list[dict[str, Any]] = []
    if city:
        emit("step", {"name": "heatmap", "text": f"Mapping where the joint audience concentrates in {city}"})
        try:
            cells, zone_cites = toolbox.heatmap([a.entity_id, b.entity_id], city)
            places = [toolbox.entities[p["entity_id"]] for p in picks.get("place", []) if p["entity_id"] in toolbox.entities]
            zones = hot_zones(cells, places)
            heat = [c.model_dump() for c in cells]
            emit("heatmap", {"zones": zones, "cells": heat, "cites": zone_cites})
        except (RunCapExceeded, QlooError) as exc:
            emit("warn", {"text": _city_error(exc, city) or f"heatmap skipped: {exc}"})

    ctx: dict[str, Any] = {
        "mode": "fit", "a": _entity_dict(a), "b": _entity_dict(b), "city": city,
        "fit": fit_dict, "fit_cites": fit_cites, "fit_cite_groups": fit_groups,
        "picks": picks, "pick_cites": pick_cites,
        "zones": zones, "zone_cites": zone_cites, "heatmap": heat,
        "source": ledger.source(), "ledger": ledger.as_list(),
    }

    writer_meta: dict[str, Any] = {"provider": "scripted"}
    brief: Optional[dict[str, Any]] = None
    if provider is not None:
        emit("step", {"name": "write", "text": f"Writing the brief with {provider.model}"})
        brief, writer_meta = llm_brief(ctx, toolbox, provider, cfg.llm_max_tool_calls, emit,
                                       deadline_s=cfg.llm_deadline_s)
        if brief is None:
            emit("warn", {"text": f"LLM writer unavailable ({writer_meta.get('error')}); using the scripted writer"})
            writer_meta = {**writer_meta, "fallback": "scripted"}
    if brief is None:
        emit("step", {"name": "write", "text": "Writing the brief from the evidence"})
        ctx["ledger"] = ledger.as_list()
        brief = scripted_brief(ctx)

    emit("step", {"name": "verify", "text": "Checking every claim against the Qloo ledger"})
    verification = verify_brief(brief, ledger, fit_dict, [a.name, b.name])

    result = {
        "mode": "fit", "a": ctx["a"], "b": ctx["b"], "city": city,
        "alternatives": {"a": toolbox.alternatives.get(a.entity_id, []), "b": toolbox.alternatives.get(b.entity_id, [])},
        "fit": fit_dict, "fit_cites": fit_cites, "fit_cite_groups": fit_groups,
        "picks": ctx["picks"], "pick_cites": ctx["pick_cites"],
        "zones": ctx.get("zones") or [], "zone_cites": ctx.get("zone_cites") or [], "heatmap": ctx.get("heatmap") or [],
        "brief": brief, "verification": verification,
        "ledger": ledger.as_list(), "source": ledger.source(),
        "writer": writer_meta, "qloo_calls": ledger.calls(),
        "duration_ms": int((time.perf_counter() - started) * 1000),
    }
    emit("done", {"summary": {"fit": fit_dict["fit"], "band": fit_dict["band"], "qloo_calls": ledger.calls()}})
    return result


def run_partner_scout(a_query: str, city: Optional[str], client: QlooClient, emit: Emit,
                      cfg: Settings = default_settings, a_type: Optional[str] = None,
                      partner_type: str = "brand") -> dict[str, Any]:
    started = time.perf_counter()
    ledger = Ledger()
    toolbox = ToolBox(client, ledger, max_calls=cfg.qloo_max_calls_per_run)

    emit("step", {"name": "resolve", "text": f"Resolving '{a_query}' in Qloo"})
    a = _resolve_or_fail(toolbox, a_query, a_type, "A")
    emit("resolved", {"a": _entity_dict(a), "alternatives": {"a": toolbox.alternatives.get(a.entity_id, [])}})

    emit("step", {"name": "candidates", "text": f"Asking Qloo which {partner_type}s fans of {a.name} over-index on"})
    try:
        rows, cand_cites = toolbox.recommend([a.entity_id], partner_type, take=8, city=city, exclude_ids=[a.entity_id])
    except QlooError as exc:
        city_msg = _city_error(exc, city)
        if not city_msg:
            raise
        emit("warn", {"text": city_msg + " Continuing without the city."})
        city = None
        rows, cand_cites = toolbox.recommend([a.entity_id], partner_type, take=8, city=None, exclude_ids=[a.entity_id])
    if not rows:
        raise RunFailed(f"Qloo returned no {partner_type} candidates for {a.name}.")
    emit("candidates", {"items": [_entity_dict(e) | {"affinity": e.affinity} for e in rows], "cites": cand_cites})

    ranked: list[dict[str, Any]] = []
    for cand in rows[:SCOUT_CANDIDATES]:
        emit("step", {"name": "fit", "text": f"Scoring {a.name} x {cand.name}"})
        try:
            fit, cites = toolbox.fit(a.entity_id, cand.entity_id)
        except (RunCapExceeded, QlooError) as exc:
            emit("warn", {"text": f"{cand.name} skipped: {exc}"})
            continue
        ranked.append({"entity": _entity_dict(cand), "qloo_affinity": cand.affinity, "fit": fit.model_dump(), "cites": cites})
        emit("scored", {"name": cand.name, "fit": fit.fit, "band": fit.band})
    ranked.sort(key=lambda r: -r["fit"]["fit"])
    for r in ranked:
        r["fit_cites"] = r.pop("cites")

    extra = [{"entity": _entity_dict(e) | {"affinity": e.affinity}, "fit": None} for e in rows[SCOUT_CANDIDATES:]]
    result = {
        "mode": "scout", "a": _entity_dict(a), "city": city, "partner_type": partner_type,
        "ranked": ranked, "more_candidates": extra, "candidate_cites": cand_cites,
        "ledger": ledger.as_list(), "source": ledger.source(), "qloo_calls": ledger.calls(),
        "duration_ms": int((time.perf_counter() - started) * 1000),
    }
    emit("done", {"summary": {"top": ranked[0]["entity"]["name"] if ranked else None, "qloo_calls": ledger.calls()}})
    return result


def execute(payload: dict[str, Any], emit: Emit, cfg: Settings = default_settings,
            client: Optional[QlooClient] = None, provider: Optional[LLMProvider] = None) -> dict[str, Any]:
    """Entry point used by the API. Falls back to demo data if live Qloo is unusable."""
    client = client or get_client(cfg)
    mode = payload.get("mode", "fit")
    try:
        return _execute_with(client, payload, emit, cfg, provider, mode)
    except (QuotaExceeded, QlooError) as exc:
        status = getattr(exc, "status", None)
        if status == 400:  # our request was wrong (unknown city, bad id): not an outage
            raise RunFailed(_city_error(exc, (payload.get("city") or "").strip() or None)
                            or f"Qloo rejected the request: {exc}") from exc
        if getattr(client, "source", "") == "live":
            emit("warn", {"text": f"Live Qloo unavailable ({exc}); re-running on demo data"})
            emit("reset", {"reason": str(exc)})
            try:
                result = _execute_with(get_mock(), payload, emit, cfg, provider, mode)
            except RunFailed as exc2:
                raise RunFailed(f"Live Qloo failed ({exc}) and the demo data could not cover this request: {exc2}") from exc
            result["fallback_reason"] = str(exc)
            return result
        raise RunFailed(f"Qloo request failed: {exc}") from exc


def _execute_with(client: QlooClient, payload: dict[str, Any], emit: Emit, cfg: Settings,
                  provider: Optional[LLMProvider], mode: str) -> dict[str, Any]:
    city = (payload.get("city") or "").strip() or None
    if mode == "scout":
        return run_partner_scout(payload["a"].strip(), city, client, emit, cfg,
                                 a_type=payload.get("a_type"), partner_type=payload.get("partner_type") or "brand")
    return run_fit_check(payload["a"].strip(), (payload.get("b") or "").strip(), city, client, provider, emit, cfg,
                         a_type=payload.get("a_type"), b_type=payload.get("b_type"))
