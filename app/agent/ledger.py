"""Evidence ledger: every Qloo call the agent made, numbered Q1..Qn.

The brief cites ledger ids, the UI shows them as footnotes, and the verifier
only accepts names that appear in ledger results. This is the hackathon
kit's "request-to-result" trail made first-class.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Iterable, Optional

from ..qloo.client import CallRecord


@dataclass
class LedgerEntry:
    id: str
    record: CallRecord
    purpose: str
    names: list[str] = field(default_factory=list)  # entity / tag names returned

    def as_dict(self) -> dict[str, Any]:
        data = self.record.as_dict()
        data.update({"id": self.id, "purpose": self.purpose, "names": self.names[:50]})
        return data


class Ledger:
    def __init__(self) -> None:
        self.entries: list[LedgerEntry] = []
        self.trace: list[CallRecord] = []  # pass to client methods
        self._known: set[str] = set()

    def __len__(self) -> int:
        return len(self.entries)

    def absorb(self, purpose: str, names: Iterable[str] = ()) -> list[str]:
        """Move any new CallRecords from `trace` into numbered entries."""
        ids = []
        names = [n for n in names if n]
        while len(self.trace) > 0:
            record = self.trace.pop(0)
            entry = LedgerEntry(id=f"Q{len(self.entries) + 1}", record=record, purpose=purpose, names=list(names))
            self.entries.append(entry)
            ids.append(entry.id)
        for name in names:
            self._known.add(_norm(name))
        return ids

    def knows(self, name: str) -> bool:
        return _norm(name) in self._known

    def known_names(self) -> set[str]:
        return set(self._known)

    def has_id(self, cite: str) -> bool:
        return any(e.id == cite for e in self.entries)

    def calls(self) -> int:
        return sum(1 for e in self.entries if not e.record.cached)

    def as_list(self) -> list[dict[str, Any]]:
        return [e.as_dict() for e in self.entries]

    def source(self) -> str:
        sources = {e.record.source for e in self.entries}
        if not sources:
            return "none"
        return "live" if sources == {"live"} else "mock" if sources == {"mock"} else "mixed"


def _norm(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", (text or "").lower()).strip()
