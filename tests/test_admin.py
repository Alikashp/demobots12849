"""Ф4: команды владельца /today и /broadcast — критерии 4.1–4.8, 4.10."""

import logging
import sqlite3
from datetime import datetime

import pytest
from aiogram.exceptions import (
    TelegramForbiddenError,
    TelegramNetworkError,
    TelegramRetryAfter,
)
from aiogram.methods import SendMessage

from saqal import admin, texts
from saqal.clock import TZ
from tests.conftest import (
    ADMIN_CHAT_ID,
    BOT_USERNAME,
    RACE_OFFSETS,
    Harness,
    after_yields,
    buttons,
    sent,
    texts_of,
)
from tests.test_flow import ALICE, BOB, PHONE, book_new
from tests.test_timezone import server_in_utc  # noqa: F401  (фикстура)

OWNER = 900  # пишет в группу администратора
OWNER2 = 901  # второй администратор в той же группе
OTHER_GROUP = -777
CAROL, DAN, EVE = 333, 444, 555

TEXT = "Друзья, привет!\nВ субботу работаем до 18:00.\n\nЖдём вас 💈"


@pytest.fixture(autouse=True)
def no_send_pause(monkeypatch):
    monkeypatch.setattr(admin, "SEND_PAUSE", 0)


def rows(db, sql, *args):
    conn = sqlite3.connect(db.path)
    try:
        result = conn.execute(sql, args).fetchall()
        conn.commit()
        return result
    finally:
        conn.close()


def to_users(requests, users) -> dict[int, list[str]]:
    return {u: texts_of(requests, u) for u in users}


async def admin_cmd(tg, text, uid=OWNER):
    return await tg.text(uid, text, chat_id=tg.settings.admin_chat_id)


async def preview(tg, text=TEXT):
    """/broadcast в чате администратора; вернуть (сообщения, id рассылки)."""
    r = await admin_cmd(tg, f"/broadcast {text}")
    msg = sent(r)[-1]
    return r, int(buttons(msg)[0][1].split(":")[1])


async def three_recipients(tg):
    for uid in (ALICE, BOB, CAROL):
        await tg.text(uid, "/start")


# --- 4.1 ---


@pytest.mark.parametrize("cmd", ["/today", f"/today@{BOT_USERNAME}"])
async def test_today_in_admin_group(tg, cmd):
    r = await admin_cmd(tg, cmd)
    [msg] = sent(r, ADMIN_CHAT_ID)
    assert msg.text.startswith("Записи на 9 октября (пт)")


async def test_command_for_another_bot_is_ignored(tg):
    assert sent(await admin_cmd(tg, "/today@other_bot")) == []


async def test_broadcast_in_admin_group_with_mention(tg):
    r = await admin_cmd(tg, f"/broadcast@{BOT_USERNAME} {TEXT}")
    assert texts_of(r, ADMIN_CHAT_ID)[-1] == TEXT


async def test_owner_private_chat_as_admin_chat(shop, db, frozen_now):
    tg = Harness(shop, db, admin_chat_id=OWNER)
    r = await tg.text(OWNER, "/today")
    assert texts_of(r, OWNER)[0].startswith("Записи на 9 октября (пт)")
    r = await tg.text(OWNER, f"/broadcast {TEXT}")
    assert texts_of(r, OWNER)[-1] == TEXT
    # Владелец в своём личном чате по-прежнему может записаться как клиент.
    r = await tg.text(OWNER, "/start")
    assert texts_of(r) == [texts.greeting(shop)]


@pytest.mark.parametrize("cmd", ["/today", f"/broadcast {TEXT}", f"/today@{BOT_USERNAME}"])
async def test_other_group_silent(tg, db, cmd):
    assert sent(await tg.text(OWNER, cmd, chat_id=OTHER_GROUP)) == []
    assert rows(db, "SELECT COUNT(*) FROM broadcasts") == [(0,)]


@pytest.mark.parametrize("cmd", ["/today", f"/broadcast {TEXT}", "/broadcast"])
async def test_foreign_private_chat_gets_unknown_text_reply(tg, db, cmd):
    r = await tg.text(BOB, cmd)
    assert texts_of(r) == [texts.UNKNOWN_MESSAGE]
    assert rows(db, "SELECT COUNT(*) FROM broadcasts") == [(0,)]


# --- 4.2 ---


