"""Проверки при запуске (5.2–5.4). Новых функций бота здесь нет."""

import logging
import os
from collections.abc import Mapping
from pathlib import Path

from aiogram import Bot

log = logging.getLogger(__name__)

# Переменные, которые Railway задаёт каждому сервису: по ним понятно, что мы на Railway.
RAILWAY_MARKERS = ("RAILWAY_ENVIRONMENT", "RAILWAY_ENVIRONMENT_ID", "RAILWAY_SERVICE_ID")
VOLUME_VAR = "RAILWAY_VOLUME_MOUNT_PATH"


class StartupError(Exception):
    """Запускаться нельзя; текст — понятная причина для лога."""


def on_railway(env: Mapping[str, str]) -> bool:
    return any(env.get(name) for name in RAILWAY_MARKERS)


def check_storage(db_path: str, env: Mapping[str, str] = os.environ) -> None:
    """5.2, А2: на Railway база должна лежать на томе, иначе она сотрётся при деплое."""
    if not on_railway(env):
        return
    mount = env.get(VOLUME_VAR)
    if not mount:
        raise StartupError(
            "К сервису на Railway не подключён том (нет переменной RAILWAY_VOLUME_MOUNT_PATH). "
            "Без тома база стирается при каждом деплое. Подключите том к сервису "
            "(путь монтирования, например, /data) и задайте DB_PATH внутри него: /data/saqal.db."
        )
    db = Path(db_path).resolve()
    volume = Path(mount).resolve()
    if not db.is_relative_to(volume):
        raise StartupError(
            f"DB_PATH={db_path} лежит вне тома {mount} (RAILWAY_VOLUME_MOUNT_PATH). "
            "Такая база стирается при каждом деплое. Задайте DB_PATH внутри тома, "
            f"например: {volume / 'saqal.db'}."
        )


def ensure_db_dir(db_path: str) -> None:
    """5.3: каталог для файла базы создаётся, если его нет."""
    Path(db_path).resolve().parent.mkdir(parents=True, exist_ok=True)


async def check_admin_chat(bot: Bot, admin_chat_id: int) -> bool:
    """5.4: чат администратора доступен боту. Если нет — ошибка в логе, бот работает дальше."""
    try:
        await bot.get_chat(admin_chat_id)
    except Exception as e:  # любая причина: чат не найден, бот не в группе, сеть
        log.error(
            "Чат администратора ADMIN_CHAT_ID=%s недоступен боту (%s: %s). "
            "Запись клиентов работает, но уведомления о записях и отменах не дойдут, "
            "а /today и /broadcast не ответят. Проверьте ADMIN_CHAT_ID и что бот добавлен "
            "в этот чат (или что владелец написал боту /start в личном чате).",
            admin_chat_id,
            type(e).__name__,
            e,
        )
        return False
    log.info("admin chat %s is reachable", admin_chat_id)
    return True
