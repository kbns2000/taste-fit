"""Brief writer: an LLM agent loop over Qloo tools, with a scripted fallback.

The scripted writer guarantees a complete, evidence-cited brief with no LLM
at all (CI, offline demos, provider outages). When a provider is configured
the model can fetch extra evidence through the tool box and must submit the
brief through the strict `finalize_brief` tool.
"""

from __future__ import annotations

import time
from typing import Any, Callable, Optional

from pydantic import BaseModel, Field, ValidationError

from ..qloo.models import QlooError, QuotaExceeded
from .llm.base import LLMError, LLMProvider, Message, ToolResult
from .prompts import SYSTEM_PROMPT, tool_result_text, user_instruction
from .tools import FINALIZE_TOOL, LLM_TOOL_SPECS, RunCapExceeded, ToolBox, hot_zones, pick_summary

Emit = Callable[[str, dict[str, Any]], None]


class _Why(BaseModel):
    text: str = Field(min_length=1)
    cites: list[str] = Field(default_factory=list)


class _Activation(BaseModel):
    title: str = Field(min_length=1)
    format: str = ""
    entities: list[str] = Field(default_factory=list)
    rationale: str = ""
    cites: list[str] = Field(default_factory=list)


class Brief(BaseModel):
    """Shape of a finished brief; the LLM's finalize_brief payload is validated against it."""
    headline: str = Field(min_length=1)
    verdict: str = Field(pattern="^(go|go_with_conditions|no_go)$")
    why: list[_Why] = Field(min_length=1)
    target_segments: list[str] = Field(default_factory=list)
    message_angles: list[str] = Field(default_factory=list)
    channels: list[str] = Field(default_factory=list)
    activations: list[_Activation] = Field(default_factory=list)
    risks: list[str] = Field(default_factory=list)
    next_steps: list[str] = Field(default_factory=list)


def validate_brief(payload: Any) -> Optional[dict[str, Any]]:
    try:
        return Brief.model_validate(payload).model_dump()
    except ValidationError:
        return None


# ---------------------------------------------------------------------------
# Scripted writer
# ---------------------------------------------------------------------------

