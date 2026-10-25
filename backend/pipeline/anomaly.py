"""Anomaly indicators: PDF metadata inconsistency and SHA-256 duplicate files."""
from __future__ import annotations

import datetime as dt


def metadata_offset_days(creation_date: str | None, issue_date: str | None) -> int | None:
    """creation_date - stated issue date, in days (None if either is unavailable)."""
    if not creation_date or not issue_date:
        return None
    return (dt.date.fromisoformat(creation_date) - dt.date.fromisoformat(issue_date)).days


def metadata_inconsistent(offset: int | None, before_max: int, after_max: int) -> bool:
    """PDF creation date is inconsistent with the stated issue date if the file claims to have been created
    more than `before_max` days BEFORE the document's own issue date, or more than `after_max` days after it."""
    return offset is not None and (offset < -before_max or offset > after_max)


def find_duplicate(sha: str, seen: dict[str, tuple[str, str]], application_id: str) -> tuple[str, str] | None:
    """Return (application_id, document_id) of an earlier occurrence of the same file in a different application."""
    hit = seen.get(sha)
    if hit and hit[0] != application_id:
        return hit
    return None
