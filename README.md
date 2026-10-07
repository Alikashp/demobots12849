# SAQAL — демо-бот записи в барбершоп

Telegram-бот записи клиентов. Требования — [docs/01-requirements.md](docs/01-requirements.md),
архитектура и план — [docs/02-architecture-plan.md](docs/02-architecture-plan.md),
история — [CHANGELOG.md](CHANGELOG.md).

## Запуск локально

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements-dev.txt
export BOT_TOKEN=...        # токен от @BotFather
export ADMIN_CHAT_ID=...    # ID чата администратора
export DB_PATH=./saqal.db   # файл SQLite
.venv/bin/python -m saqal
```

Данные барбершопа (мастера, услуги, часы работы) — в [shop.toml](shop.toml).

## Проверки

```bash
.venv/bin/ruff check . && .venv/bin/ruff format --check .
.venv/bin/pytest
```