def scripted_brief(ctx: dict[str, Any]) -> dict[str, Any]:
    fit = ctx["fit"]
    a, b, city = ctx["a"]["name"], ctx["b"]["name"], ctx.get("city")
    shared = [t["name"] for t in fit["shared_tags"][:4]]
    fresh = [t["name"] for t in fit["b_only_tags"][:3]]
    groups = ctx.get("fit_cite_groups") or {}
    tag_cites = groups.get("tags") or ctx["fit_cites"][:1]
    demo_cites = groups.get("demographics") or ctx["fit_cites"][1:3] or ctx["fit_cites"]
    cites = ctx["fit_cites"]
    band = fit["band"]
    verdict = {"strong": "go", "workable": "go_with_conditions", "weak": "no_go"}[band]

    why = []
    if shared:
        why.append({"text": f"Fans of {a} and {b} share a taste core around {', '.join(shared)} "
                            f"(shared taste {fit['shared_taste']}/100).", "cites": tag_cites})
    else:
        why.append({"text": f"Qloo finds no taste tags that both audiences over-index on "
                            f"(shared taste {fit['shared_taste']}/100).", "cites": tag_cites})
    cat, cat_cites = ctx.get("catalog"), ctx.get("catalog_cites") or []
    if cat and cat_cites:
        if cat["common_count"] == 0 and fit["shared_taste"] >= 30:
            why.append({"text": f"On paper {a} and {b} share no Qloo catalog tags, so the overlap is in what their "
                                f"fans like, not in what the two are.", "cites": cat_cites + tag_cites})
        elif cat["common_count"]:
            why.append({"text": f"Qloo's catalog already tags both with {', '.join(cat['common'][:3])}.",
                        "cites": cat_cites})
    if fit["agreeing_segments"]:
        why.append({"text": f"Both audiences over-index on the same segments: {', '.join(fit['agreeing_segments'])} "
                            f"(audience alignment {fit['audience_alignment']}/100).", "cites": demo_cites})
    elif fit["diverging_segments"]:
        why.append({"text": f"The audiences pull apart on {', '.join(fit['diverging_segments'])} "
                            f"(audience alignment {fit['audience_alignment']}/100).", "cites": demo_cites})
    elif not fit["demographics"]:
        why.append({"text": "Qloo returned no demographic index for this pair, so audience alignment is set to a "
                            "neutral 50 and the score leans on taste overlap alone.", "cites": demo_cites})
    if fresh:
        why.append({"text": f"{b} brings reach {a} does not have: {', '.join(fresh)} "
                            f"({fit['fresh_reach']}% of {b}'s top tags are new to {a}'s audience).", "cites": tag_cites})

    activations = []
    for kind, picks in ctx["picks"].items():
        if not picks:
            continue
        both = [p for p in picks if p["driver"] in ("both", "joint")] or picks
        top = both[:2]
        names = [p["name"] for p in top]
        fmt = top[0]["format"]
        where = f" in {city}" if kind == "place" and city else ""
        activations.append({
            "title": f"{kind.replace('_', ' ').title()} activation{where}: {names[0]}",
            "format": fmt,
            "entities": names,
            "rationale": f"Fans of both {a} and {b} over-index on {', '.join(names)} "
                         f"(Qloo affinity {top[0]['affinity']}); " + (
                             "the explainability split shows both audiences drive it."
                             if top[0]["driver"] == "both" else "a safe pick for the joint audience."),
            "cites": ctx["pick_cites"].get(kind, []),
        })
    zones = ctx.get("zones") or []
    if zones and city:
        near = [z["near"] for z in zones if z.get("near")]
        activations.append({
            "title": f"Neighbourhood pop-up in {city}",
            "format": "pop-up or co-hosted event in the joint audience's hottest zone",
            "entities": near[:2],
            "rationale": "The Qloo heatmap puts the joint audience's strongest cells "
                         + (f"around {', '.join(near[:2])}." if near else "in a few concentrated zones (see map)."),
            "cites": ctx["zone_cites"],
        })

    segments = list(fit["agreeing_segments"][:3]) + [f"{t} enthusiasts" for t in shared[:2]]
    angles = [f"Lead with {shared[0]}: the thing both audiences already love." if shared else
              f"Position {b} as the discovery, not the default, for {a}'s audience.",
              f"Make {b}'s {fresh[0]} angle the reason {a}'s fans try something new." if fresh else
              "Keep one shared promise across both brands and let each add a single twist."]
    channels = []
    if ctx["picks"].get("artist"):
        channels.append("co-branded playlist and artist content")
    if ctx["picks"].get("podcast"):
        channels.append("podcast host-reads")
    if zones:
        channels.append(f"out-of-home and events in the {city} hot zones")
    channels += ["owned social of both brands", "shared email to overlapping segments"]

    risks = []
    if fit["diverging_segments"]:
        risks.append(f"Diverging segments ({', '.join(fit['diverging_segments'])}): one brand's core may feel left out.")
    if fit["balance"] < 50:
        risks.append("Reach balance is off: " + ("the audiences are near-clones, so the partnership adds little new reach."
                                                 if fit["fresh_reach"] < 25 else
                                                 "very little overlap, so the message has to work for two unrelated audiences."))
    if ctx["source"] != "live":
        risks.append("This run used demo data shaped like Qloo responses, not live Qloo signals.")
    if not risks:
        risks.append("Affinities describe audiences in aggregate; validate with a small pilot before a full campaign.")

    next_steps = [
        f"Agree the shared promise around {shared[0]}." if shared else "Decide whether a reach play is worth it without a shared taste core.",
        f"Pilot the first activation ({activations[0]['entities'][0]})." if activations and activations[0]["entities"] else "Pilot one activation and measure uplift.",
        "Re-run Taste-Fit with the campaign city to localise places and hot zones." if not city else
        f"Scout venues near the {city} hot zones.",
    ]
    headline = {
        "strong": f"{a} x {b}: strong fit ({fit['fit']}/100) with a clear shared taste core.",
        "workable": f"{a} x {b}: workable fit ({fit['fit']}/100) if the message stays on the shared core.",
        "weak": f"{a} x {b}: weak fit ({fit['fit']}/100); treat as a reach experiment, not a partnership.",
    }[band]
    return {
        "headline": headline, "verdict": verdict, "why": why[:5],
        "target_segments": segments[:4], "message_angles": angles[:4], "channels": channels[:5],
        "activations": activations[:5], "risks": risks[:4], "next_steps": next_steps[:4],
    }


# ---------------------------------------------------------------------------
# LLM writer (agent loop)
# ---------------------------------------------------------------------------

