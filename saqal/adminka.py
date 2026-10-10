"""Админка в личном чате администратора: «Записи», отмена и «Расписание» (АА2–АА4, АЗ, АР).

Каждое действие проверяет роль заново (А13): фильтр роутера смотрит ADMIN_IDS на каждом
апдейте. Кнопки админки у не-администратора не совпадают ни с одним обработчиком этого
роутера и уходят в ответ «кнопка устарела» (К9).
"""

import logging
from datetime import date, datetime, time, timedelta

from aiogram import Bot, F, Router
from aiogram.exceptions import TelegramAPIError
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message

from . import clock, keyboards, slots, texts
from .admin import split_message
from .config import Settings, Shop
from .db import Booking, CancelCheck, Database
from .handlers import describe, reset, show, stale

log = logging.getLogger(__name__)

ADMIN = "adm"
DAYS = f"{ADMIN}:days"
DAY = f"{ADMIN}:d"
CANCEL_ASK = f"{ADMIN}:cx"
CANCEL_YES = f"{ADMIN}:cxy"
SCHEDULE = f"{ADMIN}:sch"  # список мастеров
MASTER = f"{ADMIN}:sm"  # :мастер — график
WEEKDAY = f"{ADMIN}:wd"  # :мастер:день — экран дня недели
MAKE_OFF = f"{ADMIN}:wdo"  # :мастер:день
MAKE_WORK = f"{ADMIN}:wdw"  # :мастер:день
START_LIST = f"{ADMIN}:wsl"  # :мастер:день
START_SET = f"{ADMIN}:wss"  # :мастер:день:ЧЧММ
END_LIST = f"{ADMIN}:wel"  # :мастер:день
END_SET = f"{ADMIN}:wse"  # :мастер:день:ЧЧММ
DAYS_OFF = f"{ADMIN}:off"  # :мастер
DAY_OFF_TOGGLE = f"{ADMIN}:oft"  # :мастер:ГГГГММДД

PROBLEMS = {
    CancelCheck.NOT_FOUND: texts.ADMIN_CANCEL_NOT_FOUND,
    CancelCheck.ALREADY_CANCELLED: texts.ADMIN_CANCEL_ALREADY,
    CancelCheck.STARTED: texts.ADMIN_CANCEL_STARTED,
}


def is_admin(event: Message | CallbackQuery, settings: Settings) -> bool:
    return settings.is_admin(event.from_user.id if event.from_user else None)


def _btn(text: str, data: str) -> InlineKeyboardButton:
    return InlineKeyboardButton(text=text, callback_data=data)


def _kb(rows: list[list[InlineKeyboardButton]]) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=rows)


def to_days() -> list[InlineKeyboardButton]:
    return [_btn(texts.BTN_ADMIN_TO_DAYS, DAYS)]


def card(shop: Shop, b: Booking) -> str:
    service_title, master_name = describe(shop, b)
    return texts.admin_booking_card(
        service_title, master_name, b.start, b.client.name, b.client.phone
    )


# --- Меню (АА2) ---


async def on_admin_menu(message: Message, state: FSMContext, bot: Bot, settings: Settings):
    await reset(state, bot, message.chat.id, settings)
    # «Записать клиента» появится в Ф8.
    await message.answer(
        texts.ADMIN_MENU,
        reply_markup=_kb(
            [[_btn(texts.BTN_ADMIN_BOOKINGS, DAYS)], [_btn(texts.BTN_ADMIN_SCHEDULE, SCHEDULE)]]
        ),
    )


# --- Записи (АЗ1) ---


async def on_days(callback: CallbackQuery, shop: Shop) -> None:
    await callback.answer()
    days = slots.booking_days(shop, clock.now())
    rows = [[_btn(texts.fmt_date(d), f"{DAY}:{d.strftime(keyboards.DAY_FMT)}")] for d in days]
    await show(callback, callback.message, texts.ADMIN_CHOOSE_DAY, _kb(rows))


