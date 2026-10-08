"""Клавиатуры. Данные кнопок: короткие строки «шаг:значение», проверяются заново (А9)."""

from collections.abc import Sequence
from datetime import date, datetime

from aiogram.types import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    KeyboardButton,
    ReplyKeyboardMarkup,
)

from . import texts
from .clock import TZ
from .config import Shop

BOOK = "book"
HOME = "home"
ANY_MASTER = "any"
SERVICE = "svc"
MASTER = "mst"
DAY = "day"
TIME = "tm"
BACK = "back"
MY = "my"
CANCEL_ASK = "cx"
CANCEL_YES = "cxy"
CANCEL_NO = "cxn"
BROADCAST_SEND = "bcs"
BROADCAST_CANCEL = "bcc"

DAY_FMT = "%Y%m%d"
TIME_FMT = "%Y%m%d%H%M"


def _btn(text: str, data: str) -> InlineKeyboardButton:
    return InlineKeyboardButton(text=text, callback_data=data)


def _back(target: str) -> list[InlineKeyboardButton]:
    return [_btn(texts.BTN_BACK, f"{BACK}:{target}")]


def greeting() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[[_btn(texts.BTN_BOOK, BOOK)], [_btn(texts.BTN_MY, MY)]]
    )


def my_bookings(bookings: Sequence[tuple[int, datetime]]) -> InlineKeyboardMarkup:
    """Своя кнопка «Отменить» у каждой записи (М1)."""
    rows = [[_btn(texts.btn_cancel(start), f"{CANCEL_ASK}:{bid}")] for bid, start in bookings]
    rows.append([_btn(texts.BTN_HOME, HOME)])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def reminder(booking_id: int) -> InlineKeyboardMarkup:
    """Н1: ведёт на то же подтверждение отмены, что и «Мои записи»."""
    return InlineKeyboardMarkup(
        inline_keyboard=[[_btn(texts.BTN_CANCEL_BOOKING, f"{CANCEL_ASK}:{booking_id}")]]
    )


def broadcast_preview(broadcast_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                _btn(texts.BTN_BROADCAST_SEND, f"{BROADCAST_SEND}:{broadcast_id}"),
                _btn(texts.BTN_BROADCAST_CANCEL, f"{BROADCAST_CANCEL}:{broadcast_id}"),
            ]
        ]
    )


def no_bookings() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[_btn(texts.BTN_BOOK, BOOK)]])


def confirm_cancel(booking_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [_btn(texts.BTN_CANCEL_YES, f"{CANCEL_YES}:{booking_id}")],
            [_btn(texts.BTN_CANCEL_NO, CANCEL_NO)],
        ]
    )


def after_booking() -> InlineKeyboardMarkup:
    """Под Т1: записаться ещё раз или посмотреть свои записи."""
    return InlineKeyboardMarkup(
        inline_keyboard=[[_btn(texts.BTN_BOOK, BOOK)], [_btn(texts.BTN_MY, MY)]]
    )


def after_cancel() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[[_btn(texts.BTN_BOOK, BOOK)], [_btn(texts.BTN_MY, MY)]]
    )


def cancel_problem() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[[_btn(texts.BTN_MY, MY)], [_btn(texts.BTN_HOME, HOME)]]
    )


def home() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[_btn(texts.BTN_HOME, HOME)]])


def services(shop: Shop) -> InlineKeyboardMarkup:
    rows = [[_btn(texts.btn_service(s), f"{SERVICE}:{s.id}")] for s in shop.services]
    rows.append(_back(HOME))
    return InlineKeyboardMarkup(inline_keyboard=rows)


def masters(shop: Shop) -> InlineKeyboardMarkup:
    rows = [[_btn(texts.btn_master(m), f"{MASTER}:{m.id}")] for m in shop.masters]
    rows.append([_btn(texts.BTN_ANY_MASTER, f"{MASTER}:{ANY_MASTER}")])
    rows.append(_back(SERVICE))
    return InlineKeyboardMarkup(inline_keyboard=rows)


def days(items: Sequence[date]) -> InlineKeyboardMarkup:
    rows = [[_btn(texts.fmt_date(d), f"{DAY}:{d.strftime(DAY_FMT)}")] for d in items]
    rows.append(_back(MASTER))
    return InlineKeyboardMarkup(inline_keyboard=rows)


def times(starts: Sequence[datetime]) -> InlineKeyboardMarkup:
    buttons = [
        _btn(texts.fmt_time(s), f"{TIME}:{s.astimezone(TZ).strftime(TIME_FMT)}") for s in starts
    ]
    rows = [buttons[i : i + 4] for i in range(0, len(buttons), 4)]
    rows.append(_back(DAY))
    return InlineKeyboardMarkup(inline_keyboard=rows)


def share_phone() -> ReplyKeyboardMarkup:
    # П1: контакт отдаётся только через кнопку обычной клавиатуры.
    return ReplyKeyboardMarkup(
        keyboard=[[KeyboardButton(text=texts.BTN_SHARE_PHONE, request_contact=True)]],
        resize_keyboard=True,
    )


def parse_day(value: str) -> date | None:
    try:
        return datetime.strptime(value, DAY_FMT).date()
    except ValueError:
        return None


def parse_time(value: str) -> datetime | None:
    try:
        return datetime.strptime(value, TIME_FMT).replace(tzinfo=TZ)
    except ValueError:
        return None
