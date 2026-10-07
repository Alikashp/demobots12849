"""Критерий 1.6 (уровень базы): две одновременные попытки — ровно одна запись (С5, А5)."""

import sqlite3
import threading
from datetime import datetime

from saqal.clock import TZ
from saqal.db import Database

NOW = datetime(2026, 10, 9, 9, 0, tzinfo=TZ)
START = datetime(2026, 10, 9, 12, 0, tzinfo=TZ)


def _race(db: Database, shop, attempts: int, master_ids_for) -> list:
    barrier = threading.Barrier(attempts)
    results: list = [None] * attempts
    errors: list = []

    def attempt(i: int) -> None:
        try:
            barrier.wait()
            results[i] = db.create_booking(
                shop=shop,
                user_id=100 + i,
                service=shop.service("men"),
                master_ids=master_ids_for(i),
                start=START,
                now=NOW,
                new_client=(f"Клиент {i}", f"+7900000000{i}"),
            )
        except Exception as e:  # pragma: no cover - падение теста покажет ошибку
            errors.append(e)

    threads = [threading.Thread(target=attempt, args=(i,)) for i in range(attempts)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert not errors
    return results


def _active_count(db: Database, master_id: str) -> int:
    conn = sqlite3.connect(db.path)
    try:
        return conn.execute(
            "SELECT COUNT(*) FROM bookings WHERE master_id = ? AND status = 'active'",
            (master_id,),
        ).fetchone()[0]
    finally:
        conn.close()


def _clear(db: Database) -> None:
    conn = sqlite3.connect(db.path)
    try:
        conn.execute("DELETE FROM bookings")
        conn.execute("DELETE FROM clients")
        conn.commit()
    finally:
        conn.close()


def test_two_simultaneous_attempts_same_master_one_booking(db, shop):
    for _ in range(20):  # повторяем, чтобы поймать разные порядки потоков
        results = _race(db, shop, 2, lambda i: ["umar"])
        assert sum(r is not None for r in results) == 1
        assert _active_count(db, "umar") == 1
        _clear(db)


def test_many_simultaneous_any_master_never_overlap(db, shop):
    """С5 для «Любого мастера»: 5 попыток на 3 мастеров — 3 записи, по одной на мастера."""
    all_ids = [m.id for m in shop.masters]
    results = _race(db, shop, 5, lambda i: all_ids)
    made = [r for r in results if r is not None]
    assert len(made) == 3
    assert sorted(r.master_id for r in made) == sorted(all_ids)
    for m in all_ids:
        assert _active_count(db, m) == 1


def test_failed_attempt_does_not_save_new_client(db, shop):
    """Имя и телефон сохраняются только вместе с записью (К6)."""
    results = _race(db, shop, 2, lambda i: ["umar"])
    loser = 100 + results.index(None)
    assert db.get_client(loser) is None