def _run_tool(name: str, args: dict[str, Any], toolbox: ToolBox, ctx: dict[str, Any]) -> dict[str, Any]:
    a_id, b_id = ctx["a"]["entity_id"], ctx["b"]["entity_id"]
    if name == "recommend":
        if args.get("target_type") not in ACTIVATION_TYPES:
            raise ValueError(f"target_type must be one of {', '.join(ACTIVATION_TYPES)}")
        city = str(args.get("city") or "").strip()[:80] or None
        try:
            take = max(1, min(int(args.get("take") or 6), 10))
        except (TypeError, ValueError):
            take = 6
        rows, ids = toolbox.recommend([a_id, b_id], args["target_type"], take=take,
                                      city=city, exclude_ids=[a_id, b_id])
        picks = [pick_summary(e, a_id, b_id) for e in rows]
        ctx["picks"].setdefault(args["target_type"], [])
        ctx["picks"][args["target_type"]] = (ctx["picks"][args["target_type"]] + picks)[:10]
        ctx["pick_cites"].setdefault(args["target_type"], [])
        ctx["pick_cites"][args["target_type"]] += ids
        return {"ledger_ids": ids, "results": [
            {k: p[k] for k in ("name", "type", "affinity", "driver", "locality", "format")} for p in picks]}
    if name == "heatmap":
        city = str(args.get("city") or "").strip()[:80]
        if not city:
            raise ValueError("city is required")
        cells, ids = toolbox.heatmap([a_id, b_id], city)
        places = [toolbox.entities[p["entity_id"]] for p in ctx["picks"].get("place", []) if p["entity_id"] in toolbox.entities]
        zones = hot_zones(cells, places)
        if zones:
            ctx["zones"], ctx["zone_cites"], ctx["city"] = zones, ids, ctx.get("city") or args["city"]
            ctx["heatmap"] = [c.model_dump() for c in cells]
        return {"ledger_ids": ids, "cells": len(cells), "hot_zones": zones}
    if name == "find_tags":
        rows, ids = toolbox.find_tags(str(args.get("query") or "")[:80])
        return {"ledger_ids": ids, "tags": [{"name": t.name, "tag_id": t.tag_id} for t in rows]}
    raise ValueError(f"unknown tool {name}")


def llm_brief(ctx: dict[str, Any], toolbox: ToolBox, provider: LLMProvider, max_tool_calls: int,
              emit: Optional[Emit] = None, deadline_s: float = 120.0) -> tuple[Optional[dict[str, Any]], dict[str, Any]]:
    """Returns (brief or None, meta). None means the caller should use the scripted writer."""
    meta: dict[str, Any] = {"provider": provider.name, "model": provider.model, "tool_calls": 0, "turns": 0}
    system = SYSTEM_PROMPT.format(max_tools=max_tool_calls)
    messages: list[Message] = [Message("user", user_instruction(ctx))]
    tools = LLM_TOOL_SPECS + [FINALIZE_TOOL]
    nudged = False
    resubmitted = False
    started = time.monotonic()
    for _ in range(max_tool_calls + 4):
        if time.monotonic() - started > deadline_s:
            meta["error"] = f"writer exceeded {int(deadline_s)}s"
            return None, meta
        meta["turns"] += 1
        try:
            response = provider.complete(system, messages, tools)
        except LLMError as exc:
            meta["error"] = str(exc)
            return None, meta
        except Exception as exc:  # SDK/type errors must never fail the run
            meta["error"] = f"{exc.__class__.__name__}"
            return None, meta
        if response.usage:
            meta.setdefault("usage", []).append(response.usage)
        if response.model:
            meta["model"] = response.model
        if response.stop_reason == "refusal":
            meta["error"] = "model refused"
            return None, meta
        messages.append(Message("assistant", response.text, response.tool_calls))
        if not response.tool_calls:
            if nudged:
                meta["error"] = "model did not call finalize_brief"
                return None, meta
            nudged = True
            messages.append(Message("user", "Please submit the brief now by calling finalize_brief."))
            continue
        results: list[ToolResult] = []
        final: Optional[dict[str, Any]] = None
        for call in response.tool_calls:
            if call.name == "finalize_brief":
                valid = validate_brief(call.input)
                if valid is None:
                    if resubmitted:
                        meta["error"] = "finalize_brief payload did not match the schema"
                        return None, meta
                    resubmitted = True
                    results.append(ToolResult(call.id, "brief rejected: it does not match the finalize_brief schema "
                                              "(headline, verdict, why[], activations[] ...). Resubmit the full brief.",
                                              is_error=True))
                    continue
                final = valid
                results.append(ToolResult(call.id, "received"))
                continue
            if meta["tool_calls"] >= max_tool_calls:
                results.append(ToolResult(call.id, "tool budget exhausted; call finalize_brief now", is_error=True))
                continue
            meta["tool_calls"] += 1
            if emit:
                emit("agent_tool", {"tool": call.name, "input": call.input})
            try:
                payload = _run_tool(call.name, call.input, toolbox, ctx)
                results.append(ToolResult(call.id, tool_result_text(payload)))
            except (RunCapExceeded, QuotaExceeded) as exc:
                results.append(ToolResult(call.id, f"{exc}; call finalize_brief with the evidence you have", is_error=True))
            except Exception as exc:  # QlooError, bad arguments, anything else
                results.append(ToolResult(call.id, f"tool failed: {exc.__class__.__name__}: {exc}"[:300], is_error=True))
        if final is not None:
            return final, meta
        messages.append(Message("tool", tool_results=results))
    meta["error"] = "agent loop exhausted"
    return None, meta
