"""Сценарий записи клиента (К1–К9)."""

import logging
from contextlib import suppress
from datetime import date, datetime, timedelta

from aiogram import Bot, F, Router
from aiogram.exceptions import TelegramAPIError, TelegramBadRequest
from aiogram.filters import Command, CommandStart, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, InlineKeyboardMarkup, Message

from . import clock, keyboards, reminders, slots, texts
from .config import Service, Settings, Shop
from .db import Booking as BookingRow
from .db import CancelCheck, Database

log = logging.getLogger(__name__)

NAME_MAX = 50


class Booking(StatesGroup):
    service = State()
    master = State()
    day = State()
    time = State()
    name = State()
    phone = State()


# --- Вспомогательное ---


async def show(
    callback: CallbackQuery | None, message: Message, text: str, markup: InlineKeyboardMarkup
) -> None:
    """Из кнопки — редактируем её сообщение; иначе — новое сообщение."""
    if callback is not None and isinstance(callback.message, Message):
        try:
            await callback.message.edit_text(text, reply_markup=markup)
            return
        except TelegramBadRequest:
            pass
    await message.answer(text, reply_markup=markup)


def chosen_service(shop: Shop, data: dict) -> Service | None:
    return shop.service(data.get("service_id", ""))


def chosen_master_ids(shop: Shop, data: dict) -> list[str] | None:
    master = data.get("master")
    if master == keyboards.ANY_MASTER:
        return [m.id for m in shop.masters]
    if master and shop.master(master):
        return [master]
    return None


async def stale(callback: CallbackQuery, bot: Bot, state: FSMContext, settings: Settings) -> None:
    """К9: устаревшая кнопка — понятный ответ и выход в начало (с шага К6 — без клавиатуры)."""
    await callback.answer()
    await reset(state, bot, callback.from_user.id, settings)
    await bot.send_message(callback.from_user.id, texts.STALE_BUTTON, reply_markup=keyboards.home())


def menu(user_id: int, settings: Settings):
    """Постоянное меню; у администратора — с кнопкой «⚙️ Админка» (АА2)."""
    return keyboards.main_menu(admin=settings.is_admin(user_id))


async def reset(
    state: FSMContext, bot: Bot, chat_id: int, settings: Settings, *, restore_menu: bool = True
) -> None:
    """Сбросить незавершённую запись (К1)."""
    if restore_menu and await state.get_state() == Booking.phone.state:
        # Запись бросили на шаге К6 — вместо кнопки «Поделиться номером» снова меню.
        await bot.send_message(chat_id, texts.RESTART, reply_markup=menu(chat_id, settings))
    await state.clear()


async def show_services(cb: CallbackQuery | None, msg: Message, state: FSMContext, shop: Shop):
    await state.set_state(Booking.service)
    await show(cb, msg, texts.CHOOSE_SERVICE, keyboards.services(shop))


async def show_masters(
    cb: CallbackQuery | None, msg: Message, state: FSMContext, shop: Shop, service: Service
):
    await state.set_state(Booking.master)
    await show(cb, msg, texts.choose_master(service), keyboards.masters(shop))


async def show_days(
    cb: CallbackQuery | None, msg: Message, state: FSMContext, shop: Shop, db: Database
):
    data = await state.get_data()
    service, master_ids = chosen_service(shop, data), chosen_master_ids(shop, data)
    now = clock.now()
    days = slots.available_days(
        shop, service, master_ids, db.busy_for_window(shop, now), now, db.schedule()
    )
    await state.set_state(Booking.day)
    if not days:
        await show(cb, msg, texts.NO_DAYS, keyboards.days([]))
        return
    await show(cb, msg, texts.CHOOSE_DATE, keyboards.days(days))


async def show_times(
    cb: CallbackQuery | None, msg: Message, state: FSMContext, shop: Shop, db: Database
):
    data = await state.get_data()
    service, master_ids = chosen_service(shop, data), chosen_master_ids(shop, data)
    day = date.fromisoformat(data["day"])
    now = clock.now()
    starts = slots.available_starts(
        shop,
        service,
        master_ids,
        day,
        db.busy_between(*slots.local_day_bounds(day)),
        now,
        db.schedule(),
    )
    if not starts:
        # День успел заполниться — возвращаем к выбору дня.
        await msg.answer(texts.NO_TIMES)
        await show_days(None, msg, state, shop, db)
        return
    await state.set_state(Booking.time)
    await show(cb, msg, texts.choose_time(day), keyboards.times(starts))


