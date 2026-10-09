"""SQLite: клиенты, записи и напоминания (А2, А3, А5, А6). Время — UTC, секунды эпохи."""

import sqlite3
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from enum import Enum

from . import slots
from .clock import TZ
from .config import Service, Shop

SCHEMA = """
CREATE TABLE IF NOT EXISTS clients (
    user_id    INTEGER PRIMARY KEY,
    name       TEXT    NOT NULL,
    phone      TEXT    NOT NULL,
    created_at INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS bookings (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id    INTEGER NOT NULL REFERENCES clients(user_id),
    master_id  TEXT    NOT NULL,
    service_id TEXT    NOT NULL,
    start_at   INTEGER NOT NULL,
    end_at     INTEGER NOT NULL,
    status     TEXT    NOT NULL DEFAULT 'active',
    created_at INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS bookings_master_start ON bookings (master_id, start_at);
CREATE TABLE IF NOT EXISTS reminders (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    booking_id   INTEGER NOT NULL REFERENCES bookings(id),
    kind         TEXT    NOT NULL,                  -- '24h', '2h', 'test'
    due_at       INTEGER NOT NULL,
    status       TEXT    NOT NULL DEFAULT 'pending', -- pending, sent, skipped
    processed_at INTEGER
);
CREATE INDEX IF NOT EXISTS reminders_pending_due ON reminders (status, due_at);
CREATE TABLE IF NOT EXISTS users (                -- все, кто нажимал /start (В3)
    user_id        INTEGER PRIMARY KEY,
    first_start_at INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS master_hours (      -- АР1: недельный шаблон, минуты от полуночи
    master_id TEXT    NOT NULL,
    weekday   INTEGER NOT NULL,                  -- 0 = понедельник
    start_min INTEGER,                           -- NULL = выходной
    end_min   INTEGER,
    PRIMARY KEY (master_id, weekday)
);
CREATE TABLE IF NOT EXISTS master_days_off (    -- АР2: выходные на даты
    master_id TEXT NOT NULL,
    day       TEXT NOT NULL,                     -- ISO-дата по Казани
    PRIMARY KEY (master_id, day)
);
CREATE TABLE IF NOT EXISTS broadcasts (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    text        TEXT    NOT NULL,
    status      TEXT    NOT NULL DEFAULT 'draft',   -- draft, sending, sent, cancelled
    created_at  INTEGER NOT NULL,
    finished_at INTEGER,
    delivered   INTEGER,
    total       INTEGER
);
"""

# Н1: напоминания за 24 часа и за 2 часа до визита.
REMINDER_OFFSETS = {"24h": timedelta(hours=24), "2h": timedelta(hours=2)}
TEST_REMINDER_KIND = "test"


@dataclass(frozen=True)
class Client:
    user_id: int
    name: str
    phone: str


@dataclass(frozen=True)
class Booking:
    id: int
    client: Client
    master_id: str
    service_id: str
    start: datetime
    status: str = "active"


@dataclass(frozen=True)
class DueReminder:
    id: int
    kind: str
    due: datetime
    booking: Booking


class CancelCheck(Enum):
    """Можно ли отменить запись (М2, М4, А9)."""

    OK = "ok"
    NOT_FOUND = "not_found"  # нет такой записи у этого клиента (в том числе чужая)
    ALREADY_CANCELLED = "already_cancelled"
    STARTED = "started"  # визит уже начался или прошёл


BOOKING_SELECT = (
    "SELECT b.id, b.user_id, c.name, c.phone, b.master_id, b.service_id, b.start_at, b.status"
    " FROM bookings b JOIN clients c ON c.user_id = b.user_id"
)


def _booking(row: tuple) -> Booking:
    bid, user_id, name, phone, master_id, service_id, start_at, status = row
    return Booking(bid, Client(user_id, name, phone), master_id, service_id, _dt(start_at), status)


def _check(booking: Booking | None, now: datetime) -> CancelCheck:
    if booking is None:
        return CancelCheck.NOT_FOUND
    if booking.status != "active":
        return CancelCheck.ALREADY_CANCELLED
    if booking.start <= now:
        return CancelCheck.STARTED
    return CancelCheck.OK


