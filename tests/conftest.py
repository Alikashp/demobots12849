from datetime import UTC, datetime
from itertools import count
from typing import Any

import pytest
from aiogram import Bot
from aiogram.client.session.base import BaseSession
from aiogram.methods import (
    AnswerCallbackQuery,
    EditMessageReplyMarkup,
    EditMessageText,
    SendMessage,
    TelegramMethod,
)
from aiogram.types import (
    CallbackQuery,
    Chat,
    Contact,
    Message,
    MessageOriginUser,
    Update,
    User,
)

from saqal import clock
from saqal.app import build_dispatcher
from saqal.clock import TZ
from saqal.config import Settings, load_shop
from saqal.db import Database

ADMIN_CHAT_ID = -100500


@pytest.fixture
def shop():
    return load_shop()


@pytest.fixture
def db(tmp_path):
    return Database(str(tmp_path / "test.db"))


@pytest.fixture
def frozen_now(monkeypatch):
    """Подмена текущего времени (А3). Возвращает функцию установки времени по Казани."""
    state = {"now": datetime(2026, 10, 9, 9, 0, tzinfo=TZ).astimezone(UTC)}
    monkeypatch.setattr(clock, "now", lambda: state["now"])

    def set_now(dt: datetime) -> None:
        state["now"] = dt.astimezone(UTC)

    return set_now


class FakeSession(BaseSession):
    """Записывает запросы к Telegram и отвечает правдоподобными объектами."""

    def __init__(self) -> None:
        super().__init__()
        self.requests: list[TelegramMethod] = []
        self._ids = count(1000)

    async def make_request(self, bot: Bot, method: TelegramMethod, timeout: int | None = None):
        self.requests.append(method)
        if isinstance(method, SendMessage | EditMessageText):
            chat_id = method.chat_id or 0
            return Message(
                message_id=next(self._ids),
                date=datetime.now(UTC),
                chat=Chat(id=chat_id, type="private" if chat_id > 0 else "supergroup"),
                text=method.text,
            )
        if isinstance(method, EditMessageReplyMarkup):
            return True
        if isinstance(method, AnswerCallbackQuery):
            return True
        return True

    async def close(self) -> None:
        pass

    async def stream_content(self, *args: Any, **kwargs: Any):  # pragma: no cover
        raise NotImplementedError


class Harness:
    """Клиент Telegram для тестов: шлёт апдейты в диспетчер и читает ответы бота."""

    def __init__(self, shop, db) -> None:
        self.session = FakeSession()
        self.bot = Bot("42:TEST", session=self.session)
        self.settings = Settings(bot_token="42:TEST", admin_chat_id=ADMIN_CHAT_ID, db_path=db.path)
        self.dp = build_dispatcher(shop, db, self.settings)
        self._update_ids = count(1)
        self._msg_ids = count(1)

    @staticmethod
    def user(uid: int) -> User:
        return User(id=uid, is_bot=False, first_name=f"User{uid}")

    async def _feed(self, **kwargs) -> list[TelegramMethod]:
        before = len(self.session.requests)
        await self.dp.feed_update(self.bot, Update(update_id=next(self._update_ids), **kwargs))
        return self.session.requests[before:]

    def _message(self, uid: int, **kwargs) -> Message:
        return Message(
            message_id=next(self._msg_ids),
            date=datetime.now(UTC),
            chat=Chat(id=uid, type="private"),
            from_user=self.user(uid),
            **kwargs,
        )

    async def text(self, uid: int, text: str):
        return await self._feed(message=self._message(uid, text=text))

    async def contact(self, uid: int, phone: str, owner_id: int | None, forwarded: bool = False):
        extra = {}
        if forwarded:
            extra["forward_origin"] = MessageOriginUser(
                date=datetime.now(UTC), sender_user=self.user(uid)
            )
        contact = Contact(phone_number=phone, first_name="X", user_id=owner_id)
        return await self._feed(message=self._message(uid, contact=contact, **extra))

    async def press(self, uid: int, data: str):
        message = self._message(uid, text="…")
        cb = CallbackQuery(
            id=str(next(self._update_ids)),
            from_user=self.user(uid),
            chat_instance="ci",
            data=data,
            message=message,
        )
        return await self._feed(callback_query=cb)


@pytest.fixture
def tg(shop, db, frozen_now):
    return Harness(shop, db)


def sent(requests, chat_id=None) -> list[SendMessage | EditMessageText]:
    """Сообщения бота (новые и отредактированные), опционально — в один чат."""
    out = [r for r in requests if isinstance(r, SendMessage | EditMessageText)]
    if chat_id is not None:
        out = [r for r in out if r.chat_id == chat_id]
    return out


def texts_of(requests, chat_id=None) -> list[str]:
    return [r.text for r in sent(requests, chat_id)]


def buttons(request) -> list[tuple[str, str | None]]:
    """(текст, callback_data) всех inline-кнопок сообщения."""
    markup = request.reply_markup
    if markup is None or not hasattr(markup, "inline_keyboard"):
        return []
    return [(b.text, b.callback_data) for row in markup.inline_keyboard for b in row]
