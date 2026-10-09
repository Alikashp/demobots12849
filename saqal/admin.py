"""Команды владельца: /today и /broadcast (В2–В4). Работают только в чате администратора."""

import asyncio
import logging
from contextlib import suppress

from aiogram import Bot, F, Router
from aiogram.exceptions import TelegramAPIError, TelegramBadRequest, TelegramRetryAfter
from aiogram.filters import Command, CommandObject
from aiogram.types import CallbackQuery, Message, TelegramObject

from . import clock, keyboards, slots, texts
from .clock import TZ
from .config import Settings, Shop
from .db import Database

log = logging.getLogger(__name__)

SEND_PAUSE = 0.05  # ~20 сообщений в секунду: ниже лимита Telegram на рассылку
MESSAGE_LIMIT = 4096


def in_admin_chat(event: TelegramObject, settings: Settings) -> bool:
    """В2, АА3: чат ADMIN_CHAT_ID (группа или личный чат владельца) или личный чат
    администратора из ADMIN_IDS. Роль проверяется на каждом апдейте (А13)."""
    if isinstance(event, CallbackQuery):
        chat = event.message.chat if event.message else None
    else:
        chat = event.chat
    if chat is None:
        return False
    if chat.id == settings.admin_chat_id:
        return True
    user = event.from_user
    return chat.type == "private" and user is not None and settings.is_admin(user.id)


# --- /today (В4) ---


def today_text(shop: Shop, db: Database) -> str:
    today = clock.now().astimezone(TZ).date()
    bookings = db.day_bookings(*slots.local_day_bounds(today))
    master_ids = [m.id for m in shop.masters]
    # Записи мастеров, которых уже нет в конфиге, тоже показываем — в конце.
    extra = [mid for mid in dict.fromkeys(b.master_id for b in bookings) if mid not in master_ids]
    blocks = []
    for mid in master_ids + extra:
        master = shop.master(mid)
        lines = [master.name if master else mid]
        own = [b for b in bookings if b.master_id == mid]
        for b in own:
            service = shop.service(b.service_id)
            title = service.title if service else b.service_id
            lines.append(texts.today_line(b.start, title, b.client.name, b.client.phone))
        if not own:
            lines.append(texts.TODAY_NO_BOOKINGS)
        blocks.append("\n".join(lines))
    return texts.today_header(today) + "\n\n" + "\n\n".join(blocks)


def split_message(text: str, limit: int = MESSAGE_LIMIT) -> list[str]:
    """Разбить длинный текст по строкам на части не длиннее лимита Telegram."""
    parts, current = [], ""
    for line in text.split("\n"):
        candidate = f"{current}\n{line}" if current else line
        if len(candidate) > limit and current:
            parts.append(current)
            candidate = line
        current = candidate
    parts.append(current)
    return parts


async def on_today(message: Message, shop: Shop, db: Database) -> None:
    for part in split_message(today_text(shop, db)):
        await message.answer(part)


# --- /broadcast (В3) ---


async def on_broadcast(message: Message, command: CommandObject, db: Database) -> None:
    text = (command.args or "").strip()
    if not text:
        await message.answer(texts.BROADCAST_HINT)
        return
    broadcast_id = db.create_broadcast(text, clock.now())
    recipients = len(db.broadcast_recipients())
    log.info("broadcast %s drafted: recipients=%s", broadcast_id, recipients)
    await message.answer(texts.broadcast_preview_header(recipients))
    # Текст — ровно как у клиентов: тот же текст без разметки, кнопки только у владельца.
    await message.answer(text, reply_markup=keyboards.broadcast_preview(broadcast_id))


def callback_id(callback: CallbackQuery) -> int:
    return int(callback.data.split(":", 1)[1])


async def reply(callback: CallbackQuery, bot: Bot, text: str) -> None:
    await bot.send_message(callback.message.chat.id, text)


async def answer_status(callback: CallbackQuery, db: Database, bot: Bot, broadcast_id: int):
    """Повторное нажатие или кнопка старого предпросмотра — честный ответ (4.7)."""
    found = db.broadcast(broadcast_id)
    status = found[1] if found else None
    text = {
        "sent": texts.BROADCAST_ALREADY_SENT,
        "cancelled": texts.BROADCAST_ALREADY_CANCELLED,
        "sending": texts.BROADCAST_IN_PROGRESS,
    }.get(status, texts.BROADCAST_UNKNOWN)
    await reply(callback, bot, text)


async def drop_buttons(callback: CallbackQuery, bot: Bot) -> None:
    with suppress(TelegramBadRequest):
        await bot.edit_message_reply_markup(
            chat_id=callback.message.chat.id,
            message_id=callback.message.message_id,
            reply_markup=None,
        )


async def on_broadcast_cancel(callback: CallbackQuery, db: Database, bot: Bot) -> None:
    await callback.answer()
    broadcast_id = callback_id(callback)
    if not db.cancel_broadcast(broadcast_id):
        await answer_status(callback, db, bot, broadcast_id)
        return
    log.info("broadcast %s cancelled", broadcast_id)
    await drop_buttons(callback, bot)
    await reply(callback, bot, texts.BROADCAST_CANCELLED)


async def send_one(bot: Bot, user_id: int, text: str) -> bool:
    """4.6: сбой на одном получателе — просто недоставлено."""
    for attempt in range(2):
        try:
            await bot.send_message(user_id, text)
            return True
        except TelegramRetryAfter as e:
            if attempt == 0:
                await asyncio.sleep(e.retry_after)
                continue
            return False
        except (TimeoutError, TelegramAPIError, OSError):
            return False
    return False


async def on_broadcast_send(callback: CallbackQuery, db: Database, bot: Bot) -> None:
    await callback.answer()
    broadcast_id = callback_id(callback)
    # 4.7: черновик переводится в «отправляется» ровно один раз — в базе, не в памяти.
    if not db.start_broadcast(broadcast_id):
        await answer_status(callback, db, bot, broadcast_id)
        return
    await drop_buttons(callback, bot)
    text, _ = db.broadcast(broadcast_id)
    recipients = db.broadcast_recipients()
    delivered = 0
    for i, user_id in enumerate(recipients):
        if i:
            await asyncio.sleep(SEND_PAUSE)
        delivered += await send_one(bot, user_id, text)
    db.finish_broadcast(broadcast_id, delivered, len(recipients), clock.now())
    log.info("broadcast %s sent: delivered=%s total=%s", broadcast_id, delivered, len(recipients))
    await reply(callback, bot, texts.broadcast_report(delivered, len(recipients)))


def build_admin_router() -> Router:
    router = Router(name="admin")
    router.message.filter(in_admin_chat)
    router.callback_query.filter(in_admin_chat)
    router.message.register(on_today, Command("today"))
    router.message.register(on_broadcast, Command("broadcast"))
    router.callback_query.register(
        on_broadcast_send, F.data.regexp(rf"^{keyboards.BROADCAST_SEND}:\d{{1,18}}$")
    )
    router.callback_query.register(
        on_broadcast_cancel, F.data.regexp(rf"^{keyboards.BROADCAST_CANCEL}:\d{{1,18}}$")
    )
    return router
