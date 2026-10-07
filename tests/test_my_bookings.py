"""Ф2: «Мои записи» и отмена — критерии 2.1–2.9."""

import sqlite3
from datetime import datetime

import pytest
from aiogram.types import ReplyKeyboardRemove

from saqal import texts
from saqal.clock import TZ
from tests.conftest import (
    ADMIN_CHAT_ID,
    RACE_OFFSETS,
    Harness,
    after_yields,
    buttons,
    sent,
    texts_of,
)
from tests.test_flow import ALICE, BOB, PHONE, book_new, go_to_time

LINE_12 = "📅 9 октября (пт) в 12:00 — «Мужская стрижка», мастер Умар"
T5_IVAN = "❌ Отмена записи: Иван, +79001234567\nМужская стрижка, мастер Умар, 9 октября (пт) 12:00"


def status(db, booking_id: int) -> str:
    conn = sqlite3.connect(db.path)
    try:
        return conn.execute("SELECT status FROM bookings WHERE id = ?", (booking_id,)).fetchone()[0]
    finally:
        conn.close()


def only_booking_id(db, user_id: int) -> int:
    [b] = db.upcoming_bookings(user_id, datetime(2026, 1, 1, tzinfo=TZ))
    return b.id


async def book_more(tg, uid, time, service="men", master="umar", day="20261009"):
    """Ещё одна запись вернувшегося клиента."""
    await go_to_time(tg, uid, service=service, master=master, day=day)
    return await tg.press(uid, f"tm:{time}")


# --- 2.1 ---


async def test_greeting_has_book_and_my_buttons(tg):
    [msg] = sent(await tg.text(ALICE, "/start"))
    assert buttons(msg) == [(texts.BTN_BOOK, "book"), (texts.BTN_MY, "my")]


# --- 2.2 ---


async def test_my_bookings_lists_future_active_in_time_order(tg, db, frozen_now):
    await book_new(tg, ALICE, time="202610091200")  # 9 окт 12:00
    await book_more(tg, ALICE, "202610111000", service="beard", master="barber3", day="20261011")
    await book_more(tg, ALICE, "202610091000", service="clipper")  # 9 окт 10:00
    await book_more(tg, ALICE, "202610101500", day="20261010")
    cancelled_id = db.active_booking_at(ALICE, datetime(2026, 10, 10, 15, 0, tzinfo=TZ)).id
    await tg.press(ALICE, f"cxy:{cancelled_id}")

    frozen_now(datetime(2026, 10, 9, 10, 10, tzinfo=TZ))  # 10:00 уже началась
    [msg] = sent(await tg.press(ALICE, "my"))
    assert msg.text == texts.my_bookings(
        [
            LINE_12,
            "📅 11 октября (вс) в 10:00 — «Моделирование бороды», мастер Барбер 3",
        ]
    )
    ids = [b.id for b in db.upcoming_bookings(ALICE, datetime(2026, 10, 9, 10, 10, tzinfo=TZ))]
    assert buttons(msg) == [
        ("❌ Отменить 9 октября (пт), 12:00", f"cx:{ids[0]}"),
        ("❌ Отменить 11 октября (вс), 10:00", f"cx:{ids[1]}"),
        (texts.BTN_HOME, "home"),
    ]


async def test_my_bookings_empty(tg):
    [msg] = sent(await tg.press(ALICE, "my"))
    assert msg.text == texts.NO_BOOKINGS
    assert buttons(msg) == [(texts.BTN_BOOK, "book")]


# --- 2.3 ---


async def test_cancel_asks_once_decline_keeps_confirm_cancels_and_sends_t5(tg, db):
    await book_new(tg, ALICE)
    bid = only_booking_id(db, ALICE)

    [msg] = sent(await tg.press(ALICE, f"cx:{bid}"))
    assert msg.text == texts.confirm_cancel(LINE_12)
    assert buttons(msg) == [(texts.BTN_CANCEL_YES, f"cxy:{bid}"), (texts.BTN_CANCEL_NO, "cxn")]

    r = await tg.press(ALICE, "cxn")
    assert texts_of(r) == [texts.my_bookings([LINE_12])]
    assert status(db, bid) == "active"
    assert texts_of(r, ADMIN_CHAT_ID) == []

    await tg.press(ALICE, f"cx:{bid}")
    r = await tg.press(ALICE, f"cxy:{bid}")
    assert texts_of(r, ALICE) == [texts.cancelled(LINE_12)]
    assert texts_of(r, ADMIN_CHAT_ID) == [T5_IVAN]
    assert status(db, bid) == "cancelled"


# --- 2.4 ---


async def test_cancelled_slot_is_free_again_for_same_master(tg, db):
    await book_new(tg, ALICE)
    [msg] = sent(await go_to_time(tg, BOB))
    assert ("12:00", "tm:202610091200") not in buttons(msg)

    await tg.press(ALICE, f"cxy:{only_booking_id(db, ALICE)}")

    [msg] = sent(await go_to_time(tg, BOB))
    assert ("12:00", "tm:202610091200") in buttons(msg)
    await tg.press(BOB, "tm:202610091200")
    await tg.text(BOB, "Пётр")
    r = await tg.contact(BOB, "79005550000", owner_id=BOB)
    assert "к мастеру Умар\n⌚ 9 октября (пт) в 12:00" in texts_of(r, BOB)[0]


# --- 2.5 ---


async def test_repeated_confirm_gives_one_cancel(tg, db):
    await book_new(tg, ALICE)
    bid = only_booking_id(db, ALICE)
    r1 = await tg.press(ALICE, f"cxy:{bid}")
    r2 = await tg.press(ALICE, f"cxy:{bid}")
    assert texts_of(r1 + r2, ADMIN_CHAT_ID) == [T5_IVAN]
    assert texts_of(r2, ALICE) == [texts.CANCEL_ALREADY]


