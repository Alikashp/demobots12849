"""Конфиг барбершопа (§2) и переменные окружения (А8, Д7)."""

import os
import tomllib
from dataclasses import dataclass
from datetime import time
from pathlib import Path

SHOP_FILE = Path(__file__).resolve().parent.parent / "shop.toml"


@dataclass(frozen=True)
class Service:
    id: str
    title: str
    minutes: int
    price: str


@dataclass(frozen=True)
class Master:
    id: str
    name: str


@dataclass(frozen=True)
class Contact:
    name: str
    phone: str


@dataclass(frozen=True)
class Shop:
    name: str
    kind: str
    address: str
    open: time
    close: time
    slot_step_minutes: int
    days_ahead: int
    masters: tuple[Master, ...]
    services: tuple[Service, ...]
    contacts: tuple[Contact, ...] = ()

    def service(self, service_id: str) -> Service | None:
        return next((s for s in self.services if s.id == service_id), None)

    def master(self, master_id: str) -> Master | None:
        return next((m for m in self.masters if m.id == master_id), None)


def load_shop(path: Path = SHOP_FILE) -> Shop:
    with path.open("rb") as f:
        raw = tomllib.load(f)
    return Shop(
        name=raw["name"],
        kind=raw["kind"],
        address=raw["address"],
        open=time.fromisoformat(raw["open"]),
        close=time.fromisoformat(raw["close"]),
        slot_step_minutes=int(raw["slot_step_minutes"]),
        days_ahead=int(raw["days_ahead"]),
        masters=tuple(Master(id=m["id"], name=m["name"]) for m in raw["masters"]),
        services=tuple(
            Service(id=s["id"], title=s["title"], minutes=int(s["minutes"]), price=s["price"])
            for s in raw["services"]
        ),
        contacts=tuple(Contact(name=c["name"], phone=c["phone"]) for c in raw.get("contacts", [])),
    )


@dataclass(frozen=True)
class Settings:
    bot_token: str
    admin_chat_id: int
    db_path: str

    @classmethod
    def from_env(cls) -> "Settings":
        missing = [k for k in ("BOT_TOKEN", "ADMIN_CHAT_ID", "DB_PATH") if not os.environ.get(k)]
        if missing:
            raise SystemExit(f"Не заданы переменные окружения: {', '.join(missing)}")
        return cls(
            bot_token=os.environ["BOT_TOKEN"],
            admin_chat_id=int(os.environ["ADMIN_CHAT_ID"]),
            db_path=os.environ["DB_PATH"],
        )
