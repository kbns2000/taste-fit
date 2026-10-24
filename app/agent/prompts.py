"""System prompt and evidence rendering for the writer agent."""

from __future__ import annotations

import json
from typing import Any

SYSTEM_PROMPT = """You are Taste-Fit, a brand-partnership strategist. You turn Qloo Taste AI evidence
into a short, decision-ready partnership brief.

Rules you never break:
1. Evidence only. Every claim about audiences, tastes, places or people must cite the ledger ids
   you were given (Q1, Q2, ...) in `cites`. Never invent entities, numbers or audience facts.
   If you want more evidence, call a tool; each tool result comes with new ledger ids.
2. Name only entities that appear in the evidence (the `names` lists) - exact spelling.
3. Qloo affinities are aggregate, privacy-safe signals about audiences, not facts about
   individuals. Write about "fans of X" and "the audience", never about people.
4. Be concrete and brief: a marketer should be able to act on it in a week.
5. The Taste-Fit score is already computed; do not recompute or contradict it. If it is weak,
   say so plainly and suggest what would have to be true for the partnership to work.
6. Finish by calling `finalize_brief` exactly once. Do not write the brief as plain text.

Budget: at most {max_tools} extra tool calls. Use them only when they would change the brief
(for example: a place list for the chosen city, or a second activation type)."""


def _catalog_line(ctx: dict[str, Any]) -> str:
    cat = ctx.get("catalog")
    if not cat:
        return "Qloo catalog compare: not available"
    cites = ", ".join(ctx.get("catalog_cites") or [])
    common = ", ".join(cat["common"][:8]) or "none"
    return (f"Qloo catalog compare (tags attached to the entities themselves, not audience taste) [{cites}]: "
            f"{cat['common_count']} in common ({common}); A has {cat['a_count']} catalog tags, B has {cat['b_count']}")


def render_evidence(ctx: dict[str, Any]) -> str:
    fit = ctx["fit"]
    a, b = ctx["a"], ctx["b"]
    lines = [
        f"MODE: {ctx['mode']}",
        f"DATA SOURCE: {ctx['source']} ({'Qloo Insights API' if ctx['source'] == 'live' else 'demo data shaped like Qloo responses'})",
        f"A: {a['name']} ({a['type']}) - {a.get('description') or ''}".rstrip(" -"),
        f"B: {b['name']} ({b['type']}) - {b.get('description') or ''}".rstrip(" -"),
        f"CITY: {ctx.get('city') or 'none given'}",
        "",
        f"TASTE-FIT SCORE: {fit['fit']}/100 ({fit['band']}) "
        f"= 0.5*shared_taste {fit['shared_taste']} + 0.3*audience_alignment {fit['audience_alignment']} "
        f"+ 0.2*balance {fit['balance']} (fresh_reach {fit['fresh_reach']}%)  cites: {', '.join(ctx['fit_cites'])}",
        "Shared tags (A affinity / B affinity): " + ", ".join(
            f"{t['name']} ({t['a_affinity']}/{t['b_affinity']})" for t in fit["shared_tags"][:10]) or "none",
        "Only A: " + ", ".join(t["name"] for t in fit["a_only_tags"][:6]),
        "Only B (fresh reach): " + ", ".join(t["name"] for t in fit["b_only_tags"][:6]),
        "Demographic index (A / B, positive = over-index): " + ", ".join(
            f"{d['label']} {d['a']:+.2f}/{d['b']:+.2f}" for d in fit["demographics"]),
        f"Segments both over-index: {', '.join(fit['agreeing_segments']) or 'none'}; "
        f"diverging: {', '.join(fit['diverging_segments']) or 'none'}",
        _catalog_line(ctx),
        "",
        "JOINT RECOMMENDATIONS (fans of A + B; driver from feature.explainability):",
    ]
    for kind, picks in ctx["picks"].items():
        cites = ", ".join(ctx["pick_cites"].get(kind, []))
        lines.append(f"- {kind} [{cites}]: " + "; ".join(
            f"{p['name']} (affinity {p['affinity']}, driver {p['driver']}"
            + (f", {p['locality']}" if p.get("locality") else "") + ")" for p in picks[:8]) or "none")
    if ctx.get("zones"):
        lines.append("HEATMAP HOT ZONES in city [" + ", ".join(ctx["zone_cites"]) + "]: " + "; ".join(
            f"({z['lat']:.3f},{z['lon']:.3f}) affinity {z['affinity']}"
            + (f" near {z['near']}" if z.get("near") else "") for z in ctx["zones"]))
    lines += ["", "LEDGER:"]
    for e in ctx["ledger"]:
        lines.append(f"{e['id']}: {e['endpoint']} - {e['purpose']} -> {e['count']} results"
                     + (f"; names: {', '.join(e['names'][:12])}" if e.get("names") else ""))
    return "\n".join(lines)


def user_instruction(ctx: dict[str, Any]) -> str:
    return (
        "Write the partnership brief for A x B using only the evidence below, then call finalize_brief.\n\n"
        + render_evidence(ctx)
    )


def tool_result_text(payload: Any) -> str:
    return json.dumps(payload, ensure_ascii=False, default=str)[:6000]
