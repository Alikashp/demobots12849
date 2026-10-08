"""Сценарий клиента через диспетчер aiogram с фейковым Telegram: 1.1–1.3, 1.6, 1.8, 1.9."""

import logging
from datetime import datetime

from aiogram.methods import SendMessage
from aiogram.types import ReplyKeyboardMarkup, ReplyKeyboardRemove

from saqal import texts
from saqal.clock import TZ
from tests.conftest import ADMIN_CHAT_ID, buttons, sent, texts_of

ALICE = 111
BOB = 222
PHONE = "79001234567"

T1_IVAN = (
    "🤜🤛 Иван, вы записаны в 💈барбершоп SAQAL на услугу «Мужская стрижка» к мастеру Умар\n"
    "⌚ 9 октября (пт) в 12:00\n"
    "📍 Куюки, Казань\n"
    "Ждём вас!"
)
# Кнопки под Т1: записаться ещё раз или открыть свои записи.
AFTER_BOOKING = [(texts.BTN_BOOK, "book"), (texts.BTN_MY, "my")]
T4_IVAN = "🆕 Новая запись: Иван, +79001234567\nМужская стрижка, мастер Умар, 9 октября (пт) 12:00"


async def go_to_time(tg, uid, service="men", master="umar", day="20261009"):
    await tg.text(uid, "/start")
    await tg.press(uid, "book")
    await tg.press(uid, f"svc:{service}")
    await tg.press(uid, f"mst:{master}")
    return await tg.press(uid, f"day:{day}")


async def book_new(tg, uid, name="Иван", phone=PHONE, time="202610091200", **kw):
    await go_to_time(tg, uid, **kw)
    await tg.press(uid, f"tm:{time}")
    await tg.text(uid, name)
    return await tg.contact(uid, phone, owner_id=uid)


# --- 1.1 ---


async def test_new_client_full_flow_gets_t1_and_admin_gets_t4(tg):
    r = await tg.text(ALICE, "/start")
    [greeting] = sent(r)
    assert "SAQAL" in greeting.text
    assert buttons(greeting) == [(texts.BTN_BOOK, "book"), (texts.BTN_MY, "my")]  # 2.1

    r = await tg.press(ALICE, "book")
    [msg] = sent(r)
    assert msg.text == texts.CHOOSE_SERVICE
    assert ("Мужская стрижка · 1 400 ₽ · 60 мин", "svc:men") in buttons(msg)

    r = await tg.press(ALICE, "svc:men")
    [msg] = sent(r)
    assert ("Умар", "mst:umar") in buttons(msg)
    assert (texts.BTN_ANY_MASTER, "mst:any") in buttons(msg)

    r = await tg.press(ALICE, "mst:umar")
    [msg] = sent(r)
    assert msg.text == texts.CHOOSE_DATE
    assert buttons(msg)[0] == ("9 октября (пт)", "day:20261009")
    assert len(buttons(msg)) == 7 + 1  # 7 дней + «Назад»

    r = await tg.press(ALICE, "day:20261009")
    [msg] = sent(r)
    assert msg.text == "⌚ Свободное время на 9 октября (пт):"
    assert ("12:00", "tm:202610091200") in buttons(msg)

    r = await tg.press(ALICE, "tm:202610091200")
    assert texts_of(r) == [texts.ASK_NAME]

    r = await tg.text(ALICE, "Иван")
    [msg] = sent(r)
    assert msg.text == texts.ask_phone("Иван")
    assert isinstance(msg.reply_markup, ReplyKeyboardMarkup)
    assert msg.reply_markup.keyboard[0][0].request_contact is True

    r = await tg.contact(ALICE, PHONE, owner_id=ALICE)
    assert texts_of(r, ALICE) == [texts.PHONE_RECEIVED, T1_IVAN]
    assert buttons(sent(r, ALICE)[-1]) == AFTER_BOOKING
    assert texts_of(r, ADMIN_CHAT_ID) == [T4_IVAN]


# --- 1.2 ---


async def test_returning_client_books_right_after_time(tg):
    await book_new(tg, ALICE)
    await go_to_time(tg, ALICE)
    r = await tg.press(ALICE, "tm:202610091500")
    t1 = T1_IVAN.replace("в 12:00", "в 15:00")
    assert texts_of(r, ALICE) == [t1]
    assert buttons(sent(r, ALICE)[-1]) == AFTER_BOOKING
    assert texts.ASK_NAME not in texts_of(r)
    assert not any(isinstance(m.reply_markup, ReplyKeyboardMarkup) for m in sent(r))
    assert texts_of(r, ADMIN_CHAT_ID) == [T4_IVAN.replace(" 12:00", " 15:00")]