@pytest.mark.parametrize("offsets", RACE_OFFSETS)
async def test_parallel_confirm_gives_one_cancel(tg, db, offsets):
    await book_new(tg, ALICE)
    bid = only_booking_id(db, ALICE)
    r = await tg.together(
        after_yields(offsets[0], tg.press(ALICE, f"cxy:{bid}")),
        after_yields(offsets[1], tg.press(ALICE, f"cxy:{bid}")),
    )
    assert texts_of(r, ADMIN_CHAT_ID) == [T5_IVAN]
    assert sorted(texts_of(r, ALICE)) == sorted([texts.cancelled(LINE_12), texts.CANCEL_ALREADY])
    assert status(db, bid) == "cancelled"


# --- 2.6 ---


@pytest.mark.parametrize(
    "now",
    [
        datetime(2026, 10, 9, 12, 0, tzinfo=TZ),  # ровно начало
        datetime(2026, 10, 9, 12, 30, tzinfo=TZ),  # идёт
        datetime(2026, 10, 10, 9, 0, tzinfo=TZ),  # прошла
    ],
)
async def test_started_or_past_booking_is_not_cancelled(tg, db, frozen_now, now):
    await book_new(tg, ALICE)
    bid = only_booking_id(db, ALICE)
    frozen_now(now)
    for data in (f"cx:{bid}", f"cxy:{bid}"):
        r = await tg.press(ALICE, data)
        assert texts_of(r) == [texts.CANCEL_STARTED]
    assert status(db, bid) == "active"
    assert texts_of(tg.session.requests, ADMIN_CHAT_ID) == [
        "🆕 Новая запись: Иван, +79001234567\nМужская стрижка, мастер Умар, 9 октября (пт) 12:00"
    ]
    assert texts_of(await tg.press(ALICE, "my")) == [texts.NO_BOOKINGS]


# --- 2.7 ---


async def test_foreign_booking_answers_like_missing(tg, db):
    await book_new(tg, ALICE)
    bid = only_booking_id(db, ALICE)
    for data in (f"cx:{bid}", f"cxy:{bid}"):
        foreign = texts_of(await tg.press(BOB, data))
        missing = texts_of(await tg.press(BOB, data.replace(str(bid), "999999")))
        assert foreign == missing == [texts.CANCEL_NOT_FOUND]
    assert status(db, bid) == "active"
    assert len(texts_of(tg.session.requests, ADMIN_CHAT_ID)) == 1  # только Т4


async def test_garbage_cancel_data_is_stale(tg):
    for data in ("cx:abc", "cxy:", "cxy:1:2", "cx:-1"):
        assert texts_of(await tg.press(ALICE, data)) == [texts.STALE_BUTTON], data


# --- 2.8 ---


async def test_my_bookings_and_cancel_work_after_restart(shop, db, frozen_now):
    before = Harness(shop, db)
    await book_new(before, ALICE)
    bid = only_booking_id(db, ALICE)
    [ask] = sent(await before.press(ALICE, f"cx:{bid}"))
    assert ask.text == texts.confirm_cancel(LINE_12)

    after = Harness(shop, db)  # новый процесс: пустое состояние в памяти
    assert texts_of(await after.press(ALICE, "my")) == [texts.my_bookings([LINE_12])]
    assert texts_of(await after.press(ALICE, f"cx:{bid}")) == [texts.confirm_cancel(LINE_12)]
    r = await after.press(ALICE, f"cxy:{bid}")  # подтверждение со старого сообщения
    assert texts_of(r, ALICE) == [texts.cancelled(LINE_12)]
    assert texts_of(r, ADMIN_CHAT_ID) == [T5_IVAN]


# --- 2.9 ---


async def reach_phone_step(tg, uid):
    await go_to_time(tg, uid)
    await tg.press(uid, "tm:202610091200")
    await tg.text(uid, "Иван")


async def test_my_bookings_resets_unfinished_booking_and_removes_phone_keyboard(tg, db):
    await reach_phone_step(tg, ALICE)
    msgs = sent(await tg.press(ALICE, "my"))
    assert msgs[0].text == texts.RESTART
    assert isinstance(msgs[0].reply_markup, ReplyKeyboardRemove)
    assert msgs[1].text == texts.NO_BOOKINGS
    # Запись сброшена: номер больше не ждём.
    assert texts_of(await tg.contact(ALICE, PHONE, owner_id=ALICE)) == [texts.UNKNOWN_MESSAGE]
    assert db.get_client(ALICE) is None


async def test_my_bookings_resets_on_earlier_steps(tg):
    await go_to_time(tg, ALICE)
    await tg.press(ALICE, "my")
    assert texts_of(await tg.press(ALICE, "tm:202610091200")) == [texts.STALE_BUTTON]


@pytest.mark.parametrize(
    "exit_action",
    [
        ("text", "/start"),
        ("press", "home"),
        ("press", "book"),
        ("press", "my"),
        ("press", "cxn"),
        ("press", "svc:men"),  # устаревшая кнопка
        ("press", "tm:202610091500"),  # кнопка другого времени из старого сообщения
        ("press", "zzz"),
    ],
)
async def test_any_exit_from_phone_step_removes_keyboard(tg, exit_action):
    await reach_phone_step(tg, ALICE)
    kind, value = exit_action
    r = await (tg.text(ALICE, value) if kind == "text" else tg.press(ALICE, value))
    removed = [m for m in sent(r) if isinstance(m.reply_markup, ReplyKeyboardRemove)]
    assert len(removed) == 1, exit_action
