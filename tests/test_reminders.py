"""Ф3: напоминания — критерии 3.1–3.9. Время подменено, настоящих секунд тесты не ждут."""

import asyncio
import logging
import sqlite3
from datetime import datetime, timedelta

import pytest
from aiogram.exceptions import TelegramForbiddenError, TelegramNetworkError
from aiogram.methods import SendMessage, SetMyCommands

from saqal import clock, reminders, texts
from saqal.clock import TZ
from saqal.db import Database
from saqal.reminders import ReminderService
from tests.conftest import Harness, buttons, sent, texts_of
from tests.test_flow import ALICE, BOB, PHONE, book_new
from tests.test_timezone import server_in_utc  # noqa: F401  (фикстура)

CAROL, DAN, EVE = 333, 444, 555
START = datetime(2026, 10, 11, 12, 0, tzinfo=TZ)  # 11 октября (вс) 12:00 по Казани
DUE_24H = START - timedelta(hours=24)
DUE_2H = START - timedelta(hours=2)

T2_IVAN = (
    "💈 Иван, напоминаем: завтра в 12:00 вы записаны к мастеру Умар на «Мужская стрижка». "
    "Если планы изменились, отмените запись кнопкой ниже, чтобы освободить время для других."
)
T3_IVAN = T2_IVAN.replace("завтра в 12:00", "сегодня в 12:00")


def rows(db: Database, sql: str, *args) -> list[tuple]:
    conn = sqlite3.connect(db.path)
    try:
        result = conn.execute(sql, args).fetchall()
        conn.commit()
        return result
    finally:
        conn.close()


def reminder_rows(db, booking_id):
    return rows(
        db,
        "SELECT kind, due_at, status FROM reminders WHERE booking_id = ? ORDER BY id",
        booking_id,
    )


def booking_id(db, user_id, start=START) -> int:
    return db.active_booking_at(user_id, start).id


async def book_on_11th(tg, uid, name="Иван", phone=PHONE, time="1200", master="umar"):
    return await book_new(
        tg, uid, name=name, phone=phone, time=f"20261011{time}", day="20261011", master=master
    )


async def tick(service: ReminderService) -> list:
    """Один проход цикла; дождаться запущенных им отправок."""
    tasks = await service.tick()
    if tasks:
        await asyncio.gather(*tasks)
    return tasks


def client_msgs(tg, uid) -> list[SendMessage]:
    return [r for r in sent(tg.session.requests, uid) if isinstance(r, SendMessage)]


def reminders_to(tg, uid) -> list[str]:
    return [m.text for m in client_msgs(tg, uid) if m.text.startswith("💈")]


@pytest.fixture
def service(tg, db, shop):
    svc = ReminderService(db, shop, tg.bot)
    yield svc


# --- 3.1 ---


async def test_reminders_created_with_booking(tg, db):
    await book_on_11th(tg, ALICE)
    bid = booking_id(db, ALICE)
    assert reminder_rows(db, bid) == [
        ("24h", int(DUE_24H.timestamp()), "pending"),
        ("2h", int(DUE_2H.timestamp()), "pending"),
    ]


@pytest.mark.parametrize(
    ("time", "kinds"),
    [
        ("202610091200", ["2h"]),  # сейчас 9 окт 09:00: срок за 24 часа уже прошёл
        ("202610091030", []),  # и срок за 2 часа (08:30) тоже
        ("202610091100", []),  # срок за 2 часа ровно сейчас — тоже не создаётся
        ("202610101000", ["24h", "2h"]),  # оба срока впереди
    ],
)
async def test_past_due_reminders_not_created(tg, db, time, kinds):
    await book_new(tg, ALICE, time=time, day=time[:8])
    start = datetime.strptime(time, "%Y%m%d%H%M").replace(tzinfo=TZ)
    assert [k for k, *_ in reminder_rows(db, booking_id(db, ALICE, start))] == kinds