# --- 1.3 ---


async def test_phone_step_accepts_only_own_contact_via_button(tg, db):
    await go_to_time(tg, ALICE)
    await tg.press(ALICE, "tm:202610091200")
    await tg.text(ALICE, "Иван")

    r = await tg.contact(ALICE, "79990000000", owner_id=BOB)
    assert texts_of(r) == [texts.PHONE_NOT_OWN]

    r = await tg.contact(ALICE, PHONE, owner_id=ALICE, forwarded=True)
    assert texts_of(r) == [texts.PHONE_NOT_OWN]

    r = await tg.contact(ALICE, PHONE, owner_id=None)
    assert texts_of(r) == [texts.PHONE_NOT_OWN]

    r = await tg.text(ALICE, "+7 900 123-45-67")
    assert texts_of(r) == [texts.PHONE_USE_BUTTON]

    assert db.get_client(ALICE) is None

    r = await tg.contact(ALICE, PHONE, owner_id=ALICE)
    received, t1 = sent(r, ALICE)
    assert received.text == texts.PHONE_RECEIVED
    assert isinstance(received.reply_markup, ReplyKeyboardRemove)  # клавиатура убрана
    assert t1.text == T1_IVAN
    assert buttons(t1) == AFTER_BOOKING
    assert db.get_client(ALICE).phone == "+79001234567"


async def test_name_must_be_plain_text(tg):
    await go_to_time(tg, ALICE)
    await tg.press(ALICE, "tm:202610091200")
    assert texts_of(await tg.text(ALICE, "   ")) == [texts.NAME_INVALID]
    assert texts_of(await tg.text(ALICE, "x" * 51)) == [texts.NAME_INVALID]
    assert texts_of(await tg.contact(ALICE, PHONE, owner_id=ALICE)) == [texts.NAME_INVALID]


# --- 1.5 через бота ---


async def test_any_master_assigned_and_shown_in_t1(tg):
    await book_new(tg, BOB, name="Пётр", phone="79005550000")  # Умар, 12:00, 60 мин
    await go_to_time(tg, ALICE, master="any")
    await tg.press(ALICE, "tm:202610091500")
    await tg.text(ALICE, "Иван")
    r = await tg.contact(ALICE, PHONE, owner_id=ALICE)
    # Умар загружен (60 мин), Барбер 2 и Барбер 3 свободны — первый по конфигу.
    assert "к мастеру Барбер 2\n" in texts_of(r, ALICE)[-1]


# --- 1.6 через бота ---


async def test_slot_taken_while_new_client_enters_phone(tg, db):
    await book_new(tg, BOB, name="Пётр", phone="79005550000")  # Боб становится вернувшимся
    # Алиса дошла до телефона на 15:00 у Умара, а Боб (вернувшийся) занял 15:00 раньше.
    await go_to_time(tg, ALICE)
    await tg.press(ALICE, "tm:202610091500")
    await tg.text(ALICE, "Иван")
    await go_to_time(tg, BOB)
    await tg.press(BOB, "tm:202610091500")

    r = await tg.contact(ALICE, PHONE, owner_id=ALICE)
    msgs = sent(r, ALICE)
    assert [m.text for m in msgs] == [texts.SLOT_TAKEN, "⌚ Свободное время на 9 октября (пт):"]
    assert isinstance(msgs[0].reply_markup, ReplyKeyboardRemove)
    assert ("15:00", "tm:202610091500") not in buttons(msgs[1])
    assert texts_of(r, ADMIN_CHAT_ID) == []
    assert db.get_client(ALICE) is None

    # Повторно имя и телефон не спрашиваются: запись сразу после выбора времени.
    r = await tg.press(ALICE, "tm:202610091600")
    assert texts_of(r, ALICE) == [T1_IVAN.replace("в 12:00", "в 16:00")]


async def test_slot_taken_before_time_pressed(tg):
    await go_to_time(tg, ALICE)
    await book_new(tg, BOB, name="Пётр", phone="79005550000")  # Боб занимает 12:00 у Умара
    r = await tg.press(ALICE, "tm:202610091200")
    msgs = sent(r, ALICE)
    assert [m.text for m in msgs] == [texts.SLOT_TAKEN, "⌚ Свободное время на 9 октября (пт):"]
    assert ("12:00", "tm:202610091200") not in buttons(msgs[1])


# --- 1.8 ---