async def test_today_lists_masters_in_config_order_with_all_todays_bookings(tg, db, frozen_now):
    await book_new(tg, ALICE, time="202610091200")  # Умар 12:00
    await book_new(tg, BOB, name="Пётр", phone="79005550000", time="202610091000")  # Умар 10:00
    await book_new(
        tg, CAROL, name="Кэрол", phone="79006660000", time="202610091500", service="beard",
        master="barber3",
    )  # fmt: skip
    await book_new(tg, DAN, name="Дэн", phone="79007770000", time="202610091300")  # отменит
    await book_new(tg, EVE, name="Ева", phone="79008880000", time="202610101200")  # завтра
    [dan] = rows(db, "SELECT id FROM bookings WHERE user_id = ?", DAN)
    await tg.press(DAN, f"cxy:{dan[0]}")

    frozen_now(datetime(2026, 10, 9, 13, 30, tzinfo=TZ))  # 10:00 и 12:00 уже прошли
    [msg] = sent(await admin_cmd(tg, "/today"))
    assert msg.text == (
        "Записи на 9 октября (пт)\n"
        "\n"
        "Умар\n"
        "10:00 — Мужская стрижка — Пётр, +79005550000\n"
        "12:00 — Мужская стрижка — Иван, +79001234567\n"
        "\n"
        "Барбер 2\n"
        "записей нет\n"
        "\n"
        "Барбер 3\n"
        "15:00 — Моделирование бороды — Кэрол, +79006660000"
    )


async def test_today_empty_day(tg):
    [msg] = sent(await admin_cmd(tg, "/today"))
    assert msg.text == (
        "Записи на 9 октября (пт)\n\nУмар\nзаписей нет\n\nБарбер 2\nзаписей нет\n\n"
        "Барбер 3\nзаписей нет"
    )


async def test_today_date_is_kazan_date(tg, db, frozen_now, server_in_utc):  # noqa: F811
    await book_new(tg, ALICE, time="202610101000", day="20261010")
    # 8 окт 21:30 UTC = 9 окт 00:30 по Казани: «сегодня» — 9 октября, запись 10-го не видна.
    frozen_now(datetime(2026, 10, 9, 0, 30, tzinfo=TZ))
    [msg] = sent(await admin_cmd(tg, "/today"))
    assert msg.text.startswith("Записи на 9 октября (пт)")
    assert "Иван" not in msg.text
    # 9 окт 21:30 UTC = 10 окт 00:30 по Казани: уже 10 октября.
    frozen_now(datetime(2026, 10, 10, 0, 30, tzinfo=TZ))
    [msg] = sent(await admin_cmd(tg, "/today"))
    assert msg.text.startswith("Записи на 10 октября (сб)")
    assert "10:00 — Мужская стрижка — Иван, +79001234567" in msg.text


def test_long_today_is_split_by_lines():
    text = "\n".join(f"строка {i:04d} " + "x" * 50 for i in range(200))
    parts = admin.split_message(text)
    assert len(parts) > 1
    assert all(len(p) <= admin.MESSAGE_LIMIT for p in parts)
    assert "\n".join(parts) == text


# --- 4.3 ---


async def test_broadcast_preview_exact_text_count_and_buttons(tg, db):
    await three_recipients(tg)
    r, bid = await preview(tg)
    header, body = sent(r, ADMIN_CHAT_ID)
    assert header.text == texts.broadcast_preview_header(3)
    assert "Получателей: 3." in header.text
    assert body.text == TEXT  # с переносами строк, ровно как у клиентов
    assert buttons(body) == [("Отправить", f"bcs:{bid}"), ("Отмена", f"bcc:{bid}")]
    # Предпросмотр ничего не рассылает.
    assert all(texts_of(r, u) == [] for u in (ALICE, BOB, CAROL))


async def test_broadcast_text_on_next_line_after_command(tg):
    r = await admin_cmd(tg, "/broadcast\nПервая строка\nВторая")
    assert texts_of(r, ADMIN_CHAT_ID)[-1] == "Первая строка\nВторая"


@pytest.mark.parametrize("cmd", ["/broadcast", "/broadcast   ", f"/broadcast@{BOT_USERNAME}"])
async def test_broadcast_without_text_gives_hint(tg, db, cmd):
    assert texts_of(await admin_cmd(tg, cmd)) == [texts.BROADCAST_HINT]
    assert "/broadcast " in texts.BROADCAST_HINT  # с примером
    assert rows(db, "SELECT COUNT(*) FROM broadcasts") == [(0,)]


# --- 4.4 ---


