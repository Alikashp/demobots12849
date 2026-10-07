"""SQLite: клиенты и записи (А2, А3, А5). Время хранится в UTC (секунды эпохи)."""

import sqlite3
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

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
"""


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
            if not slots.is_start_available(shop, service, master_ids, start, busy, now):
                conn.execute("ROLLBACK")
                return None
            master_id = slots.pick_master(service, start, master_ids, busy)
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
            conn.execute("COMMIT")
            return Booking(cur.lastrowid, client, master_id, service.id, start)
        except BaseException:
            if conn.in_transaction:
                conn.execute("ROLLBACK")
            raise
        finally:
            conn.close()
