"""Find generic Qloo tags: the ones almost every audience over-indexes on (never prints the key).

    QLOO_API_KEY=... python scripts/calibrate_tags.py --out tag_panel.json

Live tag insights mix taste tags (folk, indie, outdoor clothing) with tags that show up
for nearly any audience (friendship, star ratings, card networks). This asks Qloo for
the top-50 tags of a mixed panel of audiences, counts how many audiences carry each tag,
and prints the subtype and popularity of the common ones so a filter can be chosen on
evidence. Writes the full table as JSON. About 2 calls per panel entry (32 in total).
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
from collections import Counter, defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # run from anywhere: python scripts/<name>.py

from app.config import Settings  # noqa: E402
from app.qloo.client import LiveQlooClient

PANEL = [("Patagonia", "brand"), ("The North Face", "brand"), ("McDonald's", "brand"), ("Nike", "brand"),
         ("Apple", "brand"), ("Starbucks", "brand"), ("IKEA", "brand"), ("Sephora", "brand"),
         ("Tesla", "brand"), ("Chanel", "brand"), ("Bon Iver", "artist"), ("Taylor Swift", "artist"),
         ("Drake", "artist"), ("Metallica", "artist"), ("BTS", "artist"), ("The Joe Rogan Experience", "podcast")]
PAIR = ("Patagonia", "Bon Iver")


def family(subtype: str | None) -> str:
    return ":".join((subtype or "?").split(":")[:3])


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="tag_panel.json")
    args = ap.parse_args()
    cfg = Settings()
    if not cfg.qloo_api_key:
        print("QLOO_API_KEY not set", file=sys.stderr)
        return 2
    c = LiveQlooClient(cfg.qloo_api_key, cfg.qloo_base_url, timeout_s=cfg.qloo_timeout_s)

    panel, lists, info = [], {}, {}
    for name, kind in PANEL:
        hits = c.search(name, types=[kind])
        if not hits:
            print(f"search: {name} not found")
            continue
        rows = c.taste_tags([hits[0].entity_id], take=50)
        panel.append({"name": name, "type": kind, "entity_id": hits[0].entity_id, "resolved": hits[0].name})
        lists[name] = [t.tag_id for t in rows]
        for t in rows:
            info.setdefault(t.tag_id, {"tag_id": t.tag_id, "name": t.name, "subtype": t.type,
                                       "popularity": t.popularity})
        print(f"{name}: {len(rows)} tags")

    carriers: dict[str, list[str]] = defaultdict(list)
    for name, ids in lists.items():
        for tid in ids:
            carriers[tid].append(name)
    n = len(lists)
    table = sorted(({**info[tid], "count": len(who), "share": round(len(who) / n, 3), "audiences": who}
                    for tid, who in carriers.items()), key=lambda r: (-r["count"], r["name"].lower()))
    Path(args.out).write_text(json.dumps({"panel": panel, "panel_size": n, "tags": table}, indent=1,
                                         ensure_ascii=False))

    print(f"\npanel {n} audiences, {len(table)} distinct tags -> {args.out}")
    print("\nsubtype families over all tags:", dict(Counter(family(r["subtype"]) for r in table).most_common(20)))
    for cut in (0.5, 0.375, 0.25):
        common = [r for r in table if r["share"] >= cut]
        print(f"\ntags carried by >= {cut:.0%} of the panel: {len(common)}")
        if cut == 0.25:
            for r in common:
                print(f"  {r['count']:>2}/{n}  {r['name']!r:38} {r['subtype']!s:34} pop {r['popularity']}")
        print("  families:", dict(Counter(family(r["subtype"]) for r in common).most_common(12)))

    pops_common = [r["popularity"] for r in table if r["share"] >= 0.375 and r["popularity"] is not None]
    pops_rest = [r["popularity"] for r in table if r["share"] < 0.25 and r["popularity"] is not None]
    if pops_common and pops_rest:
        print(f"\npopularity median: common tags {statistics.median(pops_common):.3f} "
              f"vs rare tags {statistics.median(pops_rest):.3f} (n={len(pops_common)}/{len(pops_rest)})")

    a, b = PAIR
    if a in lists and b in lists:
        shared = [tid for tid in lists[a] if tid in set(lists[b])]
        print(f"\n{a} x {b}: {len(shared)} shared tags")
        for tid in shared:
            r = next(x for x in table if x["tag_id"] == tid)
            print(f"  {r['name']!r:38} {r['subtype']!s:34} panel {r['count']}/{n} pop {r['popularity']}")
    print(f"\ncalls spent: {c.budget_status().get('calls_today')}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
