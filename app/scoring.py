"""Taste-Fit score: a transparent, deterministic partnership score.

Inputs are Qloo outputs only (tag affinities and demographic indices), so
every number in the UI can be traced back to a specific API call in the
evidence ledger. The formula is deliberately simple enough to print:

    shared_taste        = cosine(tag affinities of A, tag affinities of B) x 100
    audience_alignment  = (cosine(demographic index A, demographic index B) + 1) / 2 x 100
    fresh_reach         = share of B's top tags that are not among A's top tags x 100
    balance             = 100 - |fresh_reach - 40| x 2.5            (clipped 0..100)
    fit                 = 0.5 shared_taste + 0.3 audience_alignment + 0.2 balance

A partner that is a taste clone scores high on shared taste but brings no
fresh reach (balance drops); a partner with nothing in common has fresh
reach but no shared taste. The sweet spot is a partner whose fans overlap
on a meaningful core and extend the audience by roughly 30-50%.
"""

from __future__ import annotations

import math
from typing import Optional

from pydantic import BaseModel, Field

from .qloo.models import Demographics, Tag

AGE_KEYS = ["24_and_younger", "25_to_29", "30_to_34", "35_to_44", "45_to_54", "55_and_older"]
AGE_LABELS = {
    "24_and_younger": "under 25", "25_to_29": "25-29", "30_to_34": "30-34",
    "35_to_44": "35-44", "45_to_54": "45-54", "55_and_older": "55+",
}
GENDER_KEYS = ["male", "female"]

TOP_A = 20
TOP_B = 15


class TagOverlap(BaseModel):
    tag_id: str
    name: str
    a_affinity: float
    b_affinity: float

    @property
    def shared(self) -> float:
        return min(self.a_affinity, self.b_affinity)


class DemographicDelta(BaseModel):
    key: str
    label: str
    a: float
    b: float


class FitScore(BaseModel):
    fit: float
    band: str  # strong | workable | weak
    shared_taste: float
    audience_alignment: float
    fresh_reach: float
    balance: float
    shared_tags: list[TagOverlap] = Field(default_factory=list)
    a_only_tags: list[TagOverlap] = Field(default_factory=list)
    b_only_tags: list[TagOverlap] = Field(default_factory=list)
    demographics: list[DemographicDelta] = Field(default_factory=list)
    agreeing_segments: list[str] = Field(default_factory=list)
    diverging_segments: list[str] = Field(default_factory=list)
    formula: str = "fit = 0.5*shared_taste + 0.3*audience_alignment + 0.2*balance"

    def headline(self) -> str:
        return {
            "strong": "Strong fit: a real shared taste core plus fresh reach.",
            "workable": "Workable fit: enough overlap to build on, with some stretch.",
            "weak": "Weak fit: audiences barely overlap; treat as a reach play, not a partnership.",
        }[self.band]


def band_for(fit: float) -> str:
    if fit >= 70:
        return "strong"
    if fit >= 55:
        return "workable"
    return "weak"


def _cosine(a: dict[str, float], b: dict[str, float]) -> float:
    keys = set(a) | set(b)
    dot = sum(a.get(k, 0.0) * b.get(k, 0.0) for k in keys)
    na = math.sqrt(sum(v * v for v in a.values()))
    nb = math.sqrt(sum(v * v for v in b.values()))
    if na == 0 or nb == 0:
        return 0.0
    return dot / (na * nb)


def vectors_from_compare(rows: list[Tag]) -> tuple[dict[str, float], dict[str, float], dict[str, str]]:
    """Tag vectors for A and B from `/v2/analysis/compare` rows."""
    a, b, names = {}, {}, {}
    for row in rows:
        names[row.tag_id] = row.name
        # a 0 affinity means "not among this audience's tags": treat as absent
        if row.a_affinity:
            a[row.tag_id] = float(row.a_affinity)
        if row.b_affinity:
            b[row.tag_id] = float(row.b_affinity)
    return a, b, names