async def finish_booking(
    msg: Message,
    user_id: int,
    state: FSMContext,
    shop: Shop,
    db: Database,
    bot: Bot,
    settings: Settings,
    *,
    remove_reply_keyboard: bool = False,
) -> None:
    """К7: создать запись; если слот заняли — сообщение и возврат к выбору времени (С5)."""
    data = await state.get_data()
    service, master_ids = chosen_service(shop, data), chosen_master_ids(shop, data)
    start = datetime.fromisoformat(data["start"])
    new_client = (data["name"], data["phone"]) if "phone" in data else None
    # После шага К6 вместо кнопки «Поделиться номером» возвращаем постоянное меню.
    kb_remove = menu(user_id, settings) if remove_reply_keyboard else None

    booking = db.create_booking(
        shop=shop,
        user_id=user_id,
        service=service,
        master_ids=master_ids,
        start=start,
        now=clock.now(),
        new_client=new_client,
    )
    if booking is None:
        log.info("slot taken: user=%s", user_id)
        await msg.answer(texts.SLOT_TAKEN, reply_markup=kb_remove)
        await show_times(None, msg, state, shop, db)
        return

    await state.clear()
    master = shop.master(booking.master_id)
    log.info("booking %s created: user=%s master=%s", booking.id, user_id, booking.master_id)
    if remove_reply_keyboard:
        # Одно сообщение не может и сменить клавиатуру внизу, и нести inline-кнопки.
        await msg.answer(texts.PHONE_RECEIVED, reply_markup=kb_remove)
    await msg.answer(
        texts.confirmation(shop, booking.client.name, service, master, booking.start),
        reply_markup=keyboards.after_booking(),
    )
    try:
        await bot.send_message(
            settings.admin_chat_id,
            texts.admin_new_booking(
                booking.client.name, booking.client.phone, service, master, booking.start
            ),
        )
    except TelegramAPIError as e:
        log.warning("admin notification failed: booking=%s error=%s", booking.id, type(e).__name__)


# --- К1: старт и выход в начало ---


async def on_start(
    message: Message, state: FSMContext, shop: Shop, db: Database, bot: Bot, settings: Settings
) -> None:
    db.remember_user(message.from_user.id, clock.now())  # В3: получатель рассылки
    await reset(state, bot, message.chat.id, settings, restore_menu=False)
    # Приветствие ставит постоянное меню внизу экрана (К10), в том числе вместо кнопки номера.
    await message.answer(texts.greeting(shop), reply_markup=menu(message.from_user.id, settings))


async def on_home(
    callback: CallbackQuery, state: FSMContext, shop: Shop, bot: Bot, settings: Settings
) -> None:
    await callback.answer()
    await reset(state, bot, callback.from_user.id, settings)
    await show(callback, callback.message, texts.greeting(shop), keyboards.greeting())


# --- К2: услуга ---


async def on_book(
    callback: CallbackQuery, state: FSMContext, shop: Shop, bot: Bot, settings: Settings
) -> None:
    await callback.answer()
    await reset(state, bot, callback.from_user.id, settings)
    await show_services(callback, callback.message, state, shop)


async def on_back_to_service(callback: CallbackQuery, state: FSMContext, shop: Shop) -> None:
    await callback.answer()
    await show_services(callback, callback.message, state, shop)


async def on_service(
    callback: CallbackQuery, state: FSMContext, shop: Shop, bot: Bot, settings: Settings
) -> None:
    service = shop.service(callback.data.split(":", 1)[1])
    if service is None:
        await stale(callback, bot, state, settings)
        return
    await callback.answer()
    await state.update_data(service_id=service.id)
    await show_masters(callback, callback.message, state, shop, service)


# --- К3: мастер ---


async def on_back_to_master(
    callback: CallbackQuery, state: FSMContext, shop: Shop, bot: Bot, settings: Settings
):
    service = chosen_service(shop, await state.get_data())
    if service is None:
        await stale(callback, bot, state, settings)
        return
    await callback.answer()
    await show_masters(callback, callback.message, state, shop, service)


async def on_master(
    callback: CallbackQuery,
    state: FSMContext,
    shop: Shop,
    db: Database,
    bot: Bot,
    settings: Settings,
) -> None:
    master = callback.data.split(":", 1)[1]
    if (master != keyboards.ANY_MASTER and shop.master(master) is None) or chosen_service(
        shop, await state.get_data()
    ) is None:
        await stale(callback, bot, state, settings)
        return
    await callback.answer()
    await state.update_data(master=master)
    await show_days(callback, callback.message, state, shop, db)


