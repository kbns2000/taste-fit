"""Find generic Qloo tags: ones that many unrelated audiences over-index on (never prints the key).

    QLOO_API_KEY=... python scripts/calibrate_tags.py --out tag_panel.json
    python scripts/calibrate_tags.py --from tag_panel.json --write-generic app/qloo/generic_tags.json --measured 2026-10-24

Live tag insights mix taste tags (folk, outdoor clothing) with tags that show up for
many unrelated audiences (friendship, star ratings, card networks). This asks Qloo for
the top-50 tags of a mixed panel of audiences, counts how many audiences carry each tag,
and prints the subtype and popularity of the common ones so a filter can be chosen on
evidence. Writes the full table as JSON. About 2 calls per panel entry (32 in total).
With --write-generic it saves the tags the score should ignore (rule below); --from
rebuilds that file from a saved table without calling Qloo.
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
# Generic = in the top 50 of at least half the panel, or a quarter for venue tags (subtype "...:place"),
# whose categories ("Restaurant", "Bar", "Tourist attraction") say little about taste unless rare.
# Music genres are never generic: the panel is mostly brands, and "rock" or "indie" is exactly what two
# artists' audiences should be able to share.
GENERIC_SHARE = 0.5
GENERIC_PLACE_SHARE = 0.25
NEVER_GENERIC = ("urn:tag:genre:music", "urn:tag:subgenre")


def generic_tags(table: list[dict]) -> list[dict]:
    out = []
    for r in table:
        subtype = str(r.get("subtype") or "")
        if subtype.startswith(NEVER_GENERIC):
            continue
        cut = GENERIC_PLACE_SHARE if subtype.endswith(":place") else GENERIC_SHARE
        if r["share"] >= cut:
            out.append({"tag_id": r["tag_id"], "name": r["name"], "subtype": r["subtype"], "count": r["count"]})
    return out


def write_generic(path: str, data: dict) -> None:
    tags = generic_tags(data["tags"])
    Path(path).write_text(json.dumps({
        "source": "scripts/calibrate_tags.py on the live Qloo Insights API",
        "measured": data.get("measured"),
        "panel": [p["name"] for p in data["panel"]],
        "rule": (f"top-50 tag of >= {GENERIC_SHARE:.0%} of the panel (>= {GENERIC_PLACE_SHARE:.0%} for ...:place tags);"
                 " music genres never"),
        "tags": tags,
    }, indent=1, ensure_ascii=False) + "\n")
    print(f"{len(tags)} generic tags -> {path}")


def family(subtype: str | None) -> str:
    return ":".join((subtype or "?").split(":")[:3])


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="tag_panel.json")
    ap.add_argument("--from", dest="src", help="rebuild from a saved panel table instead of calling Qloo")
    ap.add_argument("--write-generic", help="write the generic-tag list the score ignores")
    ap.add_argument("--measured", help="date stamp for the generic-tag file, e.g. 2026-10-24")
    args = ap.parse_args()
    if args.src:
        data = json.loads(Path(args.src).read_text())
        data["measured"] = args.measured or data.get("measured")
        if args.write_generic:
            write_generic(args.write_generic, data)
        return 0
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
    data = {"panel": panel, "panel_size": n, "measured": args.measured, "tags": table}
    Path(args.out).write_text(json.dumps(data, indent=1, ensure_ascii=False))
    if args.write_generic:
        write_generic(args.write_generic, data)

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
