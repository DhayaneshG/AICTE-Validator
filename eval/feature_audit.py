"""Count capabilities by status from FEATURES.md (single source of truth).
  python -m eval.feature_audit                 # print counts
  python -m eval.feature_audit --set 2,4 built "note"   # update rows
"""
from __future__ import annotations

import argparse
import re
from collections import Counter
from pathlib import Path

F = Path(__file__).resolve().parents[1] / "FEATURES.md"
ROW = re.compile(r"^\|\s*(\d+)\s*\|(.*)\|\s*$")


def parse(text: str | None = None):
    rows = []
    for line in (text or F.read_text()).splitlines():
        m = ROW.match(line)
        if m:
            cells = [c.strip() for c in line.strip().strip("|").split("|")]
            rows.append({"n": int(cells[0]), "name": cells[1], "target": cells[2], "status": cells[3], "milestone": cells[4], "notes": cells[5] if len(cells) > 5 else ""})
    return rows


def counts(rows=None) -> dict:
    rows = rows or parse()
    c = Counter(r["status"] for r in rows)
    return {"total": len(rows), "built": c["built"], "partial": c["partial"], "mocked": c["mocked"], "planned": c["planned"],
            "downgraded": c["downgraded"], "not_started": c["not-started"]}


def set_status(nums: list[int], status: str, note: str | None = None):
    out = []
    for line in F.read_text().splitlines():
        m = ROW.match(line)
        if m and int(m.group(1)) in nums:
            cells = [c.strip() for c in line.strip().strip("|").split("|")]
            cells[3] = status
            if note:
                cells[5] = note
            line = "| " + " | ".join(cells) + " |"
        out.append(line)
    F.write_text("\n".join(out) + "\n")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--set", nargs=3, metavar=("NUMS", "STATUS", "NOTE"))
    a = ap.parse_args()
    if a.set:
        set_status([int(x) for x in a.set[0].split(",")], a.set[1], a.set[2])
    c = counts()
    print(c)
