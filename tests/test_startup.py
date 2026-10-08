"""Ф5: проверки при запуске — критерии 5.2–5.4."""

import logging

import pytest
from aiogram.exceptions import TelegramBadRequest, TelegramNetworkError

from saqal import app
from saqal.db import Database
from saqal.startup import StartupError, check_admin_chat, check_storage, ensure_db_dir
from tests.conftest import ADMIN_CHAT_ID, Harness, texts_of
from tests.test_flow import ALICE, T1_IVAN, book_new

RAILWAY = {"RAILWAY_ENVIRONMENT": "production", "RAILWAY_SERVICE_ID": "svc"}


# --- 5.2 ---


def test_railway_without_volume_refuses_to_start():
    with pytest.raises(StartupError, match="не подключён том"):
        check_storage("/data/saqal.db", RAILWAY)


@pytest.mark.parametrize(
    "db_path",
    [
        "/app/saqal.db",  # рядом с кодом — сотрётся при деплое
        "saqal.db",  # относительный путь — от рабочего каталога, не на томе
        "/data2/saqal.db",  # похожее имя, но другой каталог
        "/data/../app/saqal.db",  # выход из тома через ..
    ],
)
def test_railway_db_outside_volume_refuses_to_start(db_path):
    with pytest.raises(StartupError, match="вне тома /data"):
        check_storage(db_path, {**RAILWAY, "RAILWAY_VOLUME_MOUNT_PATH": "/data"})


@pytest.mark.parametrize("db_path", ["/data/saqal.db", "/data/db/saqal.db"])
def test_railway_db_on_volume_is_ok(db_path):
    check_storage(db_path, {**RAILWAY, "RAILWAY_VOLUME_MOUNT_PATH": "/data"})


@pytest.mark.parametrize("db_path", ["saqal.db", "/tmp/x/saqal.db"])
def test_check_is_off_outside_railway(db_path):
    check_storage(db_path, {})
    check_storage(db_path, {"RAILWAY_VOLUME_MOUNT_PATH": "/data"})  # без признаков Railway


async def test_run_exits_with_clear_reason_in_log(monkeypatch, tmp_path, caplog):
    for name, value in {
        "BOT_TOKEN": "42:TEST",
        "ADMIN_CHAT_ID": "-100500",
        "DB_PATH": str(tmp_path / "saqal.db"),
        **RAILWAY,
    }.items():
        monkeypatch.setenv(name, value)
    monkeypatch.delenv("RAILWAY_VOLUME_MOUNT_PATH", raising=False)
    caplog.set_level(logging.ERROR)
    with pytest.raises(SystemExit) as exc:
        await app.run()
    assert exc.value.code == 1
    assert "Бот не запущен" in caplog.text
    assert "не подключён том" in caplog.text
    assert not (tmp_path / "saqal.db").exists()


# --- 5.3 ---


def test_missing_db_directory_is_created(tmp_path):
    db_path = tmp_path / "volume" / "nested" / "saqal.db"
    ensure_db_dir(str(db_path))
    assert db_path.parent.is_dir()
    Database(str(db_path))  # база открывается в созданном каталоге
    assert db_path.exists()
    ensure_db_dir(str(db_path))  # повторный запуск — без ошибки


# --- 5.4 ---


async def test_admin_chat_reachable(tg, caplog):
    caplog.set_level(logging.INFO)
    assert await check_admin_chat(tg.bot, ADMIN_CHAT_ID) is True
    assert not [r for r in caplog.records if r.levelno >= logging.ERROR]


@pytest.mark.parametrize(
    "error",
    [
        lambda m: TelegramBadRequest(m, "chat not found"),
        lambda m: TelegramNetworkError(m, "connection reset"),
    ],
)
async def test_unreachable_admin_chat_logs_error_and_booking_still_works(
    shop, db, frozen_now, caplog, error
):
    tg = Harness(shop, db)
    tg.session.fail_for[ADMIN_CHAT_ID] = error
    caplog.set_level(logging.INFO)
    wd = tg.dp.workflow_data
    await tg.dp.emit_startup(dispatcher=tg.dp, bot=tg.bot, bots=[tg.bot], **wd)
    try:
        errors = [r.getMessage() for r in caplog.records if r.levelno >= logging.ERROR]
        assert len(errors) == 1
        assert f"ADMIN_CHAT_ID={ADMIN_CHAT_ID} недоступен боту" in errors[0]
        assert "Запись клиентов работает" in errors[0]
        # Бот запущен, клиент записывается и получает Т1.
        r = await book_new(tg, ALICE)
        assert texts_of(r, ALICE)[-1] == T1_IVAN
    finally:
        await tg.dp.emit_shutdown(dispatcher=tg.dp, bot=tg.bot, bots=[tg.bot], **wd)
