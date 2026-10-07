"""Критерии 1.4 и 1.5: сетка, пересечения, «сегодня», скрытие дней, «Любой мастер»."""

from datetime import date, datetime, timedelta

import pytest

from saqal import slots
from saqal.clock import TZ

DAY = date(2026, 10, 9)
BEFORE_OPEN = datetime(2026, 10, 9, 8, 0, tzinfo=TZ)


def at(hh: int, mm: int = 0, day: date = DAY) -> datetime:
    return datetime(day.year, day.month, day.day, hh, mm, tzinfo=TZ)


def busy(master: str, start: datetime, minutes: int) -> slots.Busy:
    return slots.Busy(master, start, start + timedelta(minutes=minutes))


def hhmm(starts) -> list[str]:
    return [s.strftime("%H:%M") for s in starts]


@pytest.mark.parametrize(
    ("service_id", "minutes", "last"),
    [
        ("clipper", 30, "20:30"),
        ("kids", 45, "20:00"),
        ("men", 60, "20:00"),
        ("dad_son", 90, "19:30"),
    ],
)
def test_last_start_by_duration(shop, service_id, minutes, last):
    """1.4, С1: первый старт 10:00, шаг 30 минут, последний — чтобы закончить к 21:00."""
    service = shop.service(service_id)
    assert service.minutes == minutes
    starts = hhmm(slots.available_starts(shop, service, ["umar"], DAY, [], BEFORE_OPEN))
    assert starts[0] == "10:00"
    assert starts[-1] == last
    assert all(s.endswith((":00", ":30")) for s in starts)


def test_kids_at_ten_blocks_1030_leaves_1100(shop):
    """1.4, С2: детская 10:00–10:45 закрывает 10:30, следующий старт — 11:00."""
    taken = [busy("umar", at(10), 45)]
    starts = hhmm(
        slots.available_starts(shop, shop.service("clipper"), ["umar"], DAY, taken, BEFORE_OPEN)
    )
    assert "10:00" not in starts
    assert "10:30" not in starts
    assert starts[0] == "11:00"


def test_interval_overlap_respects_longer_service(shop):
    """С2: 60-минутная услуга не влезает в 10:30, если в 11:00 уже запись."""
    taken = [busy("umar", at(11), 30)]
    starts = hhmm(
        slots.available_starts(shop, shop.service("men"), ["umar"], DAY, taken, BEFORE_OPEN)
    )
    assert "10:00" in starts
    assert "10:30" not in starts
    assert "11:00" not in starts
    assert "11:30" in starts


def test_today_hides_started_slots(shop):
    """1.4, С3: на сегодня только неначавшиеся слоты; начавшийся ровно сейчас — тоже скрыт."""
    now = at(14, 30)
    starts = hhmm(slots.available_starts(shop, shop.service("clipper"), ["umar"], DAY, [], now))
    assert starts[0] == "15:00"
    now = at(14, 31)
    starts = hhmm(slots.available_starts(shop, shop.service("clipper"), ["umar"], DAY, [], now))
    assert starts[0] == "15:00"


def test_day_without_free_time_is_hidden(shop):
    """1.4, К4: полностью занятый день не показывается; показывается сегодня + 6 дней."""
    service = shop.service("men")
    full = [busy("umar", at(10, day=date(2026, 10, 10)), 11 * 60)]
    days = slots.available_days(shop, service, ["umar"], full, BEFORE_OPEN)
    assert date(2026, 10, 10) not in days
    assert days[0] == DAY
    assert days[-1] == date(2026, 10, 15)
    assert len(days) == 6


def test_today_hidden_after_last_slot(shop):
    """1.4: вечером сегодня свободного времени нет — сегодняшний день скрыт."""
    days = slots.available_days(shop, shop.service("men"), ["umar"], [], at(20, 5))
    assert DAY not in days
    assert days[0] == date(2026, 10, 10)


def test_any_master_shows_slot_if_one_master_free(shop):
    """1.5: слот показан, если свободен хотя бы один мастер; скрыт, если заняты все."""
    service = shop.service("men")
    all_ids = [m.id for m in shop.masters]
    two_busy = [busy("umar", at(12), 60), busy("barber2", at(12), 60)]
    starts = hhmm(slots.available_starts(shop, service, all_ids, DAY, two_busy, BEFORE_OPEN))
    assert "12:00" in starts
    all_busy = [*two_busy, busy("barber3", at(12), 60)]
    starts = hhmm(slots.available_starts(shop, service, all_ids, DAY, all_busy, BEFORE_OPEN))
    assert "12:00" not in starts


def test_pick_master_least_minutes_then_config_order(shop):
    """1.5, А11: меньше минут за день; при равенстве — первый по конфигу."""
    service = shop.service("clipper")
    all_ids = [m.id for m in shop.masters]
    assert slots.pick_master(service, at(15), all_ids, []) == "umar"

    # Умар: 2 записи по 30 = 60 мин, Барбер 2: одна на 90 мин, Барбер 3: 60 мин.
    taken = [
        busy("umar", at(10), 30),
        busy("umar", at(11), 30),
        busy("barber2", at(10), 90),
        busy("barber3", at(12), 60),
    ]
    # Равенство Умар (60) и Барбер 3 (60) — Умар первый по конфигу.
    assert slots.pick_master(service, at(15), all_ids, taken) == "umar"

    # Умар занят в 15:00 — назначается Барбер 3 (60 мин), а не Барбер 2 (90 мин).
    taken.append(busy("umar", at(15), 30))
    assert slots.pick_master(service, at(15), all_ids, taken) == "barber3"


def test_pick_master_counts_minutes_not_bookings(shop):
    """А11: считается сумма минут, а не число записей."""
    service = shop.service("clipper")
    taken = [busy("umar", at(10), 90), busy("barber2", at(10), 30), busy("barber2", at(11), 30)]
    assert slots.pick_master(service, at(15), ["umar", "barber2"], taken) == "barber2"


def test_pick_master_ignores_other_days(shop):
    """А11: загрузка считается только за этот день."""
    service = shop.service("clipper")
    yesterday = date(2026, 10, 8)
    taken = [busy("umar", at(10, day=yesterday), 90 * 3)]
    assert slots.pick_master(service, at(15), ["umar", "barber2"], taken) == "umar"


def test_is_start_available_rejects_off_grid_and_out_of_window(shop):
    """А9: старт не по сетке, после закрытия или вне 7 дней не принимается."""
    service = shop.service("men")
    ok = slots.is_start_available
    assert ok(shop, service, ["umar"], at(10), [], BEFORE_OPEN)
    assert not ok(shop, service, ["umar"], at(10, 15), [], BEFORE_OPEN)
    assert not ok(shop, service, ["umar"], at(20, 30), [], BEFORE_OPEN)
    assert not ok(shop, service, ["umar"], at(10, day=date(2026, 10, 16)), [], BEFORE_OPEN)
