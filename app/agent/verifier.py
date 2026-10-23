"""Checks a brief against the evidence ledger before it reaches the user.

Three questions: does every citation exist, is every named entity something
Qloo actually returned, and does the verdict agree with the score? Failures
do not block the brief; they are shown next to it so the reader can judge.
"""

from __future__ import annotations

import re
from typing import Any

from .ledger import Ledger

VERDICT_FOR_BAND = {"strong": {"go", "go_with_conditions"}, "workable": {"go_with_conditions", "go"},
                    "weak": {"no_go", "go_with_conditions"}}


def verify_brief(brief: dict[str, Any], ledger: Ledger, fit: dict[str, Any], known_inputs: list[str]) -> dict[str, Any]:
    issues: list[dict[str, str]] = []
    checked = {"citations": 0, "entities": 0}
    known = {n.lower() for n in known_inputs}

    def check_cites(cites: list[str], where: str) -> None:
        for cite in cites or []:
            checked["citations"] += 1
            if not ledger.has_id(str(cite).strip()):
                issues.append({"level": "error", "where": where, "text": f"citation {cite} is not in the ledger"})

    for i, why in enumerate(brief.get("why") or []):
        check_cites(why.get("cites") or [], f"why[{i}]")
        if not why.get("cites"):
            issues.append({"level": "warn", "where": f"why[{i}]", "text": "claim has no citation"})

    for i, act in enumerate(brief.get("activations") or []):
        check_cites(act.get("cites") or [], f"activations[{i}]")
        kept, dropped = [], []
        for name in act.get("entities") or []:
            checked["entities"] += 1
            if ledger.knows(name) or name.lower() in known:
                kept.append(name)
            else:
                dropped.append(name)
        if dropped:
            issues.append({"level": "error", "where": f"activations[{i}]",
                           "text": "not in Qloo evidence, removed: " + ", ".join(dropped)})
            act["entities"] = kept
            act["unverified_entities"] = dropped

    verdict = brief.get("verdict")
    band = fit.get("band")
    if band in VERDICT_FOR_BAND and verdict not in VERDICT_FOR_BAND[band]:
        issues.append({"level": "warn", "where": "verdict",
                       "text": f"verdict '{verdict}' is unusual for a {band} score ({fit.get('fit')})"})

    # Numbers in the headline must match the computed score if they claim to be it
    headline = brief.get("headline") or ""
    for num in re.findall(r"(\d{1,3}(?:\.\d+)?)\s*/\s*100", headline):
        if abs(float(num) - float(fit.get("fit", 0))) > 1:
            issues.append({"level": "error", "where": "headline",
                           "text": f"score {num}/100 in headline differs from computed {fit.get('fit')}"})

    passed = not any(i["level"] == "error" for i in issues)
    return {"passed": passed, "issues": issues, "checked": checked}
