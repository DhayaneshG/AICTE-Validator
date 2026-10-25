"""MOCK AICTE records retrieval (synthetic institution records; NOT real AICTE data)."""
from __future__ import annotations

import json
from pathlib import Path

RECORDS_PATH = Path(__file__).with_name("records.json")
SOURCE = "MOCK AICTE records"


class MockRecords:
    def __init__(self, records: list[dict] | None = None, path: Path | None = None):
        if records is None:
            records = json.loads(Path(path or RECORDS_PATH).read_text())["institutions"]
        self.by_id = {r["institution_id"]: r for r in records}

    def get(self, institution_id: str | None) -> dict | None:
        if not institution_id:
            return None
        r = self.by_id.get(institution_id)
        return dict(r, source=SOURCE) if r else None
