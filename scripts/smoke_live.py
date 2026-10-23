"""Exercise the live Qloo adapter once a key exists (never prints the key).

    QLOO_API_KEY=... python scripts/smoke_live.py

Runs the seven call shapes the product uses and prints counts plus the
top-level keys of the raw /v2/analysis/compare payload, whose exact shape is
not documented, so the parser can be adjusted if needed. Spends ~8 calls.
"""

from __future__ import annotations

import json
import os
import sys

import httpx

from app.config import Settings
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
        tags = c.taste_tags([ai], take=10, trace=trace)
        print("taste_tags:", [(t.name, t.affinity) for t in tags[:5]])
        demo = c.demographics([ai], trace=trace)
        print("demographics:", [(d.entity_id[:8], d.age, d.gender) for d in demo])
        recs = c.recommend([ai, bi], "artist", take=5, trace=trace)
        print("recommend+explainability:", [(e.name, e.affinity, e.explainability) for e in recs[:3]])
        heat = c.heatmap([ai, bi], "New York", trace=trace)
        print("heatmap cells:", len(heat), heat[:1])
        try:
            cmp_rows = c.compare([ai], [bi], trace=trace)
            print("compare:", [(t.name, t.a_affinity, t.b_affinity, t.delta) for t in cmp_rows[:5]])
        except QlooError as exc:
            print("compare parse fallback needed:", exc)
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
