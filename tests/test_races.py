"""2.10: повторное и параллельное нажатие кнопки времени (замечание к Ф1)."""

from datetime import datetime

import pytest

from saqal import texts
from saqal.clock import TZ
from tests.conftest import ADMIN_CHAT_ID, RACE_OFFSETS, after_yields, sent, texts_of
from tests.test_flow import ALICE, PHONE, T1_IVAN, T4_IVAN, book_new, go_to_time

T1_15 = T1_IVAN.replace("в 12:00", "в 15:00")
DAY_START = datetime(2026, 10, 9, 0, 0, tzinfo=TZ)


def t1s(requests) -> list[str]:
    return [t for t in texts_of(requests, ALICE) if t.startswith("🤜🤛")]


def assert_one_booking(requests, db, expected_t1: str | None = None) -> None:
    t1 = t1s(requests)
    assert len(t1) == 1
    if expected_t1:
        assert t1 == [expected_t1]
    assert len(texts_of(requests, ADMIN_CHAT_ID)) == 1
    assert texts.SLOT_TAKEN not in texts_of(requests, ALICE)


@pytest.mark.parametrize("offsets", RACE_OFFSETS)
@pytest.mark.parametrize("master", ["umar", "any"])
async def test_parallel_time_press_returning_client(tg, db, offsets, master):
    await book_new(tg, ALICE)  # Алиса — вернувшийся клиент
    await go_to_time(tg, ALICE, master=master)
    r = await tg.together(
        after_yields(offsets[0], tg.press(ALICE, "tm:202610091500")),
        after_yields(offsets[1], tg.press(ALICE, "tm:202610091500")),
    )
    assert_one_booking(r, db)
    assert t1s(r)[0].startswith(T1_15.split(" к мастеру")[0])
    assert len(db.upcoming_bookings(ALICE, DAY_START)) == 2  # 12:00 из book_new и 15:00


@pytest.mark.parametrize("offsets", RACE_OFFSETS)
async def test_parallel_time_press_new_client(tg, db, offsets):
    await go_to_time(tg, ALICE)
    r = await tg.together(
        after_yields(offsets[0], tg.press(ALICE, "tm:202610091200")),
        after_yields(offsets[1], tg.press(ALICE, "tm:202610091200")),
    )
    assert texts_of(r, ALICE) == [texts.ASK_NAME]
    await tg.text(ALICE, "Иван")
    r = await tg.contact(ALICE, PHONE, owner_id=ALICE)
    assert texts_of(r, ALICE) == [texts.PHONE_RECEIVED, T1_IVAN]
    assert texts_of(r, ADMIN_CHAT_ID) == [T4_IVAN]
    assert len(db.upcoming_bookings(ALICE, DAY_START)) == 1


async def test_sequential_repeat_time_press_returning_client(tg, db):
    await book_new(tg, ALICE)
    await go_to_time(tg, ALICE)
    r1 = await tg.press(ALICE, "tm:202610091500")
    r2 = await tg.press(ALICE, "tm:202610091500")
    assert_one_booking(r1 + r2, db, T1_15)
    assert sent(r2) == []  # только всплывающая подсказка ALREADY_BOOKED
    assert len(db.upcoming_bookings(ALICE, DAY_START)) == 2


async def test_sequential_repeat_time_press_during_name_and_phone(tg, db):
    await go_to_time(tg, ALICE)
    await tg.press(ALICE, "tm:202610091200")
    assert sent(await tg.press(ALICE, "tm:202610091200")) == []  # шаг имени не сброшен
    await tg.text(ALICE, "Иван")
    assert sent(await tg.press(ALICE, "tm:202610091200")) == []  # шаг телефона не сброшен
    r = await tg.contact(ALICE, PHONE, owner_id=ALICE)
    assert texts_of(r, ALICE) == [texts.PHONE_RECEIVED, T1_IVAN]