# --- К4: дата ---


async def on_back_to_day(callback: CallbackQuery, state: FSMContext, shop: Shop, db: Database):
    await callback.answer()
    await show_days(callback, callback.message, state, shop, db)


async def on_day(
    callback: CallbackQuery,
    state: FSMContext,
    shop: Shop,
    db: Database,
    bot: Bot,
    settings: Settings,
) -> None:
    day = keyboards.parse_day(callback.data.split(":", 1)[1])
    if day is None or day not in slots.booking_days(shop, clock.now()):
        await stale(callback, bot, state, settings)
        return
    await callback.answer()
    await state.update_data(day=day.isoformat())
    await show_times(callback, callback.message, state, shop, db)


# --- К5: время ---


async def on_time(
    callback: CallbackQuery,
    state: FSMContext,
    shop: Shop,
    db: Database,
    bot: Bot,
    settings: Settings,
) -> None:
    data = await state.get_data()
    start = keyboards.parse_time(callback.data.split(":", 1)[1])
    service, master_ids = chosen_service(shop, data), chosen_master_ids(shop, data)
    if (
        start is None
        or service is None
        or master_ids is None
        or start.date().isoformat() != data.get("day")
    ):
        await stale(callback, bot, state, settings)
        return
    await callback.answer()
    msg = callback.message
    if isinstance(msg, Message):
        with suppress(TelegramBadRequest):
            await msg.edit_reply_markup(reply_markup=None)

    now = clock.now()
    day_start, _ = slots.local_day_bounds(start.date())
    busy = db.busy_between(day_start, day_start + timedelta(days=1))
    if not slots.is_start_available(shop, service, master_ids, start, busy, now, db.schedule()):
        await msg.answer(texts.SLOT_TAKEN)
        await show_times(None, msg, state, shop, db)
        return

    await state.update_data(start=start.isoformat())
    if "phone" in data or db.get_client(callback.from_user.id) is not None:
        # Вернувшийся клиент (или новый, у которого слот заняли после ввода номера) — сразу запись.
        await finish_booking(msg, callback.from_user.id, state, shop, db, bot, settings)
        return
    await state.set_state(Booking.name)
    await msg.answer(texts.ASK_NAME)


# --- К6: имя и телефон (только при первой записи) ---


async def on_name(message: Message, state: FSMContext) -> None:
    name = (message.text or "").strip()
    if not name or name.startswith("/") or len(name) > NAME_MAX:
        await message.answer(texts.NAME_INVALID)
        return
    await state.update_data(name=name)
    await state.set_state(Booking.phone)
    await message.answer(texts.ask_phone(name), reply_markup=keyboards.share_phone())


def normalize_phone(raw: str) -> str:
    digits = "".join(ch for ch in raw if ch.isdigit())
    return f"+{digits}"


async def on_contact(
    message: Message,
    state: FSMContext,
    shop: Shop,
    db: Database,
    bot: Bot,
    settings: Settings,
) -> None:
    contact = message.contact
    own = (
        message.forward_origin is None
        and contact.user_id is not None
        and contact.user_id == message.from_user.id
    )
    if not own:
        await message.answer(texts.PHONE_NOT_OWN)
        return
    await state.update_data(phone=normalize_phone(contact.phone_number))
    await finish_booking(
        message, message.from_user.id, state, shop, db, bot, settings, remove_reply_keyboard=True
    )


async def on_phone_other(message: Message) -> None:
    await message.answer(texts.PHONE_USE_BUTTON)


async def on_time_repeat(
    callback: CallbackQuery, state: FSMContext, db: Database, bot: Bot, settings: Settings
) -> None:
    """Кнопка времени вне шага К5: повторное нажатие или устаревшая кнопка (2.10)."""
    start = keyboards.parse_time(callback.data.split(":", 1)[1])
    data = await state.get_data()
    if (
        start is not None
        and await state.get_state() in (Booking.name.state, Booking.phone.state)
        and data.get("start") == start.isoformat()
    ):
        # То же время уже выбрано, ждём имя или телефон — ничего не меняем.
        await callback.answer()
        return
    if start is not None and db.active_booking_at(callback.from_user.id, start) is not None:
        # Клиент уже записан на это время этим же нажатием.
        await callback.answer(texts.ALREADY_BOOKED)
        return
    await stale(callback, bot, state, settings)


# --- Мои записи и отмена (М1–М4). Не зависят от состояния в памяти (2.8) ---