def _ts(dt: datetime) -> int:
    return int(dt.timestamp())


def _dt(ts: int) -> datetime:
    return datetime.fromtimestamp(ts, UTC)


class Database:
    def __init__(self, path: str) -> None:
        self.path = path
        with self._session() as conn:
            conn.execute("PRAGMA journal_mode=WAL")
            conn.executescript(SCHEMA)

    def _connect(self) -> sqlite3.Connection:
        # isolation_level=None: транзакциями управляем сами (BEGIN IMMEDIATE в create_booking).
        conn = sqlite3.connect(self.path, timeout=30, isolation_level=None)
        conn.execute("PRAGMA foreign_keys=ON")
        return conn

    @contextmanager
    def _session(self) -> Iterator[sqlite3.Connection]:
        conn = self._connect()
        try:
            yield conn
        finally:
            conn.close()

    def get_client(self, user_id: int) -> Client | None:
        with self._session() as conn:
            row = conn.execute(
                "SELECT user_id, name, phone FROM clients WHERE user_id = ?", (user_id,)
            ).fetchone()
        return Client(*row) if row else None

    @staticmethod
    def _busy(conn: sqlite3.Connection, start: datetime, end: datetime) -> list[slots.Busy]:
        rows = conn.execute(
            "SELECT master_id, start_at, end_at FROM bookings"
            " WHERE status = 'active' AND start_at < ? AND end_at > ?",
            (_ts(end), _ts(start)),
        ).fetchall()
        return [slots.Busy(m, _dt(s), _dt(e)) for m, s, e in rows]

    def busy_between(self, start: datetime, end: datetime) -> list[slots.Busy]:
        with self._session() as conn:
            return self._busy(conn, start, end)

    def busy_for_window(self, shop: Shop, now: datetime) -> list[slots.Busy]:
        """Активные записи на все дни, доступные для записи (К4)."""
        days = slots.booking_days(shop, now)
        start, _ = slots.local_day_bounds(days[0])
        _, end = slots.local_day_bounds(days[-1])
        return self.busy_between(start, end)

    def create_booking(
        self,
        *,
        shop: Shop,
        user_id: int,
        service: Service,
        master_ids: Sequence[str],
        start: datetime,
        now: datetime,
        new_client: tuple[str, str] | None = None,
    ) -> Booking | None:
        """Перепроверка слота и вставка в одной транзакции с блокировкой на запись (С5, А5).

        new_client — (имя, телефон) для первой записи; сохраняется вместе с ней (К6).
        Возвращает None, если слот уже занят или больше недоступен.
        """
        day_start, day_end = slots.local_day_bounds(start.astimezone(TZ).date())
        conn = self._connect()
        try:
            conn.execute("BEGIN IMMEDIATE")
            busy = self._busy(conn, day_start, day_end)
            schedule = self._schedule(conn)  # А15: график — в той же транзакции
            if not slots.is_start_available(shop, service, master_ids, start, busy, now, schedule):
                conn.execute("ROLLBACK")
                return None
            master_id = slots.pick_master(service, start, master_ids, busy, schedule)
            assert master_id is not None  # гарантировано is_start_available

            row = conn.execute(
                "SELECT user_id, name, phone FROM clients WHERE user_id = ?", (user_id,)
            ).fetchone()
            if row:
                client = Client(*row)
            elif new_client:
                client = Client(user_id, *new_client)
                conn.execute(
                    "INSERT INTO clients (user_id, name, phone, created_at) VALUES (?, ?, ?, ?)",
                    (user_id, client.name, client.phone, _ts(now)),
                )
            else:
                raise ValueError("Нет данных клиента для первой записи")

            end = start + timedelta(minutes=service.minutes)
            cur = conn.execute(
                "INSERT INTO bookings"
                " (user_id, master_id, service_id, start_at, end_at, created_at)"
                " VALUES (?, ?, ?, ?, ?, ?)",
                (user_id, master_id, service.id, _ts(start), _ts(end), _ts(now)),
            )
            booking_id = cur.lastrowid
            # 3.1, Н2: напоминания создаются в той же транзакции; прошедшие сроки — нет.
            for kind, offset in REMINDER_OFFSETS.items():
                due = start - offset
                if due > now:
                    conn.execute(
                        "INSERT INTO reminders (booking_id, kind, due_at) VALUES (?, ?, ?)",
                        (booking_id, kind, _ts(due)),
                    )
            conn.execute("COMMIT")
            return Booking(booking_id, client, master_id, service.id, start)
        except BaseException:
            if conn.in_transaction:
                conn.execute("ROLLBACK")
            raise
        finally:
            conn.close()

    def upcoming_bookings(self, user_id: int, now: datetime) -> list[Booking]:
        """М1: будущие активные записи клиента по порядку времени."""
        with self._session() as conn:
            rows = conn.execute(
                f"{BOOKING_SELECT} WHERE b.user_id = ? AND b.status = 'active' AND b.start_at > ?"
                " ORDER BY b.start_at, b.id",
                (user_id, _ts(now)),
            ).fetchall()
        return [_booking(r) for r in rows]

    def active_booking_at(self, user_id: int, start: datetime) -> Booking | None:
        """Активная запись клиента на этот старт (повторное нажатие кнопки времени)."""
        with self._session() as conn:
            row = conn.execute(
                f"{BOOKING_SELECT} WHERE b.user_id = ? AND b.status = 'active' AND b.start_at = ?",
                (user_id, _ts(start)),
            ).fetchone()
        return _booking(row) if row else None

    def check_cancel(
        self, booking_id: int, user_id: int, now: datetime
    ) -> tuple[CancelCheck, Booking | None]:
        """Проверка перед подтверждением отмены. Чужая запись — как несуществующая (А9)."""
        with self._session() as conn:
            booking = self._user_booking(conn, booking_id, user_id)
        return _check(booking, now), booking

    def cancel_booking(
        self, booking_id: int, user_id: int | None, now: datetime
    ) -> tuple[CancelCheck, Booking | None]:
        """М2–М4, АЗ2: проверка и отмена в одной транзакции с блокировкой на запись.

        user_id — владелец записи; None — отменяет администратор (любую запись).
        Отменяет только при CancelCheck.OK; повторный или параллельный вызов, в том числе
        клиентом и администратором одновременно, вернёт ALREADY_CANCELLED.
        """
        conn = self._connect()
        try:
            conn.execute("BEGIN IMMEDIATE")
            booking = self._user_booking(conn, booking_id, user_id)
            result = _check(booking, now)
            if result is CancelCheck.OK:
                conn.execute(
                    "UPDATE bookings SET status = 'cancelled' WHERE id = ? AND status = 'active'",
                    (booking_id,),
                )
            conn.execute("COMMIT")
            return result, booking
        except BaseException:
            if conn.in_transaction:
                conn.execute("ROLLBACK")
            raise
        finally:
            conn.close()

    def booking(self, booking_id: int) -> Booking | None:
        """Запись по номеру — только для админки (АЗ2)."""
        with self._session() as conn:
            return self._user_booking(conn, booking_id, None)

    @staticmethod
    def _user_booking(
        conn: sqlite3.Connection, booking_id: int, user_id: int | None
    ) -> Booking | None:
        if user_id is None:
            row = conn.execute(f"{BOOKING_SELECT} WHERE b.id = ?", (booking_id,)).fetchone()
            return _booking(row) if row else None
        row = conn.execute(
            f"{BOOKING_SELECT} WHERE b.id = ? AND b.user_id = ?", (booking_id, user_id)
        ).fetchone()
        return _booking(row) if row else None

    # --- Напоминания (А6) ---

    def add_test_reminder(self, booking_id: int, due: datetime) -> None:
        """Н5: отдельная строка; настоящие напоминания записи не трогаются."""
        with self._session() as conn:
            conn.execute(
                "INSERT INTO reminders (booking_id, kind, due_at) VALUES (?, ?, ?)",
                (booking_id, TEST_REMINDER_KIND, _ts(due)),
            )

    def claim_due_reminders(self, now: datetime, max_late: timedelta) -> list[DueReminder]:
        """Выбрать наступившие напоминания и отметить их до отправки (А6, Н3, Н4).

        В одной транзакции с блокировкой на запись: отправляемые получают статус sent,
        опоздавшие больше max_late и напоминания по неактивным записям — skipped.
        Отмеченное больше никогда не выбирается: при сбое напоминание теряется, но не дублируется.
        """
        conn = self._connect()
        try:
            conn.execute("BEGIN IMMEDIATE")
            rows = conn.execute(
                "SELECT r.id, r.kind, r.due_at, b.id, b.user_id, c.name, c.phone,"
                " b.master_id, b.service_id, b.start_at, b.status"
                " FROM reminders r"
                " JOIN bookings b ON b.id = r.booking_id"
                " JOIN clients c ON c.user_id = b.user_id"
                " WHERE r.status = 'pending' AND r.due_at <= ?"
                " ORDER BY r.due_at, r.id",
                (_ts(now),),
            ).fetchall()
            claimed, skipped = [], []
            for rid, kind, due_at, *booking_row in rows:
                booking = _booking(tuple(booking_row))
                due = _dt(due_at)
                if booking.status != "active" or now - due > max_late:
                    skipped.append(rid)
                else:
                    claimed.append(DueReminder(rid, kind, due, booking))
            for status, ids in (("sent", [r.id for r in claimed]), ("skipped", skipped)):
                conn.executemany(
                    "UPDATE reminders SET status = ?, processed_at = ?"
                    " WHERE id = ? AND status = 'pending'",
                    [(status, _ts(now), rid) for rid in ids],
                )
            conn.execute("COMMIT")
            return claimed
        except BaseException:
            if conn.in_transaction:
                conn.execute("ROLLBACK")
            raise
        finally:
            conn.close()

    # --- Владелец (В3, В4) ---

    def remember_user(self, user_id: int, now: datetime) -> None:
        """В3: каждый, кто нажал /start в личном чате, — получатель рассылки."""
        with self._session() as conn:
            conn.execute(
                "INSERT OR IGNORE INTO users (user_id, first_start_at) VALUES (?, ?)",
                (user_id, _ts(now)),
            )

    def broadcast_recipients(self) -> list[int]:
        """Нажимавшие /start и все клиенты из базы, каждый один раз."""
        with self._session() as conn:
            rows = conn.execute(
                "SELECT user_id FROM users UNION SELECT user_id FROM clients ORDER BY user_id"
            ).fetchall()
        return [r[0] for r in rows]

    def day_bookings(self, day_start: datetime, day_end: datetime) -> list[Booking]:
        """В4: активные записи за день, включая уже прошедшие, по времени."""
        with self._session() as conn:
            rows = conn.execute(
                f"{BOOKING_SELECT} WHERE b.status = 'active' AND b.start_at >= ? AND b.start_at < ?"
                " ORDER BY b.start_at, b.id",
                (_ts(day_start), _ts(day_end)),
            ).fetchall()
        return [_booking(r) for r in rows]

    def create_broadcast(self, text: str, now: datetime) -> int:
        with self._session() as conn:
            cur = conn.execute(
                "INSERT INTO broadcasts (text, created_at) VALUES (?, ?)", (text, _ts(now))
            )
            return cur.lastrowid

    def broadcast(self, broadcast_id: int) -> tuple[str, str] | None:
        """(текст, статус) или None."""
        with self._session() as conn:
            row = conn.execute(
                "SELECT text, status FROM broadcasts WHERE id = ?", (broadcast_id,)
            ).fetchone()
        return (row[0], row[1]) if row else None

    def _move_broadcast(self, broadcast_id: int, new_status: str) -> bool:
        """Перевести черновик в new_status. True — только у первого вызова (4.7)."""
        with self._session() as conn:
            cur = conn.execute(
                "UPDATE broadcasts SET status = ? WHERE id = ? AND status = 'draft'",
                (new_status, broadcast_id),
            )
            return cur.rowcount == 1

    def start_broadcast(self, broadcast_id: int) -> bool:
        return self._move_broadcast(broadcast_id, "sending")

    def cancel_broadcast(self, broadcast_id: int) -> bool:
        return self._move_broadcast(broadcast_id, "cancelled")

    def finish_broadcast(
        self, broadcast_id: int, delivered: int, total: int, now: datetime
    ) -> None:
        with self._session() as conn:
            conn.execute(
                "UPDATE broadcasts SET status = 'sent', delivered = ?, total = ?, finished_at = ?"
                " WHERE id = ?",
                (delivered, total, _ts(now), broadcast_id),
            )

    # --- График мастеров (АР1–АР5, А15) ---

    @staticmethod
    def _schedule(conn: sqlite3.Connection) -> slots.Schedule:
        weekly: dict[str, dict[int, slots.Hours | None]] = {}
        for master_id, weekday, start_min, end_min in conn.execute(
            "SELECT master_id, weekday, start_min, end_min FROM master_hours"
        ):
            hours = None if start_min is None else (_min_time(start_min), _min_time(end_min))
            weekly.setdefault(master_id, {})[weekday] = hours
        days_off = frozenset(
            (master_id, date.fromisoformat(day))
            for master_id, day in conn.execute("SELECT master_id, day FROM master_days_off")
        )
        return slots.Schedule(weekly=weekly, days_off=days_off)

    def schedule(self) -> slots.Schedule:
        with self._session() as conn:
            return self._schedule(conn)

    def set_weekday_hours(
        self, shop: Shop, master_id: str, weekday: int, hours: slots.Hours | None
    ) -> None:
        """Изменить один день недели. Остальные дни шаблона заполняются режимом (Д6)."""
        conn = self._connect()
        try:
            conn.execute("BEGIN IMMEDIATE")
            for wd in range(7):
                conn.execute(
                    "INSERT OR IGNORE INTO master_hours (master_id, weekday, start_min, end_min)"
                    " VALUES (?, ?, ?, ?)",
                    (master_id, wd, _time_min(shop.open), _time_min(shop.close)),
                )
            start_min, end_min = (None, None) if hours is None else map(_time_min, hours)
            conn.execute(
                "UPDATE master_hours SET start_min = ?, end_min = ?"
                " WHERE master_id = ? AND weekday = ?",
                (start_min, end_min, master_id, weekday),
            )
            conn.execute("COMMIT")
        except BaseException:
            if conn.in_transaction:
                conn.execute("ROLLBACK")
            raise
        finally:
            conn.close()

    def toggle_day_off(self, master_id: str, day: date) -> bool:
        """АР2: поставить или снять выходной на дату. True — теперь выходной."""
        with self._session() as conn:
            cur = conn.execute(
                "DELETE FROM master_days_off WHERE master_id = ? AND day = ?",
                (master_id, day.isoformat()),
            )
            if cur.rowcount:
                return False
            conn.execute(
                "INSERT INTO master_days_off (master_id, day) VALUES (?, ?)",
                (master_id, day.isoformat()),
            )
            return True

    def future_master_bookings(
        self, master_id: str, now: datetime
    ) -> list[tuple[Booking, slots.Busy]]:
        """АР4: будущие активные записи мастера вместе с их интервалами."""
        with self._session() as conn:
            rows = conn.execute(
                "SELECT b.id, b.user_id, c.name, c.phone, b.master_id, b.service_id, b.start_at,"
                " b.status, b.end_at FROM bookings b JOIN clients c ON c.user_id = b.user_id"
                " WHERE b.master_id = ? AND b.status = 'active' AND b.start_at > ?"
                " ORDER BY b.start_at, b.id",
                (master_id, _ts(now)),
            ).fetchall()
        return [(_booking(row[:-1]), slots.Busy(row[4], _dt(row[6]), _dt(row[-1]))) for row in rows]


def _time_min(t: time) -> int:
    return t.hour * 60 + t.minute


def _min_time(minutes: int) -> time:
    return time(minutes // 60, minutes % 60)