async def test_reminders_in_same_transaction_as_booking(tg, db, shop):
    """Не удалось создать напоминания — записи тоже нет."""
    rows(db, "DROP TABLE reminders")
    with pytest.raises(sqlite3.OperationalError):
        db.create_booking(
            shop=shop,
            user_id=ALICE,
            service=shop.service("men"),
            master_ids=["umar"],
            start=START,
            now=clock.now(),
            new_client=("Иван", "+79001234567"),
        )
    assert rows(db, "SELECT COUNT(*) FROM bookings") == [(0,)]
    assert rows(db, "SELECT COUNT(*) FROM clients") == [(0,)]


# --- 3.2 ---


async def test_t2_and_t3_on_time_in_kazan_time_with_cancel_button(
    tg,
    db,
    service,
    frozen_now,
    server_in_utc,  # noqa: F811
):
    await book_on_11th(tg, ALICE)
    bid = booking_id(db, ALICE)

    frozen_now(DUE_24H - timedelta(seconds=1))
    await tick(service)
    assert reminders_to(tg, ALICE) == []

    frozen_now(DUE_24H)
    await tick(service)
    [t2] = [m for m in client_msgs(tg, ALICE) if m.text.startswith("💈")]
    assert t2.text == T2_IVAN
    assert buttons(t2) == [(texts.BTN_CANCEL_BOOKING, f"cx:{bid}")]

    frozen_now(DUE_2H)
    await tick(service)
    assert reminders_to(tg, ALICE) == [T2_IVAN, T3_IVAN]

    # Кнопка ведёт на то же подтверждение, что и «Мои записи».
    from_reminder = texts_of(await tg.press(ALICE, f"cx:{bid}"))
    [my_list] = sent(await tg.press(ALICE, "my"))
    [(_, cancel_data), _] = buttons(my_list)
    from_my = texts_of(await tg.press(ALICE, cancel_data))
    assert from_reminder == from_my
    assert from_reminder[0].startswith("Точно отменить запись?")


# --- 3.3 ---


async def test_cancelled_second_before_due_gets_no_reminders(tg, db, service, frozen_now):
    await book_on_11th(tg, ALICE)
    bid = booking_id(db, ALICE)
    frozen_now(DUE_24H - timedelta(seconds=1))
    await tg.press(ALICE, f"cxy:{bid}")
    for now in (DUE_24H, DUE_24H + timedelta(seconds=5), DUE_2H):
        frozen_now(now)
        await tick(service)
    assert reminders_to(tg, ALICE) == []
    assert [s for *_, s in reminder_rows(db, bid)] == ["skipped", "skipped"]


# --- 3.4 ---


@pytest.mark.parametrize("phase", [0.0, 0.5, 2.0, 4.9, 5.0])
async def test_reminder_sent_within_15_seconds_of_due(tg, db, shop, frozen_now, phase):
    """Цикл с подменённым sleep: часы двигаются только по его вызовам."""
    await book_on_11th(tg, ALICE)
    frozen_now(DUE_24H - timedelta(seconds=10 + phase))
    sent_at: list[datetime] = []

    async def fake_sleep(seconds: float) -> None:
        frozen_now(clock.now() + timedelta(seconds=seconds))
        await asyncio.sleep(0)

    svc = ReminderService(db, shop, tg.bot, sleep=fake_sleep)
    svc.start()
    for _ in range(100):
        await asyncio.sleep(0)
        if reminders_to(tg, ALICE):
            break
    await svc.stop()
    for req, at in zip(tg.session.requests, tg.session.times, strict=True):
        if isinstance(req, SendMessage) and req.chat_id == ALICE and req.text.startswith("💈"):
            sent_at.append(at)
    assert sent_at, "напоминание не отправлено"
    assert timedelta(0) <= sent_at[0] - DUE_24H <= timedelta(seconds=15)
    assert timedelta(seconds=15) >= reminders.INTERVAL


# --- 3.5 ---