async def test_cancel_sends_nothing(tg):
    await three_recipients(tg)
    _, bid = await preview(tg)
    r = await tg.press(OWNER, f"bcc:{bid}", chat_id=ADMIN_CHAT_ID)
    assert texts_of(r, ADMIN_CHAT_ID) == [texts.BROADCAST_CANCELLED]
    r = await tg.press(OWNER, f"bcs:{bid}", chat_id=ADMIN_CHAT_ID)
    assert texts_of(r, ADMIN_CHAT_ID) == [texts.BROADCAST_ALREADY_CANCELLED]
    assert all(texts_of(tg.session.requests, u)[1:] == [] for u in (ALICE, BOB, CAROL))


async def test_send_delivers_text_and_reports(tg):
    await three_recipients(tg)
    _, bid = await preview(tg)
    r = await tg.press(OWNER, f"bcs:{bid}", chat_id=ADMIN_CHAT_ID)
    assert to_users(r, (ALICE, BOB, CAROL)) == {ALICE: [TEXT], BOB: [TEXT], CAROL: [TEXT]}
    assert texts_of(r, ADMIN_CHAT_ID) == ["Рассылка завершена: доставлено 3 из 3."]
    # Клиенты получают текст без кнопок.
    assert all(m.reply_markup is None for m in sent(r) if m.chat_id != ADMIN_CHAT_ID)


# --- 4.5 ---


async def test_recipients_are_start_users_and_existing_clients_once_each(tg, db, shop):
    await tg.text(ALICE, "/start")  # только /start
    await tg.text(DAN, "/start")
    await tg.text(DAN, "/start")  # дважды — один получатель
    await book_new(tg, CAROL, name="Кэрол", phone="79006660000")  # и /start, и запись
    # Клиент из базы до Ф4: записан, но в таблице users его нет.
    rows(
        db, "INSERT INTO clients (user_id, name, phone, created_at) VALUES (?, 'Боб', '+7', 0)", BOB
    )
    await tg.text(EVE, "/start", chat_id=OTHER_GROUP)  # /start в группе не считается

    _, bid = await preview(tg)
    r = await tg.press(OWNER, f"bcs:{bid}", chat_id=ADMIN_CHAT_ID)
    assert to_users(r, (ALICE, BOB, CAROL, DAN, EVE)) == {
        ALICE: [TEXT],
        BOB: [TEXT],
        CAROL: [TEXT],
        DAN: [TEXT],
        EVE: [],
    }
    assert texts_of(r, ADMIN_CHAT_ID) == ["Рассылка завершена: доставлено 4 из 4."]


# --- 4.6 ---


async def test_failed_recipients_do_not_stop_broadcast(tg):
    for uid in (ALICE, BOB, CAROL, DAN, EVE):
        await tg.text(uid, "/start")
    tg.session.fail_for[BOB] = lambda m: TelegramForbiddenError(m, "bot was blocked by the user")
    tg.session.fail_for[CAROL] = lambda m: TelegramNetworkError(m, "connection reset")
    tg.session.fail_for[DAN] = lambda m: TelegramRetryAfter(m, "flood", retry_after=0)

    def eve_flood_once(m):
        del tg.session.fail_for[EVE]  # следующая попытка пройдёт
        return TelegramRetryAfter(m, "flood", retry_after=0)

    tg.session.fail_for[EVE] = eve_flood_once  # первая попытка — лимит, повтор проходит
    _, bid = await preview(tg)
    r = await tg.press(OWNER, f"bcs:{bid}", chat_id=ADMIN_CHAT_ID)
    assert to_users(r, (ALICE, EVE)) == {ALICE: [TEXT], EVE: [TEXT]}
    assert texts_of(r, ADMIN_CHAT_ID) == ["Рассылка завершена: доставлено 2 из 5."]


# --- 4.7 ---


def broadcast_texts(requests, users) -> int:
    return sum(texts_of(requests, u).count(TEXT) for u in users)


async def test_repeated_send_gives_one_broadcast(tg):
    await three_recipients(tg)
    _, bid = await preview(tg)
    r1 = await tg.press(OWNER, f"bcs:{bid}", chat_id=ADMIN_CHAT_ID)
    r2 = await tg.press(OWNER, f"bcs:{bid}", chat_id=ADMIN_CHAT_ID)
    assert broadcast_texts(r1 + r2, (ALICE, BOB, CAROL)) == 3
    assert texts_of(r2, ADMIN_CHAT_ID) == [texts.BROADCAST_ALREADY_SENT]


