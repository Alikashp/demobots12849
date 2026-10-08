"""Постоянное меню, «Контакты», новый адрес и inline-меню на текст вне команд (К10, К11, Д1, Д8)."""

import pytest

from saqal import texts
from tests.conftest import buttons, is_main_menu, sent, texts_of
from tests.test_flow import ALICE, PHONE, book_new, go_to_time

CONTACTS = "📍 Адрес: Новые Салмачи, ул. Невская, 14а\n📞 Умар, +7 958 628 30 90"
INLINE_MENU = [
    (texts.BTN_MENU_BOOK, "book"),
    (texts.BTN_MY, "my"),
    (texts.BTN_CONTACTS, "contacts"),
]


async def test_start_shows_persistent_menu_and_new_address(tg):
    [msg] = sent(await tg.text(ALICE, "/start"))
    assert is_main_menu(msg.reply_markup)
    assert "📍 Новые Салмачи, ул. Невская, 14а" in msg.text


async def test_t1_has_new_address(tg):
    r = await book_new(tg, ALICE)
    assert "📍 Новые Салмачи, ул. Невская, 14а\nЖдём вас!" in texts_of(r, ALICE)[-1]


async def test_menu_contacts(tg):
    assert texts_of(await tg.text(ALICE, texts.BTN_CONTACTS)) == [CONTACTS]
    assert texts_of(await tg.press(ALICE, "contacts")) == [CONTACTS]


async def test_menu_book_starts_booking(tg):
    [msg] = sent(await tg.text(ALICE, texts.BTN_MENU_BOOK))
    assert msg.text == texts.CHOOSE_SERVICE
    assert ("Мужская стрижка · 1 400 ₽ · 60 мин", "svc:men") in buttons(msg)
    # Дальше запись идёт обычным путём.
    assert texts_of(await tg.press(ALICE, "svc:men"))[0].startswith("«Мужская стрижка»")


async def test_menu_my_bookings(tg, db):
    assert texts_of(await tg.text(ALICE, texts.BTN_MY)) == [texts.NO_BOOKINGS]
    await book_new(tg, ALICE)
    [msg] = sent(await tg.text(ALICE, texts.BTN_MY))
    assert msg.text.startswith("📋 Ваши записи:")


async def test_menu_button_on_name_step_is_not_a_name(tg, db):
    await go_to_time(tg, ALICE)
    await tg.press(ALICE, "tm:202610091200")
    r = await tg.text(ALICE, texts.BTN_MENU_BOOK)
    assert texts_of(r) == [texts.CHOOSE_SERVICE]  # запись начата заново
    assert db.get_client(ALICE) is None


async def test_menu_on_phone_step_restores_menu(tg):
    await go_to_time(tg, ALICE)
    await tg.press(ALICE, "tm:202610091200")
    await tg.text(ALICE, "Иван")
    msgs = sent(await tg.text(ALICE, texts.BTN_MY))
    assert msgs[0].text == texts.RESTART
    assert is_main_menu(msgs[0].reply_markup)


async def test_contacts_do_not_interrupt_booking(tg):
    await go_to_time(tg, ALICE)
    assert texts_of(await tg.text(ALICE, texts.BTN_CONTACTS)) == [CONTACTS]
    r = await tg.press(ALICE, "tm:202610091200")
    assert texts_of(r) == [texts.ASK_NAME]


async def test_after_phone_menu_comes_back(tg):
    await go_to_time(tg, ALICE)
    await tg.press(ALICE, "tm:202610091200")
    await tg.text(ALICE, "Иван")
    received, _ = sent(await tg.contact(ALICE, PHONE, owner_id=ALICE), ALICE)
    assert received.text == texts.PHONE_RECEIVED
    assert is_main_menu(received.reply_markup)


@pytest.mark.parametrize("text", ["привет", "сколько стоит стрижка?", "/unknown"])
async def test_text_outside_commands_gets_inline_menu(tg, text):
    [msg] = sent(await tg.text(ALICE, text))
    assert msg.text == texts.UNKNOWN_MESSAGE
    assert buttons(msg) == INLINE_MENU


async def test_text_mid_booking_gets_inline_menu(tg):
    await go_to_time(tg, ALICE)
    [msg] = sent(await tg.text(ALICE, "а можно вечером?"))
    assert buttons(msg) == INLINE_MENU


async def test_home_shows_inline_menu(tg):
    [msg] = sent(await tg.press(ALICE, "home"))
    assert buttons(msg) == INLINE_MENU