def vectors_from_tags(a_rows: list[Tag], b_rows: list[Tag]) -> tuple[dict[str, float], dict[str, float], dict[str, str]]:
    """Tag vectors from two separate `filter.type=urn:tag` insight calls (fallback)."""
    a = {r.tag_id: float(r.affinity) for r in a_rows if r.affinity}
    b = {r.tag_id: float(r.affinity) for r in b_rows if r.affinity}
    names = {r.tag_id: r.name for r in a_rows + b_rows}
    return a, b, names


def _demo_vec(d: Optional[Demographics]) -> dict[str, float]:
    if d is None:
        return {}
    out = {f"age:{k}": float(d.age.get(k, 0.0)) for k in AGE_KEYS if k in d.age}
    out.update({f"gender:{k}": float(d.gender.get(k, 0.0)) for k in GENDER_KEYS if k in d.gender})
    return out


def compute_fit(a_vec: dict[str, float], b_vec: dict[str, float], names: dict[str, str],
                a_demo: Optional[Demographics], b_demo: Optional[Demographics]) -> FitScore:
    shared = 100.0 * max(0.0, _cosine(a_vec, b_vec))

    a_top = [k for k, _ in sorted(a_vec.items(), key=lambda kv: -kv[1])[:TOP_A]]
    b_top = [k for k, _ in sorted(b_vec.items(), key=lambda kv: -kv[1])[:TOP_B]]
    fresh = 100.0 * (sum(1 for k in b_top if k not in set(a_top)) / len(b_top)) if b_top else 0.0
    balance = max(0.0, min(100.0, 100.0 - abs(fresh - 40.0) * 2.5))

    da, db = _demo_vec(a_demo), _demo_vec(b_demo)
    if da and db:
        alignment = 100.0 * (max(-1.0, min(1.0, _cosine(da, db))) + 1.0) / 2.0
    else:
        alignment = 50.0  # unknown -> neutral, flagged in the brief

    fit = 0.5 * shared + 0.3 * alignment + 0.2 * balance

    overlaps = [
        TagOverlap(tag_id=k, name=names.get(k, k), a_affinity=round(a_vec.get(k, 0.0), 3),
                   b_affinity=round(b_vec.get(k, 0.0), 3))
        for k in set(a_vec) | set(b_vec)
    ]
    shared_tags = sorted((o for o in overlaps if o.a_affinity > 0 and o.b_affinity > 0),
                         key=lambda o: -o.shared)[:12]
    a_only = sorted((o for o in overlaps if o.a_affinity > 0 and o.b_affinity == 0),
                    key=lambda o: -o.a_affinity)[:8]
    b_only = sorted((o for o in overlaps if o.b_affinity > 0 and o.a_affinity == 0),
                    key=lambda o: -o.b_affinity)[:8]

    deltas, agreeing, diverging = [], [], []
    if da and db:
        for key in AGE_KEYS + GENDER_KEYS:
            prefix = "age" if key in AGE_KEYS else "gender"
            va, vb = da.get(f"{prefix}:{key}"), db.get(f"{prefix}:{key}")
            if va is None or vb is None:
                continue
            label = AGE_LABELS.get(key, key)
            deltas.append(DemographicDelta(key=key, label=label, a=round(va, 3), b=round(vb, 3)))
            if va > 0.15 and vb > 0.15:
                agreeing.append(label)
            elif (va > 0.15 and vb < -0.15) or (va < -0.15 and vb > 0.15):
                diverging.append(label)

    return FitScore(
        fit=round(fit, 1), band=band_for(fit),
        shared_taste=round(shared, 1), audience_alignment=round(alignment, 1),
        fresh_reach=round(fresh, 1), balance=round(balance, 1),
        shared_tags=shared_tags, a_only_tags=a_only, b_only_tags=b_only,
        demographics=deltas, agreeing_segments=agreeing, diverging_segments=diverging,
    )