@pytest.mark.parametrize(
    ("late", "delivered"),
    [
        (timedelta(0), True),
        (timedelta(minutes=9, seconds=59), True),
        (timedelta(minutes=10), True),
        (timedelta(minutes=10, seconds=1), False),
        (timedelta(hours=3), False),
    ],
)
async def test_after_restart_late_reminders(tg, db, shop, frozen_now, late, delivered):
    await book_on_11th(tg, ALICE)
    bid = booking_id(db, ALICE)
    # Бот лежал во время срока. Перезапуск: новый диспетчер и новый цикл на той же базе.
    restarted = Harness(shop, db)
    svc = ReminderService(db, shop, restarted.bot)
    frozen_now(DUE_24H + late)
    await tick(svc)
    got = reminders_to(restarted, ALICE)
    assert got == ([T2_IVAN] if delivered else [])
    assert reminder_rows(db, bid)[0][2] == ("sent" if delivered else "skipped")
    # Пропущенное больше не выбирается.
    frozen_now(DUE_24H + late + timedelta(seconds=5))
    await tick(svc)
    assert reminders_to(restarted, ALICE) == got


# --- 3.6 ---


async def test_each_reminder_sent_once(tg, db, service, frozen_now):
    await book_on_11th(tg, ALICE)
    frozen_now(DUE_24H)
    await asyncio.gather(tick(service), tick(service))
    await tick(service)
    frozen_now(DUE_24H + timedelta(minutes=5))
    await tick(ReminderService(db, service.shop, tg.bot))  # «другой процесс» после перезапуска
    assert reminders_to(tg, ALICE) == [T2_IVAN]


async def test_restart_mid_send_does_not_resend(tg, db, shop, frozen_now):
    await book_on_11th(tg, ALICE)
    bid = booking_id(db, ALICE)
    tg.session.hang_for.add(ALICE)  # отправка «висит» — тут бот падает
    svc = ReminderService(db, shop, tg.bot)
    frozen_now(DUE_24H)
    started = await svc.tick()
    assert len(started) == 1
    await svc.stop(grace=0)
    assert started[0].cancelled()

    tg.session.hang_for.clear()
    restarted = Harness(shop, db)
    frozen_now(DUE_24H + timedelta(seconds=10))
    await tick(ReminderService(db, shop, restarted.bot))
    assert reminders_to(restarted, ALICE) == []  # потеряно, но не задублировано (А6)
    assert reminder_rows(db, bid)[0][2] == "sent"


# --- 3.7 ---


async def test_one_failure_does_not_stop_or_delay_others(tg, db, service, frozen_now):
    users = [ALICE, BOB, CAROL, DAN, EVE]
    for i, uid in enumerate(users):
        await book_on_11th(tg, uid, name=f"Клиент{i}", phone=f"7900000000{i}", time=f"1{i}00")
    # Пять напоминаний с одним сроком (тестовые: у записей разное время, а срок нужен общий).
    due = clock.now() + timedelta(minutes=1)
    for uid in users:
        [b] = db.upcoming_bookings(uid, clock.now())
        db.add_test_reminder(b.id, due)
    # Боб заблокировал бота, у Кэрол сеть, у Дэна битые данные, у Евы запрос завис.
    tg.session.fail_for[BOB] = lambda m: TelegramForbiddenError(m, "bot was blocked by the user")
    tg.session.fail_for[CAROL] = lambda m: TelegramNetworkError(m, "connection reset")
    rows(db, "UPDATE bookings SET service_id = 'gone' WHERE user_id = ?", DAN)
    tg.session.hang_for.add(EVE)

    frozen_now(due)
    started = await asyncio.wait_for(service.tick(), timeout=1)  # tick не ждёт отправок
    assert len(started) == 5
    for _ in range(10):
        await asyncio.sleep(0)
    assert len(reminders_to(tg, ALICE)) == 1  # не задержано упавшими и зависшим
    assert sum(t.done() for t in started) == 4

    # Цикл жив: следующее напоминание Алисы уходит, хотя отправка Евы всё ещё висит.
    frozen_now(datetime(2026, 10, 10, 10, 0, tzinfo=TZ))  # за 24 часа до её записи 11 окт 10:00
    await asyncio.wait_for(service.tick(), timeout=1)
    for _ in range(10):
        await asyncio.sleep(0)
    assert len(reminders_to(tg, ALICE)) == 2
    await service.stop(grace=0)


