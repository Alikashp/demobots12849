"""Расчёт слотов и выбор мастера (С1–С4, А4, А11).

Без Telegram и без базы: на вход конфиг, занятые интервалы и текущее время.
Все datetime — aware; сетка и «сегодня» считаются в Europe/Moscow.
"""

from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import date, datetime, timedelta

from .clock import TZ
from .config import Service, Shop


@dataclass(frozen=True)
class Busy:
    """Активная запись мастера: интервал [start; end)."""

    master_id: str
    start: datetime
    end: datetime


def local_day_bounds(day: date) -> tuple[datetime, datetime]:
    """Начало и конец календарного дня по Казани."""
    start = datetime.combine(day, datetime.min.time(), tzinfo=TZ)
    return start, start + timedelta(days=1)


def day_grid(shop: Shop, service: Service, day: date) -> list[datetime]:
    """С1: старты по сетке от открытия, услуга заканчивается не позже закрытия."""
    step = timedelta(minutes=shop.slot_step_minutes)
    duration = timedelta(minutes=service.minutes)
    t = datetime.combine(day, shop.open, tzinfo=TZ)
    close = datetime.combine(day, shop.close, tzinfo=TZ)
    starts = []
    while t + duration <= close:
        starts.append(t)
        t += step
    return starts


def is_master_free(master_id: str, start: datetime, end: datetime, busy: Iterable[Busy]) -> bool:
    """С2: интервал не пересекается ни с одной активной записью мастера."""
    return not any(b.master_id == master_id and b.start < end and start < b.end for b in busy)


def free_masters(
    service: Service, start: datetime, master_ids: Sequence[str], busy: Sequence[Busy]
) -> list[str]:
    end = start + timedelta(minutes=service.minutes)
    return [m for m in master_ids if is_master_free(m, start, end, busy)]


def available_starts(
    shop: Shop,
    service: Service,
    master_ids: Sequence[str],
    day: date,
    busy: Sequence[Busy],
    now: datetime,
) -> list[datetime]:
    """С1–С4: старты дня, ещё не начавшиеся и свободные хотя бы у одного мастера."""
    return [
        s
        for s in day_grid(shop, service, day)
        if s > now and free_masters(service, s, master_ids, busy)
    ]


def booking_days(shop: Shop, now: datetime) -> list[date]:
    """К4: сегодня по Казани и следующие дни."""
    today = now.astimezone(TZ).date()
    return [today + timedelta(days=i) for i in range(shop.days_ahead)]


def available_days(
    shop: Shop,
    service: Service,
    master_ids: Sequence[str],
    busy: Sequence[Busy],
    now: datetime,
) -> list[date]:
    """К4: дни, где есть хотя бы один свободный слот."""
    return [
        d
        for d in booking_days(shop, now)
        if available_starts(shop, service, master_ids, d, busy, now)
    ]


def is_start_available(
    shop: Shop,
    service: Service,
    master_ids: Sequence[str],
    start: datetime,
    busy: Sequence[Busy],
    now: datetime,
) -> bool:
    """Полная перепроверка выбранного старта (С5, А9)."""
    day = start.astimezone(TZ).date()
    if day not in booking_days(shop, now):
        return False
    return start in available_starts(shop, service, master_ids, day, busy, now)


def pick_master(
    service: Service, start: datetime, master_ids: Sequence[str], busy: Sequence[Busy]
) -> str | None:
    """С4, А11: из свободных — с меньшей суммой минут за день; при равенстве — первый по конфигу."""
    candidates = free_masters(service, start, master_ids, busy)
    if not candidates:
        return None
    day_start, day_end = local_day_bounds(start.astimezone(TZ).date())

    def load(master_id: str) -> float:
        return sum(
            (b.end - b.start).total_seconds()
            for b in busy
            if b.master_id == master_id and day_start <= b.start < day_end
        )

    return min(candidates, key=lambda m: (load(m), master_ids.index(m)))