def describe(shop: Shop, booking: BookingRow) -> tuple[str, str]:
    service = shop.service(booking.service_id)
    master = shop.master(booking.master_id)
    return (
        service.title if service else booking.service_id,
        master.name if master else booking.master_id,
    )


def booking_line(shop: Shop, booking: BookingRow) -> str:
    return texts.booking_line(*describe(shop, booking), booking.start)


CANCEL_PROBLEMS = {
    CancelCheck.NOT_FOUND: texts.CANCEL_NOT_FOUND,
    CancelCheck.ALREADY_CANCELLED: texts.CANCEL_ALREADY,
    CancelCheck.STARTED: texts.CANCEL_STARTED,
}


def callback_id(callback: CallbackQuery) -> int:
    return int(callback.data.split(":", 1)[1])


async def show_my_bookings(
    callback: CallbackQuery | None, msg: Message, user_id: int, shop: Shop, db: Database
) -> None:
    bookings = db.upcoming_bookings(user_id, clock.now())
    if not bookings:
        await show(callback, msg, texts.NO_BOOKINGS, keyboards.no_bookings())
        return
    await show(
        callback,
        msg,
        texts.my_bookings([booking_line(shop, b) for b in bookings]),
        keyboards.my_bookings([(b.id, b.start) for b in bookings]),
    )


async def on_my(
    callback: CallbackQuery,
    state: FSMContext,
    shop: Shop,
    db: Database,
    bot: Bot,
    settings: Settings,
) -> None:
    """М1; в любой момент сбрасывает незавершённую запись (2.9)."""
    await callback.answer()
    await reset(state, bot, callback.from_user.id, settings)
    await show_my_bookings(callback, callback.message, callback.from_user.id, shop, db)


async def on_cancel_ask(
    callback: CallbackQuery,
    state: FSMContext,
    shop: Shop,
    db: Database,
    bot: Bot,
    settings: Settings,
) -> None:
    """М2: одно подтверждение с данными записи."""
    await callback.answer()
    await reset(state, bot, callback.from_user.id, settings)
    result, booking = db.check_cancel(callback_id(callback), callback.from_user.id, clock.now())
    if result is not CancelCheck.OK:
        await show(callback, callback.message, CANCEL_PROBLEMS[result], keyboards.cancel_problem())
        return
    await show(
        callback,
        callback.message,
        texts.confirm_cancel(booking_line(shop, booking)),
        keyboards.confirm_cancel(booking.id),
    )


async def on_cancel_no(
    callback: CallbackQuery,
    state: FSMContext,
    shop: Shop,
    db: Database,
    bot: Bot,
    settings: Settings,
) -> None:
    """Отказ от отмены ничего не меняет — возвращаем список записей."""
    await callback.answer()
    await reset(state, bot, callback.from_user.id, settings)
    await show_my_bookings(callback, callback.message, callback.from_user.id, shop, db)


async def on_cancel_yes(
    callback: CallbackQuery,
    state: FSMContext,
    shop: Shop,
    db: Database,
    bot: Bot,
    settings: Settings,
) -> None:
    """М2–М4: отмена; повтор и опоздание — понятный ответ без Т5."""
    await callback.answer()
    await reset(state, bot, callback.from_user.id, settings)
    result, booking = db.cancel_booking(callback_id(callback), callback.from_user.id, clock.now())
    if result is not CancelCheck.OK:
        await show(callback, callback.message, CANCEL_PROBLEMS[result], keyboards.cancel_problem())
        return
    log.info("booking %s cancelled: user=%s", booking.id, callback.from_user.id)
    line = booking_line(shop, booking)
    await show(callback, callback.message, texts.cancelled(line), keyboards.after_cancel())
    try:
        await bot.send_message(
            settings.admin_chat_id,
            texts.admin_cancelled(
                booking.client.name, booking.client.phone, *describe(shop, booking), booking.start
            ),
        )
    except TelegramAPIError as e:
        log.warning("admin notification failed: booking=%s error=%s", booking.id, type(e).__name__)


# --- /test_reminder (Н5). Команды нет в меню: set_my_commands бот не вызывает ---


async def on_test_reminder(message: Message, db: Database) -> None:
    now = clock.now()
    upcoming = db.upcoming_bookings(message.from_user.id, now)
    if not upcoming:
        await message.answer(texts.TEST_REMINDER_NO_BOOKINGS, reply_markup=keyboards.no_bookings())
        return
    db.add_test_reminder(upcoming[0].id, now + reminders.TEST_DELAY)
    log.info("test reminder scheduled: booking=%s", upcoming[0].id)
    await message.answer(texts.TEST_REMINDER_SCHEDULED)


