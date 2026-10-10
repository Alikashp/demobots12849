"""Ф7: график мастеров — критерии 7.4–7.8."""

import sqlite3
from datetime import UTC, date, datetime, time, timedelta

import pytest

from saqal import clock, slots, texts
from saqal.clock import TZ
from saqal.db import Database
from tests.conftest import Harness, buttons, sent, texts_of
from tests.test_flow import ALICE, book_new, go_to_time
from tests.test_timezone import server_in_utc  # noqa: F401  (фикстура)

BOSS = 700
FRI = date(2026, 10, 9)  # «сегодня» в тестах; пятница
MON = date(2026, 10, 12)
BEFORE_OPEN = datetime(2026, 10, 9, 8, 0, tzinfo=TZ)
ALL = ["umar", "barber2", "barber3"]


def at(d: date, hh: int, mm: int = 0) -> datetime:
    return datetime(d.year, d.month, d.day, hh, mm, tzinfo=TZ)


def hhmm(starts) -> list[str]:
    return [s.astimezone(TZ).strftime("%H:%M") for s in starts]


def busy(master: str, start: datetime, minutes: int) -> slots.Busy:
    return slots.Busy(master, start, start + timedelta(minutes=minutes))


@pytest.fixture
def tg(shop, db, frozen_now):
    return Harness(shop, db, admin_ids=frozenset({BOSS}))


# --- 7.4 ---


@pytest.mark.parametrize(
    ("service_id", "first", "last"),
    [("men", "12:00", "17:00"), ("dad_son", "12:00", "16:30"), ("clipper", "12:00", "17:30")],
)
def test_slots_fit_into_master_hours(shop, service_id, first, last):
    schedule = slots.Schedule(weekly={"umar": {FRI.weekday(): (time(12), time(18))}})
    starts = hhmm(
        slots.available_starts(
            shop, shop.service(service_id), ["umar"], FRI, [], BEFORE_OPEN, schedule
        )
    )
    assert starts[0] == first
    assert starts[-1] == last


def test_weekly_day_off_and_date_off_hide_master(shop):
    service = shop.service("men")
    weekly_off = slots.Schedule(weekly={"umar": {FRI.weekday(): None}})
    assert slots.available_starts(shop, service, ["umar"], FRI, [], BEFORE_OPEN, weekly_off) == []
    date_off = slots.Schedule(days_off=frozenset({("umar", FRI)}))
    assert slots.available_starts(shop, service, ["umar"], FRI, [], BEFORE_OPEN, date_off) == []
    # На другие дни выходной на дату не влияет.
    assert slots.available_starts(shop, service, ["umar"], MON, [], BEFORE_OPEN, date_off)


def test_any_master_counts_only_working_masters(shop):
    service = shop.service("men")
    # Умар не работает, двое других заняты в 12:00 — слот 12:00 не показан.
    schedule = slots.Schedule(days_off=frozenset({("umar", FRI)}))
    taken = [busy("barber2", at(FRI, 12), 60), busy("barber3", at(FRI, 12), 60)]
    starts = hhmm(slots.available_starts(shop, service, ALL, FRI, taken, BEFORE_OPEN, schedule))
    assert "12:00" not in starts
    assert "13:00" in starts
    # Наименее загруженный — Умар (0 минут), но он не работает: назначается Барбер 3.
    loaded = [busy("barber2", at(FRI, 10), 90), busy("barber3", at(FRI, 10), 30)]
    assert slots.pick_master(service, at(FRI, 15), ALL, loaded, schedule) == "barber3"
    assert slots.pick_master(service, at(FRI, 15), ALL, loaded) == "umar"  # без графика


def test_day_without_working_time_is_hidden(shop):
    off_all = slots.Schedule(days_off=frozenset((m, MON) for m in ALL))
    days = slots.available_days(shop, shop.service("men"), ALL, [], BEFORE_OPEN, off_all)
    assert MON not in days
    assert FRI in days
    # Часы, в которые услуга не помещается (10:00–10:30 для 60 минут), — тоже нет времени.
    short = slots.Schedule(weekly={"umar": {MON.weekday(): (time(10), time(10, 30))}})
    days = slots.available_days(shop, shop.service("men"), ["umar"], [], BEFORE_OPEN, short)
    assert MON not in days


async def test_client_sees_only_working_hours(tg, db, shop):
    db.set_weekday_hours(shop, "umar", FRI.weekday(), (time(12), time(18)))
    [msg] = sent(await go_to_time(tg, ALICE))  # Умар, 9 октября, мужская стрижка
    times = [t for t, _ in buttons(msg) if t[0].isdigit()]
    assert times[0] == "12:00" and times[-1] == "17:00"
    db.toggle_day_off("umar", FRI)
    await tg.text(ALICE, "/start")
    await tg.press(ALICE, "book")
    await tg.press(ALICE, "svc:men")
    [days] = sent(await tg.press(ALICE, "mst:umar"))
    assert ("9 октября (пт)", "day:20261009") not in buttons(days)


# --- 7.5 ---


async def test_schedule_changed_while_choosing_time(tg, db, shop):
    await book_new(tg, ALICE)  # Алиса — вернувшийся клиент
    await go_to_time(tg, ALICE, day="20261012")  # видит 19:00 у Умара в понедельник
    db.set_weekday_hours(shop, "umar", MON.weekday(), (time(10), time(18)))  # меняет админ
    r = await tg.press(ALICE, "tm:202610121900")
    msgs = sent(r, ALICE)
    assert [m.text for m in msgs] == [texts.SLOT_TAKEN, texts.choose_time(MON)]
    assert ("19:00", "tm:202610121900") not in buttons(msgs[1])
    assert db.active_booking_at(ALICE, at(MON, 19)) is None