async def test_back_buttons_on_k2_to_k5(tg):
    await go_to_time(tg, ALICE)

    r = await tg.press(ALICE, "back:day")
    assert texts_of(r) == [texts.CHOOSE_DATE]
    r = await tg.press(ALICE, "back:mst")
    assert texts_of(r) == [texts.choose_master(tg.dp["shop"].service("men"))]
    r = await tg.press(ALICE, "back:svc")
    assert texts_of(r) == [texts.CHOOSE_SERVICE]
    r = await tg.press(ALICE, "back:home")
    assert texts_of(r) == [texts.greeting(tg.dp["shop"])]

    # Каждая клавиатура шагов К2–К5 содержит «Назад».
    r = await tg.press(ALICE, "book")
    assert (texts.BTN_BACK, "back:home") in buttons(sent(r)[0])
    r = await tg.press(ALICE, "svc:men")
    assert (texts.BTN_BACK, "back:svc") in buttons(sent(r)[0])
    r = await tg.press(ALICE, "mst:umar")
    assert (texts.BTN_BACK, "back:mst") in buttons(sent(r)[0])
    r = await tg.press(ALICE, "day:20261009")
    assert (texts.BTN_BACK, "back:day") in buttons(sent(r)[0])


async def test_start_resets_unfinished_booking(tg):
    await go_to_time(tg, ALICE)
    r = await tg.text(ALICE, "/start")
    assert texts_of(r) == [texts.greeting(tg.dp["shop"])]
    # Кнопка времени из прежнего шага теперь устарела.
    r = await tg.press(ALICE, "tm:202610091200")
    [msg] = sent(r)
    assert msg.text == texts.STALE_BUTTON
    assert buttons(msg) == [(texts.BTN_HOME, "home")]


async def test_start_on_phone_step_removes_reply_keyboard(tg):
    await go_to_time(tg, ALICE)
    await tg.press(ALICE, "tm:202610091200")
    await tg.text(ALICE, "Иван")
    r = await tg.text(ALICE, "/start")
    msgs = sent(r)
    assert msgs[0].text == texts.RESTART
    assert isinstance(msgs[0].reply_markup, ReplyKeyboardRemove)
    assert msgs[1].text == texts.greeting(tg.dp["shop"])


async def test_stale_and_forged_buttons_get_clear_answer(tg):
    # Без начатой записи.
    for data in ["svc:men", "mst:umar", "day:20261009", "tm:202610091200", "back:day", "zzz"]:
        r = await tg.press(ALICE, data)
        assert texts_of(r) == [texts.STALE_BUTTON], data
    # Подделанные значения в правильном шаге (А9).
    await tg.text(ALICE, "/start")
    await tg.press(ALICE, "book")
    assert texts_of(await tg.press(ALICE, "svc:nope")) == [texts.STALE_BUTTON]
    await tg.press(ALICE, "book")
    await tg.press(ALICE, "svc:men")
    assert texts_of(await tg.press(ALICE, "mst:nobody")) == [texts.STALE_BUTTON]
    await tg.press(ALICE, "book")
    await tg.press(ALICE, "svc:men")
    await tg.press(ALICE, "mst:umar")
    assert texts_of(await tg.press(ALICE, "day:20991231")) == [texts.STALE_BUTTON]
    # «В начало» из ответа на устаревшую кнопку.
    assert texts_of(await tg.press(ALICE, "home")) == [texts.greeting(tg.dp["shop"])]


async def test_time_from_another_day_is_rejected(tg):
    await go_to_time(tg, ALICE)
    r = await tg.press(ALICE, "tm:202610101200")
    assert texts_of(r) == [texts.STALE_BUTTON]


async def test_ignores_group_chats(tg):
    from aiogram.types import Chat, Message, Update

    msg = Message(
        message_id=1,
        date=datetime.now(TZ),
        chat=Chat(id=-1, type="group"),
        from_user=tg.user(ALICE),
        text="/start",
    )
    before = len(tg.session.requests)
    await tg.dp.feed_update(tg.bot, Update(update_id=999, message=msg))
    assert not [r for r in tg.session.requests[before:] if isinstance(r, SendMessage)]


# --- 1.9 ---


async def test_name_and_phone_not_in_logs(tg, caplog):
    caplog.set_level(logging.DEBUG)
    await book_new(tg, ALICE, name="Иван")
    await go_to_time(tg, ALICE)
    await tg.press(ALICE, "tm:202610091500")
    assert caplog.records, "логи должны писаться"
    assert PHONE not in caplog.text
    assert "Иван" not in caplog.text