@pytest.mark.parametrize("offsets", RACE_OFFSETS)
@pytest.mark.parametrize("second_admin", [OWNER, OWNER2])
async def test_parallel_send_gives_one_broadcast(tg, offsets, second_admin):
    await three_recipients(tg)
    _, bid = await preview(tg)
    r = await tg.together(
        after_yields(offsets[0], tg.press(OWNER, f"bcs:{bid}", chat_id=ADMIN_CHAT_ID)),
        after_yields(offsets[1], tg.press(second_admin, f"bcs:{bid}", chat_id=ADMIN_CHAT_ID)),
    )
    assert broadcast_texts(r, (ALICE, BOB, CAROL)) == 3
    reports = [t for t in texts_of(r, ADMIN_CHAT_ID) if t.startswith("Рассылка завершена")]
    assert reports == ["Рассылка завершена: доставлено 3 из 3."]
    others = [t for t in texts_of(r, ADMIN_CHAT_ID) if not t.startswith("Рассылка завершена")]
    assert len(others) == 1
    assert others[0] in (texts.BROADCAST_ALREADY_SENT, texts.BROADCAST_IN_PROGRESS)


async def test_old_preview_buttons_work_after_restart(shop, db, frozen_now):
    before = Harness(shop, db)
    await three_recipients(before)
    _, bid = await preview(before)
    after = Harness(shop, db)  # перезапуск: память пустая, база та же
    r = await after.press(OWNER, f"bcs:{bid}", chat_id=ADMIN_CHAT_ID)
    assert broadcast_texts(r, (ALICE, BOB, CAROL)) == 3
    r = await Harness(shop, db).press(OWNER, f"bcs:{bid}", chat_id=ADMIN_CHAT_ID)
    assert texts_of(r, ADMIN_CHAT_ID) == [texts.BROADCAST_ALREADY_SENT]


async def test_restart_during_sending_never_sends_twice(shop, db, frozen_now):
    before = Harness(shop, db)
    await three_recipients(before)
    _, bid = await preview(before)
    rows(db, "UPDATE broadcasts SET status = 'sending' WHERE id = ?", bid)  # упал посреди
    after = Harness(shop, db)
    r = await after.press(OWNER, f"bcs:{bid}", chat_id=ADMIN_CHAT_ID)
    assert texts_of(r, ADMIN_CHAT_ID) == [texts.BROADCAST_IN_PROGRESS]
    assert broadcast_texts(r, (ALICE, BOB, CAROL)) == 0
    r = await after.press(OWNER, f"bcc:{bid}", chat_id=ADMIN_CHAT_ID)
    assert texts_of(r, ADMIN_CHAT_ID) == [texts.BROADCAST_IN_PROGRESS]


async def test_unknown_preview_is_reported_stale(tg):
    r = await tg.press(OWNER, "bcs:999999", chat_id=ADMIN_CHAT_ID)
    assert texts_of(r, ADMIN_CHAT_ID) == [texts.BROADCAST_UNKNOWN]


# --- 4.8 ---


@pytest.mark.parametrize("chat", ["private", "group"])
async def test_preview_buttons_only_in_admin_chat(tg, db, chat):
    await three_recipients(tg)
    _, bid = await preview(tg)
    for data in (f"bcs:{bid}", f"bcc:{bid}"):
        if chat == "private":
            r = await tg.press(BOB, data)
            assert texts_of(r) == [texts.STALE_BUTTON]
        else:
            r = await tg.press(OWNER, data, chat_id=OTHER_GROUP)
            assert sent(r) == []
        assert broadcast_texts(r, (ALICE, BOB, CAROL)) == 0
    assert rows(db, "SELECT status FROM broadcasts WHERE id = ?", bid) == [("draft",)]


# --- 4.10 ---


async def test_logs_have_no_names_phones_or_broadcast_text(tg, caplog):
    caplog.set_level(logging.DEBUG)
    await book_new(tg, ALICE)
    await admin_cmd(tg, "/today")
    _, bid = await preview(tg, "СЕКРЕТНАЯ акция для своих")
    await tg.press(OWNER, f"bcs:{bid}", chat_id=ADMIN_CHAT_ID)
    assert "broadcast" in caplog.text
    for secret in ("Иван", PHONE, "СЕКРЕТНАЯ", "акция"):
        assert secret not in caplog.text


async def test_no_send_message_for_broadcast_to_admin_chat_itself(tg):
    """Чат администратора — не получатель, если владелец сам не нажимал /start."""
    await three_recipients(tg)
    _, bid = await preview(tg)
    r = await tg.press(OWNER, f"bcs:{bid}", chat_id=ADMIN_CHAT_ID)
    assert TEXT not in [m.text for m in r if isinstance(m, SendMessage) and m.chat_id == OWNER]
