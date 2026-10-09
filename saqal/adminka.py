"""Админка в личном чате администратора: «Записи» и отмена (АА2–АА4, АЗ1–АЗ2).

Каждое действие проверяет роль заново (А13): фильтр роутера смотрит ADMIN_IDS на каждом
апдейте. Кнопки админки у не-администратора не совпадают ни с одним обработчиком этого
роутера и уходят в ответ «кнопка устарела» (К9).
"""

import logging
from datetime import date

from aiogram import Bot, F, Router
from aiogram.exceptions import TelegramAPIError
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message

from . import clock, keyboards, slots, texts
from .admin import split_message
from .config import Settings, Shop
from .db import Booking, CancelCheck, Database
from .handlers import describe, reset, show, stale

log = logging.getLogger(__name__)

ADMIN = "adm"
DAYS = f"{ADMIN}:days"
DAY = f"{ADMIN}:d"
CANCEL_ASK = f"{ADMIN}:cx"
CANCEL_YES = f"{ADMIN}:cxy"

PROBLEMS = {
    CancelCheck.NOT_FOUND: texts.ADMIN_CANCEL_NOT_FOUND,
    CancelCheck.ALREADY_CANCELLED: texts.ADMIN_CANCEL_ALREADY,
    CancelCheck.STARTED: texts.ADMIN_CANCEL_STARTED,
}


def is_admin(event: Message | CallbackQuery, settings: Settings) -> bool:
    return settings.is_admin(event.from_user.id if event.from_user else None)


def _btn(text: str, data: str) -> InlineKeyboardButton:
    return InlineKeyboardButton(text=text, callback_data=data)


def _kb(rows: list[list[InlineKeyboardButton]]) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=rows)


def to_days() -> list[InlineKeyboardButton]:
    return [_btn(texts.BTN_ADMIN_TO_DAYS, DAYS)]


def card(shop: Shop, b: Booking) -> str:
    service_title, master_name = describe(shop, b)
    return texts.admin_booking_card(
        service_title, master_name, b.start, b.client.name, b.client.phone
    )


# --- Меню (АА2) ---


async def on_admin_menu(message: Message, state: FSMContext, bot: Bot, settings: Settings):
    await reset(state, bot, message.chat.id, settings)
    # В Ф6 только «Записи»; «Расписание» и «Записать клиента» — в Ф7 и Ф8.
    await message.answer(
        texts.ADMIN_MENU, reply_markup=_kb([[_btn(texts.BTN_ADMIN_BOOKINGS, DAYS)]])
    )


# --- Записи (АЗ1) ---


async def on_days(callback: CallbackQuery, shop: Shop) -> None:
    await callback.answer()
    days = slots.booking_days(shop, clock.now())
    rows = [[_btn(texts.fmt_date(d), f"{DAY}:{d.strftime(keyboards.DAY_FMT)}")] for d in days]
    await show(callback, callback.message, texts.ADMIN_CHOOSE_DAY, _kb(rows))


def day_view(shop: Shop, db: Database, day: date) -> tuple[str, InlineKeyboardMarkup]:
    now = clock.now()
    bookings = db.day_bookings(*slots.local_day_bounds(day))
    master_ids = [m.id for m in shop.masters]
    extra = [mid for mid in dict.fromkeys(b.master_id for b in bookings) if mid not in master_ids]
    blocks, buttons = [], []
    for mid in master_ids + extra:
        master = shop.master(mid)
        name = master.name if master else mid
        own = [b for b in bookings if b.master_id == mid]
        lines = [name]
        for b in own:
            service_title, _ = describe(shop, b)
            lines.append(
                texts.admin_booking_line(b.start, service_title, b.client.name, b.client.phone)
            )
            if b.start > now:  # АЗ2: отменить можно только не начавшуюся запись
                buttons.append(
                    [
                        _btn(
                            texts.btn_admin_cancel(b.start, name, b.client.name),
                            f"{CANCEL_ASK}:{b.id}",
                        )
                    ]
                )
        if not own:
            lines.append(texts.TODAY_NO_BOOKINGS)
        blocks.append("\n".join(lines))
    text = texts.admin_day_header(day) + "\n\n" + "\n\n".join(blocks)
    return text, _kb([*buttons, to_days()])