def day_view(shop: Shop, db: Database, day: date) -> tuple[str, InlineKeyboardMarkup]:
    now = clock.now()
    bookings = db.day_bookings(*slots.local_day_bounds(day))
    master_ids = [m.id for m in shop.masters]
    extra = [mid for mid in dict.fromkeys(b.master_id for b in bookings) if mid not in master_ids]
    blocks, buttons = [], []
    for mid in master_ids + extra:
        master = shop.master(mid)
        name = master.name if master else mid
        own = [b for b in bookings if b.master_id == mid]
        lines = [name]
        for b in own:
            service_title, _ = describe(shop, b)
            lines.append(
                texts.admin_booking_line(b.start, service_title, b.client.name, b.client.phone)
            )
            if b.start > now:  # АЗ2: отменить можно только не начавшуюся запись
                buttons.append(
                    [
                        _btn(
                            texts.btn_admin_cancel(b.start, name, b.client.name),
                            f"{CANCEL_ASK}:{b.id}",
                        )
                    ]
                )
        if not own:
            lines.append(texts.TODAY_NO_BOOKINGS)
        blocks.append("\n".join(lines))
    text = texts.admin_day_header(day) + "\n\n" + "\n\n".join(blocks)
    return text, _kb([*buttons, to_days()])


async def on_day(
    callback: CallbackQuery, shop: Shop, db: Database, bot: Bot, settings: Settings,
    state: FSMContext,
) -> None:  # fmt: skip
    day = keyboards.parse_day(callback.data.split(":", 2)[2])
    if day is None or day not in slots.booking_days(shop, clock.now()):
        await stale(callback, bot, state, settings)
        return
    await callback.answer()
    text, markup = day_view(shop, db, day)
    parts = split_message(text)
    if len(parts) == 1:
        await show(callback, callback.message, text, markup)
        return
    # 6.4: длинный список — несколькими сообщениями, кнопки под последним.
    for i, part in enumerate(parts):
        await callback.message.answer(part, reply_markup=markup if i == len(parts) - 1 else None)


# --- Отмена (АЗ2) ---


def booking_id(callback: CallbackQuery) -> int:
    return int(callback.data.rsplit(":", 1)[1])


async def on_cancel_ask(callback: CallbackQuery, shop: Shop, db: Database) -> None:
    await callback.answer()
    result, b = db.check_cancel(booking_id(callback), None, clock.now())
    if result is not CancelCheck.OK:
        await show(callback, callback.message, PROBLEMS[result], _kb([to_days()]))
        return
    day = b.start.astimezone(clock.TZ).strftime(keyboards.DAY_FMT)
    markup = _kb(
        [
            [_btn(texts.BTN_ADMIN_CANCEL_YES, f"{CANCEL_YES}:{b.id}")],
            [_btn(texts.BTN_ADMIN_CANCEL_NO, f"{DAY}:{day}")],
        ]
    )
    await show(callback, callback.message, texts.admin_confirm_cancel(card(shop, b)), markup)


async def on_cancel_yes(
    callback: CallbackQuery, shop: Shop, db: Database, bot: Bot, settings: Settings
) -> None:
    await callback.answer()
    # Та же транзакция, что и у клиента (М4): одна отмена при любом числе нажатий.
    result, b = db.cancel_booking(booking_id(callback), None, clock.now())
    if result is not CancelCheck.OK:
        await show(callback, callback.message, PROBLEMS[result], _kb([to_days()]))
        return
    log.info("booking %s cancelled by admin %s", b.id, callback.from_user.id)
    service_title, master_name = describe(shop, b)
    try:  # Т5 — в чат администратора
        await bot.send_message(
            settings.admin_chat_id,
            texts.admin_cancelled(
                b.client.name, b.client.phone, service_title, master_name, b.start
            ),
        )
    except TelegramAPIError as e:
        log.warning("admin notification failed: booking=%s error=%s", b.id, type(e).__name__)
    notified = True
    try:  # Т6 — клиенту; не доставлено — отмена всё равно в силе (6.7)
        await bot.send_message(
            b.client.user_id,
            texts.booking_cancelled_by_shop(
                shop, b.client.name, service_title, master_name, b.start
            ),
        )
    except TelegramAPIError as e:
        notified = False
        log.warning("client notification failed: booking=%s error=%s", b.id, type(e).__name__)
    await show(
        callback,
        callback.message,
        texts.admin_cancel_done(card(shop, b), notified, b.client.phone),
        _kb([to_days()]),
    )


