"""Фоновый цикл напоминаний (Н1–Н5, А6, А12).

Состояние — только в базе: строки напоминаний со сроком и отметкой. Цикл раз в
INTERVAL выбирает наступившие, отмечает их в базе и только потом отправляет.
Каждая отправка — отдельная задача: сбой или зависание одной не задерживает остальные.
"""

import asyncio
import contextlib
import logging
from collections.abc import Awaitable, Callable
from datetime import timedelta

from aiogram import Bot

from . import clock, keyboards, texts
from .config import Shop
from .db import Database, DueReminder

log = logging.getLogger(__name__)

INTERVAL = timedelta(seconds=5)  # 3.4: отправка не позже чем через 15 секунд после срока
MAX_LATE = timedelta(minutes=10)  # Н3
SEND_TIMEOUT = timedelta(seconds=30)
TEST_DELAY = timedelta(minutes=1)  # Н5


class ReminderService:
    def __init__(
        self,
        db: Database,
        shop: Shop,
        bot: Bot,
        *,
        interval: timedelta = INTERVAL,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        self.db = db
        self.shop = shop
        self.bot = bot
        self.interval = interval
        self._sleep = sleep
        self._task: asyncio.Task | None = None
        self._sends: set[asyncio.Task] = set()

    # --- Один проход ---

    async def tick(self) -> list[asyncio.Task]:
        """Отметить наступившие напоминания и запустить их отправку. Возвращает задачи отправки."""
        due = self.db.claim_due_reminders(clock.now(), MAX_LATE)
        started = []
        for reminder in due:
            task = asyncio.create_task(self._send(reminder))
            self._sends.add(task)
            task.add_done_callback(self._sends.discard)
            started.append(task)
        return started

    async def _send(self, reminder: DueReminder) -> None:
        b = reminder.booking
        try:
            service = self.shop.service(b.service_id)
            master = self.shop.master(b.master_id)
            text = texts.reminder(reminder.kind, b.client.name, service.title, master.name, b.start)
            async with asyncio.timeout(SEND_TIMEOUT.total_seconds()):
                await self.bot.send_message(
                    b.client.user_id, text, reply_markup=keyboards.reminder(b.id)
                )
            log.info("reminder %s (%s) sent: booking=%s", reminder.id, reminder.kind, b.id)
        except asyncio.CancelledError:
            raise
        except Exception as e:  # 3.7: блокировка бота, сеть, данные — не роняем цикл
            log.warning(
                "reminder %s (%s) failed: booking=%s error=%s",
                reminder.id,
                reminder.kind,
                b.id,
                type(e).__name__,
            )

    # --- Жизненный цикл (3.9) ---

    async def run(self) -> None:
        log.info("reminders loop started")
        while True:
            try:
                await self.tick()
            except asyncio.CancelledError:
                raise
            except Exception as e:  # сбой базы не останавливает цикл
                log.warning("reminders tick failed: error=%s", type(e).__name__)
            await self._sleep(self.interval.total_seconds())

    def start(self) -> None:
        if self._task is None:
            self._task = asyncio.create_task(self.run())

    async def stop(self, grace: float = 5.0) -> None:
        """Остановить цикл; уже отмеченным отправкам дать до grace секунд завершиться."""
        if self._task is not None:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task
            self._task = None
        if self._sends:
            _, pending = await asyncio.wait(set(self._sends), timeout=grace)
            for task in pending:
                task.cancel()
            for task in pending:
                with contextlib.suppress(asyncio.CancelledError):
                    await task
        log.info("reminders loop stopped")
