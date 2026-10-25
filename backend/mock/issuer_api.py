"""MOCK issuer verification service (stands in for real NOC/affiliation issuers; NOT a real integration).

Reads backend/mock/issuer_registry.json. Entries may carry `simulate_outage_attempts: n`: the first n lookups of that
reference raise SourceUnavailable (exercises retry/backoff). `outage_all=True` simulates a total outage.
Every response is labelled source="MOCK issuer registry".
"""
from __future__ import annotations

import json
from pathlib import Path

REGISTRY_PATH = Path(__file__).with_name("issuer_registry.json")
SOURCE = "MOCK issuer registry"


class SourceUnavailable(Exception):
    pass


class MockIssuerAPI:
    def __init__(self, entries: list[dict] | None = None, outage_all: bool = False, path: Path | None = None):
        if entries is None:
            entries = json.loads(Path(path or REGISTRY_PATH).read_text())["entries"]
        self.entries = {e["ref_id"]: e for e in entries}
        self.outage_all = outage_all
        self.calls: dict[str, int] = {}

    def reset(self):
        self.calls.clear()

    def lookup(self, ref_id: str) -> dict:
        self.calls[ref_id] = self.calls.get(ref_id, 0) + 1
        e = self.entries.get(ref_id)
        if self.outage_all or (e and self.calls[ref_id] <= e.get("simulate_outage_attempts", 0)):
            raise SourceUnavailable(f"{SOURCE}: service unavailable (simulated)")
        if not e:
            return {"source": SOURCE, "found": False, "ref_id": ref_id}
        rec = {k: v for k, v in e.items() if k != "simulate_outage_attempts"}
        return {"source": SOURCE, "found": True, "ref_id": ref_id, "record": rec}
