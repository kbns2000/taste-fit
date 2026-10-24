"""Exercise the live Qloo adapter once a key exists (never prints the key).

    QLOO_API_KEY=... python scripts/smoke_live.py

Runs the call shapes the product uses and prints counts plus the shapes of
the compare (catalog tags) and explainability (per-signal split) responses,
plus the cross-affinity both ways (the shared-taste input), so the parser can be
checked against live data. Spends ~10 calls.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # run from anywhere: python scripts/<name>.py

from app.config import Settings  # noqa: E402
from app.qloo.client import LiveQlooClient
from app.qloo.models import QlooError


def keys(obj, depth=0, out=None):
    out = out if out is not None else []
    if isinstance(obj, dict):
        for k, v in list(obj.items())[:12]:
            out.append("  " * depth + str(k))
            keys(v, depth + 1, out)
    elif isinstance(obj, list) and obj:
        out.append("  " * depth + f"[list of {len(obj)}]")
        keys(obj[0], depth + 1, out)
    return out


def main() -> int:
    cfg = Settings()
    if not cfg.qloo_api_key:
        print("QLOO_API_KEY not set", file=sys.stderr)
        return 2
    c = LiveQlooClient(cfg.qloo_api_key, cfg.qloo_base_url, timeout_s=cfg.qloo_timeout_s)
    trace = []
    try:
        a = c.search("Patagonia", types=["brand"], trace=trace)
        b = c.search("Bon Iver", types=["artist"], trace=trace)
        print("search:", [e.label() for e in a[:3]], [e.label() for e in b[:3]])
        if not a or not b:
            return 1
        ai, bi = a[0].entity_id, b[0].entity_id
        tags = c.taste_tags([ai], take=50, trace=trace)
        affs = sorted((t.affinity or 0.0) for t in tags)
        print("taste_tags:", [(t.name, t.affinity) for t in tags[:5]],
              f"| {len(tags)} tags, affinity min {affs[0] if affs else None} median {affs[len(affs) // 2] if affs else None}")
        a_to_b = c.affinity_among([ai], [bi], "artist", trace=trace).get(bi)
        b_to_a = c.affinity_among([bi], [ai], "brand", trace=trace).get(ai)
        mean = [v for v in (a_to_b, b_to_a) if v is not None]
        print(f"cross-affinity: Bon Iver among Patagonia fans {a_to_b}, Patagonia among Bon Iver fans {b_to_a}"
              f" -> shared taste {round(max(0.0, (sum(mean) / len(mean) - 0.5) / 0.5 * 100), 1) if mean else None}")
        demo = c.demographics([ai], trace=trace)
        print("demographics:", [(d.entity_id[:8], d.age, d.gender) for d in demo])
        recs = c.recommend([ai, bi], "artist", take=5, trace=trace)
        print("recommend+explainability:", [(e.name, e.affinity, e.explainability) for e in recs[:3]],
              f"| {sum(1 for e in recs if e.explainability)}/{len(recs)} picks carry a per-signal split")
        heat = c.heatmap([ai, bi], "New York", trace=trace)
        print("heatmap cells:", len(heat), heat[:1])
        try:
            cat = c.compare([ai], [bi], trace=trace)
            print(f"compare (catalog tags): a {len(cat.a_tags)}, b {len(cat.b_tags)}, common {len(cat.common)}",
                  [t.name for t in cat.common[:5]], [t.name for t in cat.a_tags[:3]], [t.name for t in cat.b_tags[:3]])
        except QlooError as exc:
            print("compare failed:", exc)
        # raw shape of compare (keys only)
        r = httpx.get(f"{cfg.qloo_base_url}/v2/analysis/compare",
                      params={"a.signal.interests.entities": ai, "b.signal.interests.entities": bi},
                      headers={"X-Api-Key": cfg.qloo_api_key}, timeout=30)
        print("compare raw status", r.status_code)
        try:
            print("\n".join(keys(r.json())[:40]))
        except ValueError:
            print(r.text[:300])
    finally:
        print("--- ledger ---")
        for rec in trace:
            print(json.dumps(rec.as_dict(), ensure_ascii=False)[:300])
    return 0


if __name__ == "__main__":
    sys.exit(main())
