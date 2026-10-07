"""Сборка диспетчера и запуск long polling (А1)."""

import asyncio
import logging

from aiogram import Bot, Dispatcher
from aiogram.fsm.storage.memory import MemoryStorage, SimpleEventIsolation

from .config import Settings, Shop, load_shop
from .db import Database
from .handlers import build_routers
from .reminders import ReminderService


def build_dispatcher(shop: Shop, db: Database, settings: Settings) -> Dispatcher:
    # А7: состояние незавершённой записи — только в памяти.
    # Апдейты одного клиента обрабатываются по очереди: двойное нажатие кнопки приходит
    # двумя апдейтами, и без очереди оба читают одно и то же состояние записи (2.10).
    # Процесс один (А1), поэтому блокировки в памяти достаточно; база страхует отдельно (А5).
    dp = Dispatcher(
        storage=MemoryStorage(),
        events_isolation=SimpleEventIsolation(),
        shop=shop,
        db=db,
        settings=settings,
    )
    dp.include_routers(*build_routers())
    dp.startup.register(start_reminders)
    dp.shutdown.register(stop_reminders)
    return dp


async def start_reminders(dispatcher: Dispatcher, bot: Bot, db: Database, shop: Shop) -> None:
    """3.9: цикл напоминаний живёт ровно столько, сколько бот."""
    service = ReminderService(db, shop, bot)
    dispatcher["reminders"] = service
    service.start()


async def stop_reminders(dispatcher: Dispatcher) -> None:
    service = dispatcher.workflow_data.pop("reminders", None)
    if service is not None:
        await service.stop()


async def run() -> None:
    settings = Settings.from_env()
    # А10: в логи пишутся только id; тексты сообщений, имена и телефоны — нет.
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s"
    )
    dp = build_dispatcher(load_shop(), Database(settings.db_path), settings)
    bot = Bot(settings.bot_token)
    await bot.delete_webhook(drop_pending_updates=False)
    await dp.start_polling(bot, allowed_updates=dp.resolve_used_update_types())


def main() -> None:
    asyncio.run(run())
