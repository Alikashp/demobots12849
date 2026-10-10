"""Расчёт слотов и выбор мастера (С1–С6, А4, А11, А15).

Без Telegram и без базы: на вход конфиг, график мастеров, занятые интервалы и текущее время.
Все datetime — aware; сетка, «сегодня», дни недели и часы считаются в Europe/Moscow.
"""

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta

from .clock import TZ
from .config import Service, Shop


@dataclass(frozen=True)
class Busy:
    """Активная запись мастера: интервал [start; end)."""

    master_id: str
    start: datetime
    end: datetime


Hours = tuple[time, time]


@dataclass(frozen=True)
class Schedule:
    """График мастеров (АР1, АР2). Мастер без шаблона работает весь режим (Д6).

    weekly[мастер][день недели 0–6] — (начало, конец) или None, если день выходной.
    days_off — выходные на даты: пары (мастер, дата).
    """

    weekly: Mapping[str, Mapping[int, Hours | None]] = field(default_factory=dict)
    days_off: frozenset[tuple[str, date]] = frozenset()

    def hours(self, master_id: str, day: date, shop: Shop) -> Hours | None:
        """Рабочие часы мастера в этот день по Казани; None — не работает."""
        if (master_id, day) in self.days_off:
            return None
        template = self.weekly.get(master_id)
        if template is None or day.weekday() not in template:
            return (shop.open, shop.close)
        return template[day.weekday()]

    def works(self, master_id: str, start: datetime, end: datetime) -> bool:
        """С6, АР3: интервал целиком внутри рабочих часов мастера."""
        local_start, local_end = start.astimezone(TZ), end.astimezone(TZ)
        day = local_start.date()
        if (master_id, day) in self.days_off:
            return False
        template = self.weekly.get(master_id)
        if template is None or day.weekday() not in template:
            return True  # весь режим: границы уже задаёт сетка (С1)
        hours = template[day.weekday()]
        if hours is None:
            return False
        work_start = datetime.combine(day, hours[0], tzinfo=TZ)
        work_end = datetime.combine(day, hours[1], tzinfo=TZ)
        return work_start <= local_start and local_end <= work_end


FULL_TIME = Schedule()


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
    service: Service,
    start: datetime,
    master_ids: Sequence[str],
    busy: Sequence[Busy],
    schedule: Schedule = FULL_TIME,
) -> list[str]:
    """Мастера, которые в этот интервал работают (АР3) и свободны (С2)."""
    end = start + timedelta(minutes=service.minutes)
    return [
        m
        for m in master_ids
        if schedule.works(m, start, end) and is_master_free(m, start, end, busy)
    ]


def available_starts(
    shop: Shop,
    service: Service,
    master_ids: Sequence[str],
    day: date,
    busy: Sequence[Busy],
    now: datetime,
    schedule: Schedule = FULL_TIME,
) -> list[datetime]:
    """С1–С6: старты дня, ещё не начавшиеся, у хотя бы одного работающего свободного мастера."""
    return [
        s
        for s in day_grid(shop, service, day)
        if s > now and free_masters(service, s, master_ids, busy, schedule)
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
    schedule: Schedule = FULL_TIME,
) -> list[date]:
    """К4, АР3: дни, где есть хотя бы один свободный слот в рабочее время."""
    return [
        d
        for d in booking_days(shop, now)
        if available_starts(shop, service, master_ids, d, busy, now, schedule)
    ]


def is_start_available(
    shop: Shop,
    service: Service,
    master_ids: Sequence[str],
    start: datetime,
    busy: Sequence[Busy],
    now: datetime,
    schedule: Schedule = FULL_TIME,
) -> bool:
    """Полная перепроверка выбранного старта (С5, С6, А9)."""
    day = start.astimezone(TZ).date()
    if day not in booking_days(shop, now):
        return False
    return start in available_starts(shop, service, master_ids, day, busy, now, schedule)


def pick_master(
    service: Service,
    start: datetime,
    master_ids: Sequence[str],
    busy: Sequence[Busy],
    schedule: Schedule = FULL_TIME,
) -> str | None:
    """С4, А11: из работающих свободных — с меньшей суммой минут за день; при равенстве —
    первый по конфигу."""
    candidates = free_masters(service, start, master_ids, busy, schedule)
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


def outside_schedule(bookings: Iterable[Busy], schedule: Schedule) -> list[Busy]:
    """АР4: записи, которые не помещаются в рабочие часы мастера."""
    return [b for b in bookings if not schedule.works(b.master_id, b.start, b.end)]
