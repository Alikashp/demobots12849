"""Ф6: админка — критерии 6.3, 6.5–6.8."""

import sqlite3
from datetime import datetime

import pytest
from aiogram.exceptions import TelegramForbiddenError

from saqal import admin, texts
from saqal.clock import TZ
from saqal.reminders import ReminderService
from tests.conftest import (
    ADMIN_CHAT_ID,
    RACE_OFFSETS,
    Harness,
    after_yields,
    buttons,
    sent,
    texts_of,
)
from tests.test_flow import ALICE, BOB, book_new

BOSS = 700  # администратор из ADMIN_IDS
DAY = "20261009"
T6_IVAN = (
    "😔 Иван, ваша запись в 💈барбершоп SAQAL отменена: «Мужская стрижка», мастер Умар, "
    "9 октября (пт) в 12:00. Чтобы выбрать другое время, нажмите /start."
)
T5_IVAN = "❌ Отмена записи: Иван, +79001234567\nМужская стрижка, мастер Умар, 9 октября (пт) 12:00"


@pytest.fixture
def tg(shop, db, frozen_now):
    return Harness(shop, db, admin_ids=frozenset({BOSS}))


@pytest.fixture(autouse=True)
def no_send_pause(monkeypatch):
    monkeypatch.setattr(admin, "SEND_PAUSE", 0)


def status(db, booking_id):
    conn = sqlite3.connect(db.path)
    try:
        return conn.execute("SELECT status FROM bookings WHERE id = ?", (booking_id,)).fetchone()[0]
    finally:
        conn.close()


async def booked_id(tg, db, uid=ALICE, **kw) -> int:
    await book_new(tg, uid, **kw)
    [b] = db.upcoming_bookings(uid, datetime(2026, 1, 1, tzinfo=TZ))
    return b.id


def t6_count(requests, uid=ALICE) -> int:
    return sum(t.startswith("😔") for t in texts_of(requests, uid))


# --- 6.3 ---


async def test_non_admin_sees_no_admin_button(tg):
    [msg] = sent(await tg.text(ALICE, "/start"))
    flat = [b.text for row in msg.reply_markup.keyboard for b in row]
    assert texts.BTN_ADMIN not in flat
    [msg] = sent(await tg.text(BOSS, "/start"))
    assert texts.BTN_ADMIN in [b.text for row in msg.reply_markup.keyboard for b in row]


async def test_non_admin_typing_admin_button_gets_client_menu(tg):
    [msg] = sent(await tg.text(ALICE, texts.BTN_ADMIN))
    assert msg.text == texts.UNKNOWN_MESSAGE
    assert texts.ADMIN_MENU not in texts_of(tg.session.requests)


async def test_non_admin_admin_buttons_are_stale_and_change_nothing(tg, db):
    bid = await booked_id(tg, db)
    before = len(tg.session.requests)
    for data in ("adm:days", f"adm:d:{DAY}", f"adm:cx:{bid}", f"adm:cxy:{bid}", "adm:cxy:999"):
        r = await tg.press(BOB, data)
        assert texts_of(r) == [texts.STALE_BUTTON], data
    r = tg.session.requests[before:]
    assert not any("Иван" in t for t in texts_of(r, BOB))  # ничего не увидел
    assert status(db, bid) == "active"
    assert texts_of(r, ADMIN_CHAT_ID) == [] and t6_count(r) == 0


async def test_admin_removed_from_list_loses_access_after_restart(shop, db, frozen_now):
    first = Harness(shop, db, admin_ids=frozenset({BOSS}))
    bid = await booked_id(first, db)
    await first.press(BOSS, f"adm:cx:{bid}")
    later = Harness(shop, db, admin_ids=frozenset())  # BOSS убран из ADMIN_IDS
    r = await later.press(BOSS, f"adm:cxy:{bid}")  # кнопка из старого сообщения
    assert texts_of(r) == [texts.STALE_BUTTON]
    assert status(db, bid) == "active"


async def test_admin_buttons_in_group_chat_do_nothing(tg, db):
    bid = await booked_id(tg, db)
    r = await tg.press(BOSS, f"adm:cxy:{bid}", chat_id=-777)
    assert sent(r) == []
    assert status(db, bid) == "active"


# --- 6.5 ---


async def test_admin_cancel_sends_t6_and_t5_and_stops_reminders(tg, db, shop, frozen_now):
    bid = await booked_id(tg, db)
    [day] = sent(await tg.press(BOSS, f"adm:d:{DAY}"))
    assert "12:00 — Мужская стрижка — Иван, +79001234567" in day.text
    assert (texts.btn_admin_cancel(datetime(2026, 10, 9, 12, tzinfo=TZ), "Умар", "Иван"),
            f"adm:cx:{bid}") in buttons(day)  # fmt: skip

    [ask] = sent(await tg.press(BOSS, f"adm:cx:{bid}"))
    assert ask.text.startswith("Отменить запись?")
    assert (texts.BTN_ADMIN_CANCEL_YES, f"adm:cxy:{bid}") in buttons(ask)
    assert status(db, bid) == "active"  # одно подтверждение, до него ничего не меняется

    r = await tg.press(BOSS, f"adm:cxy:{bid}")
    assert texts_of(r, ALICE) == [T6_IVAN]
    assert texts_of(r, ADMIN_CHAT_ID) == [T5_IVAN]
    assert texts.ADMIN_CLIENT_NOTIFIED in texts_of(r, BOSS)[-1]
    assert status(db, bid) == "cancelled"

    svc = ReminderService(db, shop, tg.bot)
    frozen_now(datetime(2026, 10, 9, 10, 0, tzinfo=TZ))  # срок напоминания за 2 часа
    tasks = await svc.tick()
    assert tasks == []
    assert not [t for t in texts_of(tg.session.requests, ALICE) if t.startswith("💈")]


