"""Критерий 1.7: сервер в UTC, а слоты, «сегодня» и дата в Т1 — по Europe/Moscow."""

import os
import time
from datetime import UTC, date, datetime

import pytest

from saqal import slots, texts
from saqal.clock import TZ


@pytest.fixture
def server_in_utc():
    old = os.environ.get("TZ")
    os.environ["TZ"] = "UTC"
    time.tzset()
    yield
    if old is None:
        del os.environ["TZ"]
    else:
        os.environ["TZ"] = old
    time.tzset()


def test_server_is_really_utc(server_in_utc):
    assert time.localtime().tm_gmtoff == 0


def test_today_is_kazan_date_after_utc_midnight_shift(server_in_utc, shop):
    """22:30 UTC 8 октября = 01:30 9 октября по Казани: «сегодня» — 9 октября."""
    now = datetime(2026, 10, 8, 22, 30, tzinfo=UTC)
    assert slots.booking_days(shop, now)[0] == date(2026, 10, 9)
    days = slots.available_days(shop, shop.service("men"), ["umar"], [], now)
    assert days[0] == date(2026, 10, 9)


def test_slots_grid_in_kazan_time(server_in_utc, shop):
    """Сетка 10:00–21:00 по Казани = 07:00–18:00 UTC."""
    now = datetime(2026, 10, 8, 22, 30, tzinfo=UTC)
    starts = slots.available_starts(
        shop, shop.service("clipper"), ["umar"], date(2026, 10, 9), [], now
    )
    assert starts[0] == datetime(2026, 10, 9, 7, 0, tzinfo=UTC)
    assert starts[0].astimezone(TZ).strftime("%H:%M") == "10:00"
    assert starts[-1].astimezone(TZ).strftime("%H:%M") == "20:30"


def test_started_slots_by_kazan_time(server_in_utc, shop):
    """11:10 UTC = 14:10 по Казани: первый свободный старт сегодня — 14:30."""
    now = datetime(2026, 10, 9, 11, 10, tzinfo=UTC)
    starts = slots.available_starts(
        shop, shop.service("clipper"), ["umar"], date(2026, 10, 9), [], now
    )
    assert texts.fmt_time(starts[0]) == "14:30"


def test_confirmation_date_in_kazan_time(server_in_utc, shop):
    """Старт 9 октября 00:30 по Казани хранится как 8 октября 21:30 UTC; в Т1 — 9 октября."""
    start = datetime(2026, 10, 8, 21, 30, tzinfo=UTC)
    t1 = texts.confirmation(shop, "Иван", shop.service("men"), shop.masters[0], start)
    assert "⌚ 9 октября (пт) в 00:30" in t1
