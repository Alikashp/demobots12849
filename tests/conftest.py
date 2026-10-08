import asyncio
from collections.abc import Callable
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
    GetChat,
    GetMe,
    SendMessage,
    TelegramMethod,
)
from aiogram.types import (
    AcceptedGiftTypes,
    CallbackQuery,
    Chat,
    ChatFullInfo,
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
BOT_USERNAME = "saqal_test_bot"


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
        self.times: list[datetime] = []  # подменённое время каждого запроса
        self._ids = count(1000)
        # Сбои по чатам: chat_id -> фабрика исключения; чаты, где запрос зависает навсегда.
        self.fail_for: dict[int, Callable[[TelegramMethod], Exception]] = {}
        self.hang_for: set[int] = set()

    async def make_request(self, bot: Bot, method: TelegramMethod, timeout: int | None = None):
        # Как настоящая сеть: отдаём управление циклу, чтобы параллельные апдейты перемешивались.
        await asyncio.sleep(0)
        chat_id = getattr(method, "chat_id", None)
        if chat_id in self.hang_for:
            await asyncio.Event().wait()
        if chat_id in self.fail_for:
            raise self.fail_for[chat_id](method)
        self.requests.append(method)
        self.times.append(clock.now())
        if isinstance(method, SendMessage | EditMessageText):
            chat_id = method.chat_id or 0
            return Message(
                message_id=next(self._ids),
                date=datetime.now(UTC),
                chat=Chat(id=chat_id, type="private" if chat_id > 0 else "supergroup"),
                text=method.text,
            )
        if isinstance(method, GetChat):
            return ChatFullInfo(
                id=method.chat_id,
                type="private" if method.chat_id > 0 else "supergroup",
                accent_color_id=0,
                max_reaction_count=0,
                accepted_gift_types=AcceptedGiftTypes(
                    unlimited_gifts=False,
                    limited_gifts=False,
                    unique_gifts=False,
                    premium_subscription=False,
                    gifts_from_channels=False,
                ),
            )
        if isinstance(method, GetMe):
            return User(id=42, is_bot=True, first_name="SAQAL", username=BOT_USERNAME)
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

    def __init__(self, shop, db, admin_chat_id: int = ADMIN_CHAT_ID) -> None:
        self.session = FakeSession()
        self.bot = Bot("42:TEST", session=self.session)
        self.settings = Settings(bot_token="42:TEST", admin_chat_id=admin_chat_id, db_path=db.path)
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

    def _message(self, uid: int, chat_id: int | None = None, **kwargs) -> Message:
        chat_id = uid if chat_id is None else chat_id
        return Message(
            message_id=next(self._msg_ids),
            date=datetime.now(UTC),
            chat=Chat(id=chat_id, type="private" if chat_id > 0 else "supergroup"),
            from_user=self.user(uid),
            **kwargs,
        )

    async def text(self, uid: int, text: str, chat_id: int | None = None):
        """Сообщение от uid; chat_id < 0 — группа, другой положительный — чужой личный чат."""
        return await self._feed(message=self._message(uid, chat_id, text=text))

    async def contact(self, uid: int, phone: str, owner_id: int | None, forwarded: bool = False):
        extra = {}
        if forwarded:
            extra["forward_origin"] = MessageOriginUser(
                date=datetime.now(UTC), sender_user=self.user(uid)
            )
        contact = Contact(phone_number=phone, first_name="X", user_id=owner_id)
        return await self._feed(message=self._message(uid, contact=contact, **extra))

    async def together(self, *coros) -> list[TelegramMethod]:
        """Подать несколько апдейтов параллельно; вернуть все запросы бота за это время."""
        before = len(self.session.requests)
        await asyncio.gather(*coros)
        return self.session.requests[before:]

    async def press(self, uid: int, data: str, chat_id: int | None = None):
        message = self._message(uid, chat_id, text="…")
        cb = CallbackQuery(
            id=str(next(self._update_ids)),
            from_user=self.user(uid),
            chat_instance="ci",
            data=data,
            message=message,
        )
        return await self._feed(callback_query=cb)


async def after_yields(n: int, coro):
    """Запустить корутину после n переключений цикла — разные порядки гонки."""
    for _ in range(n):
        await asyncio.sleep(0)
    return await coro


# Сдвиги между двумя параллельными апдейтами: покрывают все порядки, найденные на Ф1.
RACE_OFFSETS = [(0, 0), (0, 1), (1, 0), (0, 2), (2, 0), (1, 1), (0, 3), (3, 0), (0, 5), (5, 0)]


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
