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

DAY_FMT = "%Y%m%d"
TIME_FMT = "%Y%m%d%H%M"


def _btn(text: str, data: str) -> InlineKeyboardButton:
    return InlineKeyboardButton(text=text, callback_data=data)


def _back(target: str) -> list[InlineKeyboardButton]:
    return [_btn(texts.BTN_BACK, f"{BACK}:{target}")]


def greeting() -> InlineKeyboardMarkup:
    # «Мои записи» появится в Ф2.
    return InlineKeyboardMarkup(inline_keyboard=[[_btn(texts.BTN_BOOK, BOOK)]])


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
