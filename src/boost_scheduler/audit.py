"""Append-only audit log with a SHA-256 hash chain.

Each event stores the hash of the previous event, so editing, deleting or
re-ordering any past entry breaks every hash after it. ``verify()`` detects
that. This is *tamper-evident*, not tamper-proof: anchor the latest hash
somewhere external if you need a stronger guarantee.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

GENESIS_HASH = "0" * 64


@dataclass(frozen=True)
class AuditEvent:
    seq: int
    ts: str  # ISO-8601, UTC offset included
    actor_id: str
    action: str
    run_id: str | None
    details: dict[str, Any]
    prev_hash: str
    hash: str


def _digest(
    seq: int,
    ts: str,
    actor_id: str,
    action: str,
    run_id: str | None,
    details: dict[str, Any],
    prev_hash: str,
) -> str:
    payload = json.dumps(
        [seq, ts, actor_id, action, run_id, details, prev_hash],
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


class AuditLog:
    def __init__(self) -> None:
        self._events: list[AuditEvent] = []

    def __len__(self) -> int:
        return len(self._events)

    @property
    def head_hash(self) -> str:
        return self._events[-1].hash if self._events else GENESIS_HASH

    def record(
        self,
        *,
        ts: datetime,
        actor_id: str,
        action: str,
        run_id: str | None = None,
        **details: Any,
    ) -> AuditEvent:
        """Append one event. ``details`` must be JSON-serializable."""
        seq = len(self._events) + 1
        ts_iso = ts.isoformat()
        prev = self.head_hash
        digest = _digest(seq, ts_iso, actor_id, action, run_id, details, prev)
        event = AuditEvent(seq, ts_iso, actor_id, action, run_id, details, prev, digest)
        self._events.append(event)
        return event

    def events(self, *, run_id: str | None = None, action: str | None = None) -> list[AuditEvent]:
        return [
            e
            for e in self._events
            if (run_id is None or e.run_id == run_id) and (action is None or e.action == action)
        ]

    def verify(self) -> bool:
        """Recompute the whole chain; ``False`` means something was altered."""
        prev = GENESIS_HASH
        for i, e in enumerate(self._events, start=1):
            expected = _digest(e.seq, e.ts, e.actor_id, e.action, e.run_id, e.details, prev)
            if e.seq != i or e.prev_hash != prev or e.hash != expected:
                return False
            prev = e.hash
        return True

    def to_jsonl(self, path: str | Path) -> None:
        with Path(path).open("w", encoding="utf-8") as fh:
            for e in self._events:
                fh.write(json.dumps(asdict(e), ensure_ascii=False, sort_keys=True) + "\n")