async def test_loop_survives_failed_tick(tg, db, shop, frozen_now, monkeypatch):
    await book_on_11th(tg, ALICE)
    frozen_now(DUE_24H)
    calls = {"n": 0}
    real_claim = db.claim_due_reminders

    def flaky_claim(now, max_late):
        calls["n"] += 1
        if calls["n"] == 1:
            raise sqlite3.OperationalError("database is locked")
        return real_claim(now, max_late)

    monkeypatch.setattr(db, "claim_due_reminders", flaky_claim)

    async def fake_sleep(seconds: float) -> None:
        frozen_now(clock.now() + timedelta(seconds=seconds))
        await asyncio.sleep(0)

    svc = ReminderService(db, shop, tg.bot, sleep=fake_sleep)
    svc.start()
    for _ in range(50):
        await asyncio.sleep(0)
        if reminders_to(tg, ALICE):
            break
    await svc.stop()
    assert calls["n"] >= 2
    assert reminders_to(tg, ALICE) == [T2_IVAN]


# --- 3.8 ---


async def test_test_reminder_sends_t2_in_a_minute_for_nearest_booking(tg, db, service, frozen_now):
    await book_on_11th(tg, ALICE)  # 11 окт 12:00
    await book_new(tg, ALICE, time="202610101200", day="20261010")  # ближайшая — 10 окт
    nearest = booking_id(db, ALICE, datetime(2026, 10, 10, 12, 0, tzinfo=TZ))
    real_before = rows(db, "SELECT * FROM reminders ORDER BY id")

    now = clock.now()
    r = await tg.text(ALICE, "/test_reminder")
    assert texts_of(r) == [texts.TEST_REMINDER_SCHEDULED]
    assert rows(db, "SELECT * FROM reminders WHERE kind != 'test' ORDER BY id") == real_before

    frozen_now(now + timedelta(seconds=59))
    await tick(service)
    assert reminders_to(tg, ALICE) == []

    frozen_now(now + timedelta(seconds=60))
    await tick(service)
    [msg] = [m for m in client_msgs(tg, ALICE) if m.text.startswith("💈")]
    assert msg.text == T2_IVAN  # Т2 дословно, по ближайшей записи 10 окт 12:00
    assert buttons(msg) == [(texts.BTN_CANCEL_BOOKING, f"cx:{nearest}")]
    # Настоящие напоминания не изменились.
    assert rows(db, "SELECT * FROM reminders WHERE kind != 'test' ORDER BY id") == real_before


async def test_test_reminder_without_bookings(tg, db):
    r = await tg.text(ALICE, "/test_reminder")
    [msg] = sent(r)
    assert msg.text == texts.TEST_REMINDER_NO_BOOKINGS
    assert buttons(msg) == [(texts.BTN_BOOK, "book")]
    assert rows(db, "SELECT COUNT(*) FROM reminders") == [(0,)]


async def test_test_reminder_not_in_bot_menu(tg):
    await tg.dp.emit_startup(dispatcher=tg.dp, bot=tg.bot, bots=[tg.bot], **tg.dp.workflow_data)
    await tg.dp.emit_shutdown(dispatcher=tg.dp, bot=tg.bot, bots=[tg.bot], **tg.dp.workflow_data)
    assert not [r for r in tg.session.requests if isinstance(r, SetMyCommands)]


# --- 3.9 ---


async def test_loop_starts_and_stops_with_bot(tg):
    wd = tg.dp.workflow_data
    await tg.dp.emit_startup(dispatcher=tg.dp, bot=tg.bot, bots=[tg.bot], **wd)
    svc = tg.dp["reminders"]
    task = svc._task
    assert task is not None and not task.done()
    await tg.dp.emit_shutdown(dispatcher=tg.dp, bot=tg.bot, bots=[tg.bot], **tg.dp.workflow_data)
    assert task.done()
    assert "reminders" not in tg.dp.workflow_data


async def test_reminders_do_not_log_names_or_phones(tg, db, service, frozen_now, caplog):
    caplog.set_level(logging.DEBUG)
    await book_on_11th(tg, ALICE)
    tg.session.fail_for[ALICE] = lambda m: TelegramForbiddenError(m, "blocked")
    frozen_now(DUE_24H)
    await tick(service)
    tg.session.fail_for.clear()
    frozen_now(DUE_2H)
    await tick(service)
    await tg.text(ALICE, "/test_reminder")
    assert "reminder" in caplog.text
    assert "Иван" not in caplog.text
    assert PHONE not in caplog.text
