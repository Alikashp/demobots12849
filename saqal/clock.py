"""Единственный источник текущего времени (А3). В тестах подменяется `clock.now`."""

from datetime import UTC, datetime
from zoneinfo import ZoneInfo

TZ = ZoneInfo("Europe/Moscow")


def now() -> datetime:
    """Текущий момент, aware, в UTC."""
    return datetime.now(UTC)