# --- Расписание (АР1–АР4) ---


def week_of(shop: Shop, schedule: slots.Schedule, master_id: str) -> list[slots.Hours | None]:
    """Семь дней недели: сохранённый шаблон или весь режим (Д6)."""
    template = schedule.weekly.get(master_id, {})
    full = (shop.open, shop.close)
    return [template.get(wd, full) for wd in range(7)]


def grid_times(shop: Shop) -> list[time]:
    """АР1: все времена режима по сетке 30 минут, от открытия до закрытия включительно."""
    step = timedelta(minutes=shop.slot_step_minutes)
    t = datetime.combine(date.min, shop.open)
    end = datetime.combine(date.min, shop.close)
    out = []
    while t <= end:
        out.append(t.time())
        t += step
    return out


def parse_hhmm(value: str) -> time | None:
    try:
        return datetime.strptime(value, "%H%M").time()
    except ValueError:
        return None


def master_view(shop: Shop, db: Database, master_id: str) -> tuple[str, InlineKeyboardMarkup]:
    schedule = db.schedule()
    master = shop.master(master_id)
    week = week_of(shop, schedule, master_id)
    days = slots.booking_days(shop, clock.now())
    off = [d for d in days if (master_id, d) in schedule.days_off]
    rows = [
        [_btn(texts.schedule_line(wd, h), f"{WEEKDAY}:{master_id}:{wd}")]
        for wd, h in enumerate(week)
    ]
    rows.append([_btn(texts.BTN_SCHEDULE_DAYS_OFF, f"{DAYS_OFF}:{master_id}")])
    rows.append([_btn(texts.BTN_TO_MASTERS, SCHEDULE)])
    return texts.schedule_view(master.name, week, off), _kb(rows)


def weekday_screen(
    shop: Shop, db: Database, master_id: str, wd: int
) -> tuple[str, InlineKeyboardMarkup]:
    hours = week_of(shop, db.schedule(), master_id)[wd]
    key = f"{master_id}:{wd}"
    if hours is None:
        rows = [[_btn(texts.BTN_MAKE_WORKDAY, f"{MAKE_WORK}:{key}")]]
    else:
        rows = [
            [
                _btn(texts.btn_start(hours[0]), f"{START_LIST}:{key}"),
                _btn(texts.btn_end(hours[1]), f"{END_LIST}:{key}"),
            ],
            [_btn(texts.BTN_MAKE_DAY_OFF, f"{MAKE_OFF}:{key}")],
        ]
    rows.append([_btn(texts.BTN_TO_SCHEDULE, f"{MASTER}:{master_id}")])
    return texts.weekday_view(shop.master(master_id).name, wd, hours), _kb(rows)


def days_off_screen(shop: Shop, db: Database, master_id: str) -> tuple[str, InlineKeyboardMarkup]:
    schedule = db.schedule()
    rows = [
        [
            _btn(
                texts.btn_day_off(d, (master_id, d) in schedule.days_off),
                f"{DAY_OFF_TOGGLE}:{master_id}:{d.strftime(keyboards.DAY_FMT)}",
            )
        ]
        for d in slots.booking_days(shop, clock.now())
    ]
    rows.append([_btn(texts.BTN_TO_SCHEDULE, f"{MASTER}:{master_id}")])
    return texts.days_off_view(shop.master(master_id).name), _kb(rows)