async def on_day(
    callback: CallbackQuery, shop: Shop, db: Database, bot: Bot, settings: Settings,
    state: FSMContext,
) -> None:  # fmt: skip
    day = keyboards.parse_day(callback.data.split(":", 2)[2])
    if day is None or day not in slots.booking_days(shop, clock.now()):
        await stale(callback, bot, state, settings)
        return
    await callback.answer()
    text, markup = day_view(shop, db, day)
    parts = split_message(text)
    if len(parts) == 1:
        await show(callback, callback.message, text, markup)
        return
    # 6.4: длинный список — несколькими сообщениями, кнопки под последним.
    for i, part in enumerate(parts):
        await callback.message.answer(part, reply_markup=markup if i == len(parts) - 1 else None)


# --- Отмена (АЗ2) ---


def booking_id(callback: CallbackQuery) -> int:
    return int(callback.data.rsplit(":", 1)[1])


async def on_cancel_ask(callback: CallbackQuery, shop: Shop, db: Database) -> None:
    await callback.answer()
    result, b = db.check_cancel(booking_id(callback), None, clock.now())
    if result is not CancelCheck.OK:
        await show(callback, callback.message, PROBLEMS[result], _kb([to_days()]))
        return
    day = b.start.astimezone(clock.TZ).strftime(keyboards.DAY_FMT)
    markup = _kb(
        [
            [_btn(texts.BTN_ADMIN_CANCEL_YES, f"{CANCEL_YES}:{b.id}")],
            [_btn(texts.BTN_ADMIN_CANCEL_NO, f"{DAY}:{day}")],
        ]
    )
    await show(callback, callback.message, texts.admin_confirm_cancel(card(shop, b)), markup)


async def on_cancel_yes(
    callback: CallbackQuery, shop: Shop, db: Database, bot: Bot, settings: Settings
) -> None:
    await callback.answer()
    # Та же транзакция, что и у клиента (М4): одна отмена при любом числе нажатий.
    result, b = db.cancel_booking(booking_id(callback), None, clock.now())
    if result is not CancelCheck.OK:
        await show(callback, callback.message, PROBLEMS[result], _kb([to_days()]))
        return
    log.info("booking %s cancelled by admin %s", b.id, callback.from_user.id)
    service_title, master_name = describe(shop, b)
    try:  # Т5 — в чат администратора
        await bot.send_message(
            settings.admin_chat_id,
            texts.admin_cancelled(
                b.client.name, b.client.phone, service_title, master_name, b.start
            ),
        )
    except TelegramAPIError as e:
        log.warning("admin notification failed: booking=%s error=%s", b.id, type(e).__name__)
    notified = True
    try:  # Т6 — клиенту; не доставлено — отмена всё равно в силе (6.7)
        await bot.send_message(
            b.client.user_id,
            texts.booking_cancelled_by_shop(
                shop, b.client.name, service_title, master_name, b.start
            ),
        )
    except TelegramAPIError as e:
        notified = False
        log.warning("client notification failed: booking=%s error=%s", b.id, type(e).__name__)
    await show(
        callback,
        callback.message,
        texts.admin_cancel_done(card(shop, b), notified, b.client.phone),
        _kb([to_days()]),
    )


def build_adminka_router() -> Router:
    router = Router(name="adminka")
    router.message.filter(F.chat.type == "private", is_admin)
    router.callback_query.filter(F.message.chat.type == "private", is_admin)
    router.callback_query.filter(lambda c: isinstance(c.message, Message))
    router.message.register(on_admin_menu, F.text == texts.BTN_ADMIN)
    cb = router.callback_query.register
    cb(on_days, F.data == DAYS)
    cb(on_day, F.data.regexp(rf"^{DAY}:\d{{8}}$"))
    cb(on_cancel_ask, F.data.regexp(rf"^{CANCEL_ASK}:\d{{1,18}}$"))
    cb(on_cancel_yes, F.data.regexp(rf"^{CANCEL_YES}:\d{{1,18}}$"))
    return router
