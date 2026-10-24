"""Probe joint-signal tag insights: what fans of A *and* B over-index on (never prints the key).

    QLOO_API_KEY=... python scripts/probe_joint_tags.py

The top-50 tag lists of two audiences share few tags once generic tags are dropped
(median three, and 15 of 120 pairs none, on the 2026-10-24 panel). This asks Qloo for the tags of the
joint audience (`signal.interests.entities=A,B`, `filter.type=urn:tag`) with
explainability, and prints each tag's subtype, affinity, per-signal split and whether
it is generic, next to the single-audience lists. About 11 calls.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # run from anywhere: python scripts/<name>.py

from app.config import Settings  # noqa: E402
from app.qloo.client import LiveQlooClient, _explain_from, _results, _tag_from_raw
from app.scoring import is_taste_tag, tag_stem

NAMES = [("Patagonia", "brand"), ("Bon Iver", "artist"), ("The North Face", "brand"), ("McDonald's", "brand")]
PAIRS = [("Patagonia", "Bon Iver"), ("Patagonia", "The North Face"), ("Patagonia", "McDonald's")]


def main() -> int:
    cfg = Settings()
    if not cfg.qloo_api_key:
        print("QLOO_API_KEY not set", file=sys.stderr)
        return 2
    c = LiveQlooClient(cfg.qloo_api_key, cfg.qloo_base_url, timeout_s=cfg.qloo_timeout_s)
    ids, single = {}, {}
    for name, kind in NAMES:
        hits = c.search(name, types=[kind])
        if not hits:
            print(f"search: {name} not found")
            continue
        ids[name] = hits[0].entity_id
        single[name] = {tag_stem(t.name) for t in c.taste_tags([hits[0].entity_id], take=50) if is_taste_tag(t)}

    for x, y in PAIRS:
        if x not in ids or y not in ids:
            continue
        sig = [ids[x], ids[y]]
        payload = c._get("/v2/insights", {"filter.type": "urn:tag", "signal.interests.entities": sig,
                                          "take": 50, "feature.explainability": True}, None, "tags")
        raw = _results(payload, "tags")
        print(f"\n=== {x} + {y}: {len(raw)} joint tags ===")
        if raw and isinstance(raw[0], dict):
            q = raw[0].get("query") or {}
            print("first tag query keys:", sorted(q), "| explainability:", str(q.get("explainability"))[:200])
        kept = both = 0
        for i, r in enumerate(raw, 1):
            t = _tag_from_raw(r)
            if t is None:
                continue
            split = _explain_from((r.get("query") or {}).get("explainability"), sig)
            sa, sb = split.get(ids[x].lower(), split.get(ids[x])), split.get(ids[y].lower(), split.get(ids[y]))
            taste = is_taste_tag(t)
            key = tag_stem(t.name)
            where = ("A" if key in single.get(x, set()) else "-") + ("B" if key in single.get(y, set()) else "-")
            kept += taste
            both += taste and where == "AB"
            print(f"{i:>2} {'  ' if taste else 'G '}{t.name!r:34} {t.type!s:30} aff {t.affinity} "
                  f"split {sa}/{sb} in-single {where}")
        print(f"taste tags kept {kept}/{len(raw)}; also in both single top-50s: {both}")
    print(f"\ncalls spent: {c.budget_status().get('calls_today')}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