async def report_conflicts(callback: CallbackQuery, shop: Shop, db: Database, master_id: str):
    """АР4, 7.6: после изменения графика — будущие записи вне его, с «Отменить» (как АЗ2)."""
    schedule = db.schedule()
    found = [
        b
        for b, interval in db.future_master_bookings(master_id, clock.now())
        if not schedule.works(interval.master_id, interval.start, interval.end)
    ]
    if not found:
        return
    lines, rows = [], []
    for b in found:
        service_title, _ = describe(shop, b)
        lines.append(texts.conflict_line(b.start, service_title, b.client.name, b.client.phone))
        rows.append(
            [_btn(texts.btn_conflict_cancel(b.start, b.client.name), f"{CANCEL_ASK}:{b.id}")]
        )
    for i, part in enumerate(split_message(texts.schedule_conflicts(lines))):
        await callback.message.answer(part, reply_markup=_kb(rows) if i == 0 else None)


def schedule_args(callback: CallbackQuery, shop: Shop) -> list[str] | None:
    """Разбор «adm:действие:мастер[:день[:значение]]» с проверкой мастера и дня (А9)."""
    parts = callback.data.split(":")[2:]
    if not parts or shop.master(parts[0]) is None:
        return None
    if len(parts) > 1 and not (parts[1].isdigit() and 0 <= int(parts[1]) <= 6):
        return None
    return parts


async def on_schedule(callback: CallbackQuery, shop: Shop) -> None:
    await callback.answer()
    rows = [[_btn(m.name, f"{MASTER}:{m.id}")] for m in shop.masters]
    await show(callback, callback.message, texts.ADMIN_SCHEDULE_MASTERS, _kb(rows))


async def on_master(callback, shop: Shop, db: Database, bot: Bot, state, settings) -> None:
    args = schedule_args(callback, shop)
    if args is None:
        await stale(callback, bot, state, settings)
        return
    await callback.answer()
    await show(callback, callback.message, *master_view(shop, db, args[0]))


async def on_weekday(callback, shop: Shop, db: Database, bot: Bot, state, settings) -> None:
    args = schedule_args(callback, shop)
    if args is None or len(args) != 2:
        await stale(callback, bot, state, settings)
        return
    await callback.answer()
    await show(callback, callback.message, *weekday_screen(shop, db, args[0], int(args[1])))


async def save_weekday(callback, shop, db, master_id, wd, hours) -> None:
    db.set_weekday_hours(shop, master_id, wd, hours)
    log.info(
        "schedule changed: master=%s weekday=%s admin=%s", master_id, wd, callback.from_user.id
    )
    await callback.answer(texts.SCHEDULE_SAVED)
    await show(callback, callback.message, *weekday_screen(shop, db, master_id, wd))
    await report_conflicts(callback, shop, db, master_id)


async def on_make_off(callback, shop: Shop, db: Database, bot: Bot, state, settings) -> None:
    args = schedule_args(callback, shop)
    if args is None or len(args) != 2:
        await stale(callback, bot, state, settings)
        return
    await save_weekday(callback, shop, db, args[0], int(args[1]), None)


async def on_make_work(callback, shop: Shop, db: Database, bot: Bot, state, settings) -> None:
    args = schedule_args(callback, shop)
    if args is None or len(args) != 2:
        await stale(callback, bot, state, settings)
        return
    await save_weekday(callback, shop, db, args[0], int(args[1]), (shop.open, shop.close))


async def on_time_list(callback, shop: Shop, db: Database, bot: Bot, state, settings) -> None:
    args = schedule_args(callback, shop)
    if args is None or len(args) != 2:
        await stale(callback, bot, state, settings)
        return
    master_id, wd = args[0], int(args[1])
    hours = week_of(shop, db.schedule(), master_id)[wd]
    if hours is None:  # день стал выходным — сначала «Сделать рабочим»
        await stale(callback, bot, state, settings)
        return
    await callback.answer()
    is_start = callback.data.startswith(START_LIST)
    if is_start:  # 7.2: начало — раньше конца
        options = [t for t in grid_times(shop) if t < hours[1]]
        prefix, title = START_SET, texts.choose_start(wd)
    else:  # конец — позже начала
        options = [t for t in grid_times(shop) if t > hours[0]]
        prefix, title = END_SET, texts.choose_end(wd)
    buttons = [
        _btn(t.strftime("%H:%M"), f"{prefix}:{master_id}:{wd}:{t.strftime('%H%M')}")
        for t in options
    ]
    rows = [buttons[i : i + 4] for i in range(0, len(buttons), 4)]
    rows.append([_btn(texts.BTN_TO_SCHEDULE, f"{WEEKDAY}:{master_id}:{wd}")])
    await show(callback, callback.message, title, _kb(rows))


