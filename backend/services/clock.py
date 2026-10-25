"""Clock abstraction with a simulated-time helper (used by alert/escalation tests and the alert-timeliness report)."""
from __future__ import annotations

import datetime as dt
from contextlib import contextmanager

UTC = dt.timezone.utc


class Clock:
    def __init__(self):
        self._offset = dt.timedelta(0)
        self._fixed: dt.datetime | None = None

    def now(self) -> dt.datetime:
        return self._fixed if self._fixed is not None else dt.datetime.now(UTC) + self._offset

    def today(self) -> dt.date:
        return self.now().date()

    # --- simulation helpers (tests / demo only)
    def set(self, when: dt.datetime) -> None:
        self._fixed = when if when.tzinfo else when.replace(tzinfo=UTC)

    def advance(self, **kw) -> dt.datetime:
        if self._fixed is not None:
            self._fixed += dt.timedelta(**kw)
        else:
            self._offset += dt.timedelta(**kw)
        return self.now()

    def reset(self) -> None:
        self._offset = dt.timedelta(0)
        self._fixed = None


clock = Clock()


@contextmanager
def frozen(when: dt.datetime):
    old = (clock._fixed, clock._offset)
    clock.set(when)
    try:
        yield clock
    finally:
        clock._fixed, clock._offset = old


def utcnow() -> dt.datetime:
    return clock.now()
