"""Tiện ích thời gian: UTC-aware cho persistence, monotonic cho response time."""

from __future__ import annotations

import time as _time
from datetime import datetime, timezone


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def monotonic() -> float:
    return _time.monotonic()


def local_date_str(now: datetime | None = None) -> str:
    """Ngày giờ địa phương dạng YYYY-MM-DD (cho daily streak)."""
    now = now or datetime.now()
    return now.date().isoformat()


def parse_iso(value: str) -> datetime:
    return datetime.fromisoformat(value)