def test_create_booking_rechecks_schedule_in_transaction(db, shop):
    db.set_weekday_hours(shop, "umar", MON.weekday(), None)
    made = db.create_booking(
        shop=shop,
        user_id=ALICE,
        service=shop.service("men"),
        master_ids=["umar"],
        start=at(MON, 12),
        now=BEFORE_OPEN,
        new_client=("Иван", "+79001234567"),
    )
    assert made is None
    assert db.get_client(ALICE) is None


# --- 7.6 ---


def reminder_statuses(db, booking_id):
    conn = sqlite3.connect(db.path)
    try:
        return [
            r[0]
            for r in conn.execute(
                "SELECT status FROM reminders WHERE booking_id = ?", (booking_id,)
            )
        ]
    finally:
        conn.close()


@pytest.mark.parametrize(
    "change",
    ["adm:wse:umar:0:1800", "adm:wss:umar:0:1930", "adm:wdo:umar:0", "adm:oft:umar:20261012"],
)
async def test_bookings_outside_new_schedule_are_shown_not_cancelled(tg, db, change):
    await book_new(tg, ALICE, time="202610121900", day="20261012")  # пн 19:00, Умар
    [b] = db.upcoming_bookings(ALICE, BEFORE_OPEN)
    r = await tg.press(BOSS, change)
    warning = [m for m in sent(r, BOSS) if m.text.startswith("⚠️")]
    assert len(warning) == 1
    assert "12 октября (пн) 19:00 — Мужская стрижка — Иван, +79001234567" in warning[0].text
    assert (texts.btn_conflict_cancel(b.start, "Иван"), f"adm:cx:{b.id}") in buttons(warning[0])
    # Запись не отменена, напоминания ждут своего срока.
    assert db.active_booking_at(ALICE, at(MON, 19)) is not None
    assert reminder_statuses(db, b.id) == ["pending", "pending"]
    # Отмена — как в АЗ2: клиент получает Т6.
    r = await tg.press(BOSS, f"adm:cxy:{b.id}")
    assert texts_of(r, ALICE)[0].startswith("😔 Иван, ваша запись")


async def test_no_warning_when_bookings_still_fit(tg, db):
    await book_new(tg, ALICE, time="202610121200", day="20261012")
    r = await tg.press(BOSS, "adm:wse:umar:0:1800")
    assert not [m for m in sent(r, BOSS) if m.text.startswith("⚠️")]


# --- 7.7 ---


async def test_schedule_survives_restart(shop, db, frozen_now):
    db.set_weekday_hours(shop, "umar", FRI.weekday(), (time(12), time(18)))
    db.toggle_day_off("barber2", FRI)
    reopened = Database(db.path)  # перезапуск: новое подключение к тому же файлу
    schedule = reopened.schedule()
    assert schedule.weekly["umar"][FRI.weekday()] == (time(12), time(18))
    assert schedule.weekly["umar"][MON.weekday()] == (shop.open, shop.close)
    assert ("barber2", FRI) in schedule.days_off
    tg = Harness(shop, reopened)
    [msg] = sent(await go_to_time(tg, ALICE))
    assert [t for t, _ in buttons(msg) if t[0].isdigit()][0] == "12:00"


def test_without_saved_schedule_everything_as_before(db, shop):
    assert db.schedule() == slots.FULL_TIME
    service = shop.service("dad_son")
    with_db = slots.available_starts(shop, service, ALL, FRI, [], BEFORE_OPEN, db.schedule())
    plain = slots.available_starts(shop, service, ALL, FRI, [], BEFORE_OPEN)
    assert with_db == plain
    assert hhmm(plain)[0] == "10:00" and hhmm(plain)[-1] == "19:30"


# --- 7.8 ---


def test_weekday_and_hours_by_kazan_time(server_in_utc, shop):  # noqa: F811
    # 8 окт 21:30 UTC — четверг по UTC, но по Казани уже пятница 9 октября 00:30.
    now = datetime(2026, 10, 8, 21, 30, tzinfo=UTC)
    assert slots.booking_days(shop, now)[0] == FRI
    friday_off = slots.Schedule(weekly={"umar": {FRI.weekday(): None}})
    days = slots.available_days(shop, shop.service("men"), ["umar"], [], now, friday_off)
    assert FRI not in days
    thursday_off = slots.Schedule(weekly={"umar": {3: None}})
    days = slots.available_days(shop, shop.service("men"), ["umar"], [], now, thursday_off)
    assert days[0] == FRI
    # Часы 12:00–18:00 по Казани = 09:00–15:00 UTC.
    hours = slots.Schedule(weekly={"umar": {FRI.weekday(): (time(12), time(18))}})
    starts = slots.available_starts(shop, shop.service("men"), ["umar"], FRI, [], now, hours)
    assert starts[0] == datetime(2026, 10, 9, 9, 0, tzinfo=UTC)
    assert starts[-1] == datetime(2026, 10, 9, 14, 0, tzinfo=UTC)


async def test_date_off_by_kazan_date(tg, db, shop, frozen_now, server_in_utc):  # noqa: F811
    frozen_now(datetime(2026, 10, 9, 0, 30, tzinfo=TZ))  # 8 окт 21:30 UTC
    for master in ALL:
        db.toggle_day_off(master, FRI)  # выходной на 9 октября по Казани
    await tg.text(ALICE, "/start")
    await tg.press(ALICE, "book")
    await tg.press(ALICE, "svc:men")
    [days] = sent(await tg.press(ALICE, "mst:any"))
    assert buttons(days)[0] == ("10 октября (сб)", "day:20261010")
    assert clock.now().astimezone(UTC).date() == date(2026, 10, 8)