async def test_started_booking_has_no_cancel_button_and_cannot_be_cancelled(tg, db, frozen_now):
    bid = await booked_id(tg, db)
    frozen_now(datetime(2026, 10, 9, 12, 0, tzinfo=TZ))  # началась ровно сейчас
    [day] = sent(await tg.press(BOSS, f"adm:d:{DAY}"))
    assert "12:00 — Мужская стрижка — Иван" in day.text  # видна
    assert not [d for _, d in buttons(day) if d == f"adm:cx:{bid}"]  # но без «Отменить»
    for data in (f"adm:cx:{bid}", f"adm:cxy:{bid}"):  # подделанная кнопка
        assert texts_of(await tg.press(BOSS, data)) == [texts.ADMIN_CANCEL_STARTED]
    assert status(db, bid) == "active"
    assert t6_count(tg.session.requests) == 0


# --- 6.6 ---


async def test_repeated_admin_cancel_gives_one_cancel(tg, db):
    bid = await booked_id(tg, db)
    r1 = await tg.press(BOSS, f"adm:cxy:{bid}")
    r2 = await tg.press(BOSS, f"adm:cxy:{bid}")
    assert texts_of(r1 + r2, ADMIN_CHAT_ID).count(T5_IVAN) == 1
    assert t6_count(r1 + r2) == 1
    assert texts_of(r2, BOSS) == [texts.ADMIN_CANCEL_ALREADY]


@pytest.mark.parametrize("offsets", RACE_OFFSETS)
@pytest.mark.parametrize("other", ["admin", "client"])
async def test_parallel_cancel_admin_with_admin_or_client(tg, db, offsets, other):
    bid = await booked_id(tg, db)
    second = tg.press(BOSS, f"adm:cxy:{bid}") if other == "admin" else tg.press(ALICE, f"cxy:{bid}")
    r = await tg.together(
        after_yields(offsets[0], tg.press(BOSS, f"adm:cxy:{bid}")),
        after_yields(offsets[1], second),
    )
    assert status(db, bid) == "cancelled"
    assert texts_of(r, ADMIN_CHAT_ID).count(T5_IVAN) == 1
    assert t6_count(r) <= 1


# --- 6.7 ---


async def test_undelivered_t6_still_cancels_and_admin_is_warned(tg, db):
    bid = await booked_id(tg, db)
    tg.session.fail_for[ALICE] = lambda m: TelegramForbiddenError(m, "bot was blocked by the user")
    r = await tg.press(BOSS, f"adm:cxy:{bid}")
    assert status(db, bid) == "cancelled"
    assert texts_of(r, ADMIN_CHAT_ID) == [T5_IVAN]
    [done] = texts_of(r, BOSS)
    assert "Клиент не уведомлён" in done
    assert "+79001234567" in done


# --- 6.8 ---


async def test_today_in_admin_private_chat(tg, db):
    await book_new(tg, ALICE)
    [msg] = sent(await tg.text(BOSS, "/today"))
    assert msg.chat_id == BOSS
    assert msg.text.startswith("Записи на 9 октября (пт)")
    assert "12:00 — Мужская стрижка — Иван, +79001234567" in msg.text
    # Не-администратор в личном чате — как раньше: меню, ничего не выполняется.
    assert texts_of(await tg.text(BOB, "/today")) == [texts.UNKNOWN_MESSAGE]


async def test_broadcast_with_preview_buttons_in_admin_private_chat(tg, db):
    for uid in (ALICE, BOB):
        await tg.text(uid, "/start")
    r = await tg.text(BOSS, "/broadcast Привет!\nСкидка 10%")
    body = sent(r, BOSS)[-1]
    assert body.text == "Привет!\nСкидка 10%"
    send_data = buttons(body)[0][1]
    # Чужой личный чат: те же данные кнопки ничего не рассылают.
    r = await tg.press(BOB, send_data)
    assert texts_of(r) == [texts.STALE_BUTTON]
    assert "Привет!\nСкидка 10%" not in texts_of(tg.session.requests, ALICE)
    # Личный чат администратора: рассылка уходит.
    r = await tg.press(BOSS, send_data)
    assert texts_of(r, ALICE) == ["Привет!\nСкидка 10%"]
    assert texts_of(r, BOSS)[-1].startswith("Рассылка завершена: доставлено")


async def test_admin_chat_still_works_as_before(tg, db):
    r = await tg.text(BOB, "/today", chat_id=ADMIN_CHAT_ID)  # любой участник группы
    assert texts_of(r, ADMIN_CHAT_ID)[0].startswith("Записи на 9 октября (пт)")