# --- Постоянное меню (К10) и «Контакты» ---


async def on_menu_book(
    message: Message, state: FSMContext, shop: Shop, bot: Bot, settings: Settings
) -> None:
    await reset(state, bot, message.chat.id, settings)
    await show_services(None, message, state, shop)


async def on_menu_my(
    message: Message, state: FSMContext, shop: Shop, db: Database, bot: Bot, settings: Settings
) -> None:
    await reset(state, bot, message.chat.id, settings)
    await show_my_bookings(None, message, message.from_user.id, shop, db)


async def on_menu_contacts(message: Message, shop: Shop) -> None:
    """Контакты не прерывают начатую запись."""
    await message.answer(texts.contacts(shop))


async def on_contacts(callback: CallbackQuery, shop: Shop, bot: Bot) -> None:
    await callback.answer()
    await bot.send_message(callback.from_user.id, texts.contacts(shop))


# --- Прочее ---


async def on_other_message(message: Message) -> None:
    """К11: текст не по командам — меню inline-кнопками."""
    await message.answer(texts.UNKNOWN_MESSAGE, reply_markup=keyboards.greeting())


async def on_stale(
    callback: CallbackQuery, bot: Bot, state: FSMContext, settings: Settings
) -> None:
    await stale(callback, bot, state, settings)


# --- Маршрутизация ---


def build_routers() -> list[Router]:
    """Порядок важен: сначала шаги записи, в конце — ответы на всё остальное (К9)."""
    back = keyboards.BACK
    router = Router(name="booking")
    # Бот работает с клиентами только в личных чатах (§1).
    router.message.filter(F.chat.type == "private")
    # Кнопки под недоступными (слишком старыми) сообщениями уходят в fallback (К9).
    router.callback_query.filter(F.message.chat.type == "private")
    router.callback_query.filter(lambda c: isinstance(c.message, Message))

    cb = router.callback_query.register
    router.message.register(on_start, CommandStart())
    router.message.register(on_test_reminder, Command("test_reminder"))
    # Кнопки постоянного меню — раньше шагов записи: на шаге имени это не имя.
    router.message.register(on_menu_book, F.text == texts.BTN_MENU_BOOK)
    router.message.register(on_menu_my, F.text == texts.BTN_MY)
    router.message.register(on_menu_contacts, F.text == texts.BTN_CONTACTS)
    cb(on_contacts, F.data == keyboards.CONTACTS)
    cb(on_home, F.data == keyboards.HOME)
    cb(on_home, StateFilter(Booking.service), F.data == f"{back}:{keyboards.HOME}")
    cb(on_book, F.data == keyboards.BOOK)
    cb(on_back_to_service, StateFilter(Booking.master), F.data == f"{back}:{keyboards.SERVICE}")
    cb(on_service, StateFilter(Booking.service), F.data.startswith(f"{keyboards.SERVICE}:"))
    cb(on_back_to_master, StateFilter(Booking.day), F.data == f"{back}:{keyboards.MASTER}")
    cb(on_master, StateFilter(Booking.master), F.data.startswith(f"{keyboards.MASTER}:"))
    cb(on_back_to_day, StateFilter(Booking.time), F.data == f"{back}:{keyboards.DAY}")
    cb(on_day, StateFilter(Booking.day), F.data.startswith(f"{keyboards.DAY}:"))
    cb(on_time, StateFilter(Booking.time), F.data.startswith(f"{keyboards.TIME}:"))
    cb(on_time_repeat, F.data.startswith(f"{keyboards.TIME}:"))
    cb(on_my, F.data == keyboards.MY)
    cb(on_cancel_ask, F.data.regexp(rf"^{keyboards.CANCEL_ASK}:\d{{1,18}}$"))
    cb(on_cancel_yes, F.data.regexp(rf"^{keyboards.CANCEL_YES}:\d{{1,18}}$"))
    cb(on_cancel_no, F.data == keyboards.CANCEL_NO)
    router.message.register(on_name, StateFilter(Booking.name))
    router.message.register(on_contact, StateFilter(Booking.phone), F.contact)
    router.message.register(on_phone_other, StateFilter(Booking.phone))

    fallback = Router(name="fallback")
    fallback.message.filter(F.chat.type == "private")
    fallback.callback_query.filter(F.message.chat.type == "private")
    fallback.message.register(on_other_message)
    fallback.callback_query.register(on_stale)
    return [router, fallback]
