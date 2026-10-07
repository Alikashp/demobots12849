"""Сценарий записи клиента (К1–К9)."""

import logging
from contextlib import suppress
from datetime import date, datetime, timedelta

from aiogram import Bot, F, Router
from aiogram.exceptions import TelegramAPIError, TelegramBadRequest
from aiogram.filters import CommandStart, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, InlineKeyboardMarkup, Message, ReplyKeyboardRemove

from . import clock, keyboards, slots, texts
from .config import Service, Settings, Shop
from .db import Database

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


async def stale(callback: CallbackQuery, bot: Bot, state: FSMContext) -> None:
    """К9: устаревшая кнопка — понятный ответ и выход в начало."""
    await callback.answer()
    await state.clear()
    await bot.send_message(callback.from_user.id, texts.STALE_BUTTON, reply_markup=keyboards.home())


async def reset(state: FSMContext, bot: Bot, chat_id: int) -> None:
    """Сбросить незавершённую запись (К1)."""
    if await state.get_state() == Booking.phone.state:
        # Запись бросили на шаге К6 — убираем кнопку «Поделиться номером».
        await bot.send_message(chat_id, texts.RESTART, reply_markup=ReplyKeyboardRemove())
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
    days = slots.available_days(shop, service, master_ids, db.busy_for_window(shop, now), now)
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
        shop, service, master_ids, day, db.busy_between(*slots.local_day_bounds(day)), now
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
    kb_remove = ReplyKeyboardRemove() if remove_reply_keyboard else None

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
    await msg.answer(
        texts.confirmation(shop, booking.client.name, service, master, booking.start),
        reply_markup=kb_remove,
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


async def on_start(message: Message, state: FSMContext, shop: Shop, bot: Bot) -> None:
    await reset(state, bot, message.chat.id)
    await message.answer(texts.greeting(shop), reply_markup=keyboards.greeting())


async def on_home(callback: CallbackQuery, state: FSMContext, shop: Shop, bot: Bot) -> None:
    await callback.answer()
    await reset(state, bot, callback.from_user.id)
    await show(callback, callback.message, texts.greeting(shop), keyboards.greeting())


# --- К2: услуга ---


async def on_book(callback: CallbackQuery, state: FSMContext, shop: Shop, bot: Bot) -> None:
    await callback.answer()
    await reset(state, bot, callback.from_user.id)
    await show_services(callback, callback.message, state, shop)


async def on_back_to_service(callback: CallbackQuery, state: FSMContext, shop: Shop) -> None:
    await callback.answer()
    await show_services(callback, callback.message, state, shop)


async def on_service(callback: CallbackQuery, state: FSMContext, shop: Shop, bot: Bot) -> None:
    service = shop.service(callback.data.split(":", 1)[1])
    if service is None:
        await stale(callback, bot, state)
        return
    await callback.answer()
    await state.update_data(service_id=service.id)
    await show_masters(callback, callback.message, state, shop, service)


# --- К3: мастер ---


async def on_back_to_master(callback: CallbackQuery, state: FSMContext, shop: Shop, bot: Bot):
    service = chosen_service(shop, await state.get_data())
    if service is None:
        await stale(callback, bot, state)
        return
    await callback.answer()
    await show_masters(callback, callback.message, state, shop, service)


async def on_master(
    callback: CallbackQuery, state: FSMContext, shop: Shop, db: Database, bot: Bot
) -> None:
    master = callback.data.split(":", 1)[1]
    if (master != keyboards.ANY_MASTER and shop.master(master) is None) or chosen_service(
        shop, await state.get_data()
    ) is None:
        await stale(callback, bot, state)
        return
    await callback.answer()
    await state.update_data(master=master)
    await show_days(callback, callback.message, state, shop, db)


# --- К4: дата ---


async def on_back_to_day(callback: CallbackQuery, state: FSMContext, shop: Shop, db: Database):
    await callback.answer()
    await show_days(callback, callback.message, state, shop, db)


async def on_day(
    callback: CallbackQuery, state: FSMContext, shop: Shop, db: Database, bot: Bot
) -> None:
    day = keyboards.parse_day(callback.data.split(":", 1)[1])
    if day is None or day not in slots.booking_days(shop, clock.now()):
        await stale(callback, bot, state)
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
        await stale(callback, bot, state)
        return
    await callback.answer()
    msg = callback.message
    if isinstance(msg, Message):
        with suppress(TelegramBadRequest):
            await msg.edit_reply_markup(reply_markup=None)

    now = clock.now()
    day_start, _ = slots.local_day_bounds(start.date())
    busy = db.busy_between(day_start, day_start + timedelta(days=1))
    if not slots.is_start_available(shop, service, master_ids, start, busy, now):
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


# --- Прочее ---


async def on_other_message(message: Message, state: FSMContext) -> None:
    if await state.get_state() is None:
        await message.answer(texts.UNKNOWN_MESSAGE)
    else:
        await message.answer(texts.USE_BUTTONS)


async def on_stale(callback: CallbackQuery, bot: Bot, state: FSMContext) -> None:
    await stale(callback, bot, state)


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
    router.message.register(on_name, StateFilter(Booking.name))
    router.message.register(on_contact, StateFilter(Booking.phone), F.contact)
    router.message.register(on_phone_other, StateFilter(Booking.phone))

    fallback = Router(name="fallback")
    fallback.message.filter(F.chat.type == "private")
    fallback.callback_query.filter(F.message.chat.type == "private")
    fallback.message.register(on_other_message)
    fallback.callback_query.register(on_stale)
    return [router, fallback]