async def on_time_set(callback, shop: Shop, db: Database, bot: Bot, state, settings) -> None:
    args = schedule_args(callback, shop)
    value = parse_hhmm(args[2]) if args is not None and len(args) == 3 else None
    if value is None:
        await stale(callback, bot, state, settings)
        return
    master_id, wd = args[0], int(args[1])
    hours = week_of(shop, db.schedule(), master_id)[wd]
    if hours is None:
        await stale(callback, bot, state, settings)
        return
    new = (value, hours[1]) if callback.data.startswith(START_SET) else (hours[0], value)
    # 7.2, А9: конец позже начала, всё внутри режима и по сетке — иначе не сохраняем.
    grid = grid_times(shop)
    if not (new[0] in grid and new[1] in grid and new[0] < new[1]):
        await stale(callback, bot, state, settings)
        return
    await save_weekday(callback, shop, db, master_id, wd, new)


async def on_days_off(callback, shop: Shop, db: Database, bot: Bot, state, settings) -> None:
    args = schedule_args(callback, shop)
    if args is None or len(args) != 1:
        await stale(callback, bot, state, settings)
        return
    await callback.answer()
    await show(callback, callback.message, *days_off_screen(shop, db, args[0]))


async def on_day_off_toggle(callback, shop: Shop, db: Database, bot: Bot, state, settings):
    parts = callback.data.split(":")[2:]
    day = keyboards.parse_day(parts[1]) if len(parts) == 2 else None
    if (
        shop.master(parts[0]) is None
        or day is None
        or day not in slots.booking_days(shop, clock.now())
    ):
        await stale(callback, bot, state, settings)
        return
    db.toggle_day_off(parts[0], day)
    log.info("day off toggled: master=%s day=%s admin=%s", parts[0], day, callback.from_user.id)
    await callback.answer(texts.SCHEDULE_SAVED)
    await show(callback, callback.message, *days_off_screen(shop, db, parts[0]))
    await report_conflicts(callback, shop, db, parts[0])


def build_adminka_router() -> Router:
    router = Router(name="adminka")
    router.message.filter(F.chat.type == "private", is_admin)
    router.callback_query.filter(F.message.chat.type == "private", is_admin)
    router.callback_query.filter(lambda c: isinstance(c.message, Message))
    router.message.register(on_admin_menu, F.text == texts.BTN_ADMIN)
    cb = router.callback_query.register
    cb(on_days, F.data == DAYS)
    cb(on_day, F.data.regexp(rf"^{DAY}:\d{{8}}$"))
    cb(on_cancel_ask, F.data.regexp(rf"^{CANCEL_ASK}:\d{{1,18}}$"))
    cb(on_cancel_yes, F.data.regexp(rf"^{CANCEL_YES}:\d{{1,18}}$"))
    m = r"[a-z0-9_]{1,32}"
    cb(on_schedule, F.data == SCHEDULE)
    cb(on_master, F.data.regexp(rf"^{MASTER}:{m}$"))
    cb(on_weekday, F.data.regexp(rf"^{WEEKDAY}:{m}:\d$"))
    cb(on_make_off, F.data.regexp(rf"^{MAKE_OFF}:{m}:\d$"))
    cb(on_make_work, F.data.regexp(rf"^{MAKE_WORK}:{m}:\d$"))
    cb(on_time_list, F.data.regexp(rf"^({START_LIST}|{END_LIST}):{m}:\d$"))
    cb(on_time_set, F.data.regexp(rf"^({START_SET}|{END_SET}):{m}:\d:\d{{4}}$"))
    cb(on_days_off, F.data.regexp(rf"^{DAYS_OFF}:{m}$"))
    cb(on_day_off_toggle, F.data.regexp(rf"^{DAY_OFF_TOGGLE}:{m}:\d{{8}}$"))
    return router
