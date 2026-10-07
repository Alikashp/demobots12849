"""Сборка диспетчера и запуск long polling (А1)."""

import asyncio
import logging

from aiogram import Bot, Dispatcher
from aiogram.fsm.storage.memory import MemoryStorage

from .config import Settings, Shop, load_shop
from .db import Database
from .handlers import build_routers


def build_dispatcher(shop: Shop, db: Database, settings: Settings) -> Dispatcher:
    # А7: состояние незавершённой записи — только в памяти.
    dp = Dispatcher(storage=MemoryStorage(), shop=shop, db=db, settings=settings)
    dp.include_routers(*build_routers())
    return dp


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
