"""Сборка диспетчера и запуск long polling (А1)."""

import asyncio
import logging

from aiogram import Bot, Dispatcher
from aiogram.fsm.storage.memory import MemoryStorage, SimpleEventIsolation

from .admin import build_admin_router
from .adminka import build_adminka_router
from .config import Settings, Shop, load_shop
from .db import Database
from .handlers import build_routers
from .reminders import ReminderService
from .startup import StartupError, check_admin_chat, check_storage, ensure_db_dir


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
    # Команды владельца — первыми: в личном чате владельца остальное уходит в сценарий клиента.
    # Админка — до сценария клиента: на шаге имени «⚙️ Админка» не должна стать именем.
    dp.include_routers(build_admin_router(), build_adminka_router(), *build_routers())
    dp.startup.register(check_admin_chat_on_start)
    dp.startup.register(start_reminders)
    dp.shutdown.register(stop_reminders)
    return dp


async def check_admin_chat_on_start(bot: Bot, settings: Settings) -> None:
    """5.4: недоступный чат администратора не мешает запуску."""
    await check_admin_chat(bot, settings.admin_chat_id)


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
    log = logging.getLogger("saqal")
    try:
        check_storage(settings.db_path)  # 5.2
    except StartupError as e:
        log.error("Бот не запущен: %s", e)
        raise SystemExit(1) from None
    ensure_db_dir(settings.db_path)  # 5.3
    if not settings.admin_ids:
        log.warning(
            "ADMIN_IDS не задан: админка выключена. Чтобы включить, задайте Telegram ID "
            "администраторов через запятую и перезапустите бота."
        )
    dp = build_dispatcher(load_shop(), Database(settings.db_path), settings)
    bot = Bot(settings.bot_token)
    await bot.delete_webhook(drop_pending_updates=False)
    await dp.start_polling(bot, allowed_updates=dp.resolve_used_update_types())


def main() -> None:
    asyncio.run(run())
