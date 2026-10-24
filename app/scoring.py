"""Taste-Fit score: a short, printed formula over Qloo outputs.

    shared_taste       = (mean(affinity of B among A's fans, affinity of A among B's fans) - 0.5) / 0.5 x 100
                         (Qloo `filter.results.entities`; 0.5 is Qloo's "medium", so medium -> 0, perfect -> 100;
                          falls back to the cosine of the two audiences' tag lists when no cross-affinity came back)
    audience_alignment = (cosine(demographic index A, demographic index B) + 1) / 2 x 100
    fit                = 0.6 shared_taste + 0.4 audience_alignment
    fresh_reach        = share of B's top-15 taste tags not among A's top-20 (shown, not scored)

Why cross-affinity: live tag affinities sit at ~1.0 for an audience's whole top-50, so tag lists say *what*
the audiences share but not *how much*. Qloo's entity-to-entity affinity is a cosine similarity of
audience embeddings and separates related from unrelated pairs (2026-10-24 probe).
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
    a_to_b: Optional[float] = None  # Qloo affinity of B among A's fans
    b_to_a: Optional[float] = None  # Qloo affinity of A among B's fans
    shared_basis: str = "cross_affinity"  # or "tags" when no cross-affinity was available
    shared_tags: list[TagOverlap] = Field(default_factory=list)
    a_only_tags: list[TagOverlap] = Field(default_factory=list)
    b_only_tags: list[TagOverlap] = Field(default_factory=list)
    demographics: list[DemographicDelta] = Field(default_factory=list)
    agreeing_segments: list[str] = Field(default_factory=list)
    diverging_segments: list[str] = Field(default_factory=list)
    formula: str = "fit = 0.6*shared_taste + 0.4*audience_alignment"

    def headline(self) -> str:
        return {
            "strong": "Strong fit: each audience over-indexes on the other and the audiences line up.",
            "workable": "Workable fit: real taste overlap, with some audience stretch.",
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


# Tag families that describe venues or payment, not taste; live top-50 lists for unrelated audiences all
# carry some of them ("Three Star", "Discover", "Inexpensive"), which inflates overlap.
NON_TASTE_TAG_KINDS = {"urn:tag:hotel_rating", "urn:tag:credit_card", "urn:tag:cost_description",
                       "urn:tag:price_range", "urn:tag:payment"}


def is_taste_tag(tag: Tag) -> bool:
    kind = ":".join((tag.type or "").split(":")[:3])
    return kind not in NON_TASTE_TAG_KINDS


def shared_taste_from_affinity(a_to_b: Optional[float], b_to_a: Optional[float]) -> Optional[float]:
    vals = [v for v in (a_to_b, b_to_a) if v is not None]
    if not vals:
        return None
    mean = sum(vals) / len(vals)
    return max(0.0, min(100.0, (mean - 0.5) / 0.5 * 100.0))


def vectors_from_tags(a_rows: list[Tag], b_rows: list[Tag]) -> tuple[dict[str, float], dict[str, float], dict[str, str]]:
    """Audience tag vectors from two `filter.type=urn:tag` insight calls (one per audience)."""
    a = {r.tag_id: float(r.affinity) for r in a_rows if r.affinity and is_taste_tag(r)}
    b = {r.tag_id: float(r.affinity) for r in b_rows if r.affinity and is_taste_tag(r)}
    names = {r.tag_id: r.name for r in a_rows + b_rows}
    return a, b, names


def _demo_vec(d: Optional[Demographics]) -> dict[str, float]:
    if d is None:
        return {}
    out = {f"age:{k}": float(d.age.get(k, 0.0)) for k in AGE_KEYS if k in d.age}
    out.update({f"gender:{k}": float(d.gender.get(k, 0.0)) for k in GENDER_KEYS if k in d.gender})
    return out


def compute_fit(a_vec: dict[str, float], b_vec: dict[str, float], names: dict[str, str],
                a_demo: Optional[Demographics], b_demo: Optional[Demographics],
                a_to_b: Optional[float] = None, b_to_a: Optional[float] = None) -> FitScore:
    cross = shared_taste_from_affinity(a_to_b, b_to_a)
    shared = cross if cross is not None else 100.0 * max(0.0, _cosine(a_vec, b_vec))

    a_top = [k for k, _ in sorted(a_vec.items(), key=lambda kv: -kv[1])[:TOP_A]]
    b_top = [k for k, _ in sorted(b_vec.items(), key=lambda kv: -kv[1])[:TOP_B]]
    fresh = 100.0 * (sum(1 for k in b_top if k not in set(a_top)) / len(b_top)) if b_top else 0.0

    da, db = _demo_vec(a_demo), _demo_vec(b_demo)
    if da and db:
        alignment = 100.0 * (max(-1.0, min(1.0, _cosine(da, db))) + 1.0) / 2.0
    else:
        alignment = 50.0  # unknown -> neutral, flagged in the brief

    fit = 0.6 * shared + 0.4 * alignment

    overlaps = [
        TagOverlap(tag_id=k, name=names.get(k, k), a_affinity=round(a_vec.get(k, 0.0), 3),
                   b_affinity=round(b_vec.get(k, 0.0), 3))
        for k in set(a_vec) | set(b_vec)
    ]
    # live affinities tie at ~1.0, so Qloo's own ranking (dict insertion order) breaks ties
    rank_a = {k: i for i, k in enumerate(a_vec)}
    rank_b = {k: i for i, k in enumerate(b_vec)}
    far = len(rank_a) + len(rank_b)
    shared_tags = sorted((o for o in overlaps if o.a_affinity > 0 and o.b_affinity > 0),
                         key=lambda o: (-round(o.shared, 2), rank_a.get(o.tag_id, far) + rank_b.get(o.tag_id, far)))[:12]
    a_only = sorted((o for o in overlaps if o.a_affinity > 0 and o.b_affinity == 0),
                    key=lambda o: (-round(o.a_affinity, 2), rank_a.get(o.tag_id, far)))[:8]
    b_only = sorted((o for o in overlaps if o.b_affinity > 0 and o.a_affinity == 0),
                    key=lambda o: (-round(o.b_affinity, 2), rank_b.get(o.tag_id, far)))[:8]

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

    fit = round(fit, 1)  # band the number the user sees
    return FitScore(
        fit=fit, band=band_for(fit),
        shared_taste=round(shared, 1), audience_alignment=round(alignment, 1),
        fresh_reach=round(fresh, 1),
        a_to_b=None if a_to_b is None else round(a_to_b, 3), b_to_a=None if b_to_a is None else round(b_to_a, 3),
        shared_basis="cross_affinity" if cross is not None else "tags",
        shared_tags=shared_tags, a_only_tags=a_only, b_only_tags=b_only,
        demographics=deltas, agreeing_segments=agreeing, diverging_segments=diverging,
    )
