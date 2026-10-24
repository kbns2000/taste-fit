"""Calibration probe for the Taste-Fit score on live Qloo data (never prints the key).

    QLOO_API_KEY=... python scripts/probe_calibration.py

Live tag affinities sit at ~1.0 for an audience's whole top-50, so the shared-taste
term needs a reference scale. For a handful of reference pairs this prints how much
the two audiences' top-50 tag lists overlap (count, Jaccard, rank-biased overlap),
the tag subtypes involved, and what `filter.results.entities` returns as the
cross-affinity of B among A's fans. Numbers and names only. About 14 calls.
"""

from __future__ import annotations

import sys
from pathlib import Path
from collections import Counter

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # run from anywhere: python scripts/<name>.py

from app.config import Settings  # noqa: E402
from app.qloo.client import LiveQlooClient, _results, _tag_from_raw
from app.qloo.models import to_urn

NAMES = [("Patagonia", "brand"), ("The North Face", "brand"), ("Bon Iver", "artist"),
         ("Taylor Swift", "artist"), ("McDonald's", "brand")]
PAIRS = [("Patagonia", "The North Face"), ("Patagonia", "Bon Iver"), ("Patagonia", "Taylor Swift"),
         ("Patagonia", "McDonald's"), ("Bon Iver", "Taylor Swift"), ("The North Face", "McDonald's")]
CROSS = [("Patagonia", "Bon Iver"), ("Bon Iver", "Patagonia"), ("Patagonia", "The North Face"), ("Patagonia", "McDonald's")]


def rbo(a: list[str], b: list[str], p: float = 0.9) -> float:
    """Rank-biased overlap (Webber et al. 2010), truncated at the list length."""
    depth = min(len(a), len(b))
    if not depth:
        return 0.0
    seen_a, seen_b, score = set(), set(), 0.0
    for d in range(1, depth + 1):
        seen_a.add(a[d - 1]); seen_b.add(b[d - 1])
        score += (p ** (d - 1)) * len(seen_a & seen_b) / d
    return round((1 - p) * score, 4)


def main() -> int:
    cfg = Settings()
    if not cfg.qloo_api_key:
        print("QLOO_API_KEY not set", file=sys.stderr)
        return 2
    c = LiveQlooClient(cfg.qloo_api_key, cfg.qloo_base_url, timeout_s=cfg.qloo_timeout_s)
    ids, types, tags = {}, {}, {}
    for name, kind in NAMES:
        hits = c.search(name, types=[kind])
        if not hits:
            print(f"search: {name} not found"); continue
        ids[name], types[name] = hits[0].entity_id, kind
        payload = c._get("/v2/insights", {"filter.type": "urn:tag", "signal.interests.entities": [hits[0].entity_id],
                                          "take": 50}, None, "tags")
        raw = _results(payload, "tags")
        rows = [t for t in (_tag_from_raw(r) for r in raw) if t]
        tags[name] = rows
        affs = sorted(t.affinity or 0 for t in rows)
        print(f"\n{name}: {len(rows)} tags, affinity min {affs[0] if affs else None} max {affs[-1] if affs else None}")
        print("  subtypes:", dict(Counter((t.type or "?").rsplit(":", 1)[0] for t in rows).most_common(6)))
        print("  top 8:", [t.name for t in rows[:8]])
        if raw and isinstance(raw[0], dict):
            q = raw[0].get("query") or {}
            print("  first tag query fields:", {k: v for k, v in q.items() if not isinstance(v, (dict, list))})
    print("\n--- pair overlap of the two audiences' top-50 tags ---")
    for x, y in PAIRS:
        if x not in tags or y not in tags:
            continue
        a = [t.tag_id for t in tags[x]]; b = [t.tag_id for t in tags[y]]
        shared = set(a) & set(b)
        names = [t.name for t in tags[x] if t.tag_id in shared]
        jac = len(shared) / len(set(a) | set(b)) if a or b else 0
        print(f"{x} x {y}: shared {len(shared)}/50, jaccard {jac:.3f}, rbo {rbo(a, b)}; shared: {names[:8]}")
    print("\n--- cross-affinity: B among A's fans (filter.results.entities) ---")
    for x, y in CROSS:
        if x not in ids or y not in ids:
            continue
        payload = c._get("/v2/insights", {"filter.type": to_urn(types[y]), "signal.interests.entities": [ids[x]],
                                          "filter.results.entities": [ids[y]]}, None, "entities")
        ents = _results(payload, "entities")
        if not ents:
            print(f"{y} among fans of {x}: no result"); continue
        q = ents[0].get("query") or {}
        print(f"{y} among fans of {x}:", {k: v for k, v in q.items() if not isinstance(v, (dict, list))})
    print(f"\ncalls spent: {c.budget_status().get('calls_today')}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
