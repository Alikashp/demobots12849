"""Все тексты интерфейса (А8). Т1 и Т4 — дословно из требований (§8)."""

from datetime import date, datetime

from .clock import TZ
from .config import Master, Service, Shop

MONTHS = (
    "января", "февраля", "марта", "апреля", "мая", "июня",
    "июля", "августа", "сентября", "октября", "ноября", "декабря",
)  # fmt: skip
WEEKDAYS = ("пн", "вт", "ср", "чт", "пт", "сб", "вс")


def fmt_date(d: date) -> str:
    """«9 октября (пт)»."""
    return f"{d.day} {MONTHS[d.month - 1]} ({WEEKDAYS[d.weekday()]})"


def fmt_time(dt: datetime) -> str:
    """«14:30» по Казани."""
    return dt.astimezone(TZ).strftime("%H:%M")


# --- Кнопки ---

BTN_BOOK = "✂️ Записаться"
BTN_BACK = "⬅️ Назад"
BTN_HOME = "🏠 В начало"
BTN_ANY_MASTER = "💈 Любой мастер"
BTN_SHARE_PHONE = "📱 Поделиться номером"
BTN_MY = "📋 Мои записи"
BTN_MENU_BOOK = "✂️ Записаться на услугу"
BTN_CONTACTS = "📞 Контакты"
BTN_CANCEL_YES = "✅ Да, отменить"
BTN_CANCEL_NO = "↩️ Нет, оставить"
BTN_CANCEL_BOOKING = "❌ Отменить запись"


def btn_cancel(start: datetime) -> str:
    return f"❌ Отменить {fmt_date(start.astimezone(TZ).date())}, {fmt_time(start)}"


def btn_service(s: Service) -> str:
    return f"{s.title} · {s.price} · {s.minutes} мин"


def btn_master(m: Master) -> str:
    return m.name


# --- Шаги записи ---


def greeting(shop: Shop) -> str:
    return (
        f"Привет! 👋 Это бот записи в 💈{shop.kind} {shop.name}.\n"
        f"📍 {shop.address}\n\n"
        "Выберите услугу, мастера и удобное время — это займёт меньше минуты."
    )


CHOOSE_SERVICE = "✂️ Выберите услугу:"


def choose_master(service: Service) -> str:
    return f"«{service.title}» — отличный выбор! 👌\nК какому мастеру записать?"


CHOOSE_DATE = "📅 Выберите день:"
NO_DAYS = (
    "😔 На ближайшую неделю свободного времени нет.\nПопробуйте другого мастера или другую услугу."
)


def choose_time(d: date) -> str:
    return f"⌚ Свободное время на {fmt_date(d)}:"


NO_TIMES = "😔 На этот день свободного времени уже не осталось. Выберите другой день."
ASK_NAME = "Почти готово! ✍️ Как вас зовут? Напишите имя одним сообщением."
NAME_INVALID = "🙏 Напишите, пожалуйста, имя обычным текстом — не длиннее 50 символов."


def ask_phone(name: str) -> str:
    return (
        f"Приятно познакомиться, {name}! 🤝\n"
        f"Нажмите кнопку «{BTN_SHARE_PHONE}» внизу — так мастер сможет с вами связаться."
    )


PHONE_NOT_OWN = (
    f"🙏 Нужен ваш собственный номер. Нажмите кнопку «{BTN_SHARE_PHONE}» внизу — "
    "пересланные и чужие контакты не подходят."
)
PHONE_RECEIVED = "📱 Номер получен, спасибо!"
PHONE_USE_BUTTON = f"Номер набирать не нужно 🙂 Просто нажмите кнопку «{BTN_SHARE_PHONE}» внизу."
SLOT_TAKEN = "😔 Это время только что заняли. Выберите, пожалуйста, другое."
STALE_BUTTON = f"🙈 Эта кнопка устарела. Давайте начнём заново — нажмите «{BTN_HOME}»."
UNKNOWN_MESSAGE = "Выберите, что вас интересует 👇"


def contacts(shop: Shop) -> str:
    lines = [f"📍 Адрес: {shop.address}"]
    lines += [f"📞 {c.name}, {c.phone}" for c in shop.contacts]
    return "\n".join(lines)


RESTART = "Хорошо, начнём заново 👌"


# --- Мои записи и отмена (М1–М4) ---

ALREADY_BOOKED = "✅ Вы уже записаны на это время — подтверждение в чате."


def booking_line(service_title: str, master_name: str, start: datetime) -> str:
    when = f"{fmt_date(start.astimezone(TZ).date())} в {fmt_time(start)}"
    return f"📅 {when} — «{service_title}», мастер {master_name}"


def my_bookings(lines: list[str]) -> str:
    return "📋 Ваши записи:\n\n" + "\n".join(lines)


NO_BOOKINGS = "У вас пока нет предстоящих записей 🙂 Давайте запишемся?"


def confirm_cancel(line: str) -> str:
    return f"Точно отменить запись? 🤔\n\n{line}"


def cancelled(line: str) -> str:
    return f"Запись отменена ✅\n\n{line}\n\nБудем рады видеть вас в другой раз! 💈"


CANCEL_NOT_FOUND = "🤔 Такой записи нет. Актуальный список — в «📋 Мои записи»."
CANCEL_ALREADY = "👌 Эта запись уже отменена."
CANCEL_STARTED = "⏰ Эта запись уже началась или прошла — отменить её нельзя."


# --- /test_reminder (Н5) ---

TEST_REMINDER_SCHEDULED = "⏰ Готово! Через минуту пришлю тестовое напоминание о ближайшей записи."
TEST_REMINDER_NO_BOOKINGS = (
    "🙂 Чтобы проверить напоминание, сначала запишитесь — и сразу пробуйте снова."
)


# --- Владелец: /today и /broadcast (В2–В4). Деловой тон, без лишних эмодзи ---

TODAY_NO_BOOKINGS = "записей нет"
BTN_BROADCAST_SEND = "Отправить"
BTN_BROADCAST_CANCEL = "Отмена"
BROADCAST_HINT = (
    "Напишите текст рассылки в том же сообщении, сразу после команды. Например:\n\n"
    "/broadcast Завтра работаем до 18:00. Ждём вас!"
)
BROADCAST_CANCELLED = "Рассылка отменена, сообщения не отправлялись."
BROADCAST_ALREADY_SENT = "Эта рассылка уже отправлена."
BROADCAST_ALREADY_CANCELLED = "Эта рассылка уже отменена."
BROADCAST_IN_PROGRESS = (
    "Эта рассылка уже отправляется или была прервана перезапуском бота. Повторно она не отправится."
)
BROADCAST_UNKNOWN = "Предпросмотр устарел. Отправьте /broadcast с текстом заново."


def today_header(d: date) -> str:
    return f"Записи на {fmt_date(d)}"


def today_line(start: datetime, service_title: str, name: str, phone: str) -> str:
    return f"{fmt_time(start)} — {service_title} — {name}, {phone}"


def broadcast_preview_header(recipients: int) -> str:
    return (
        f"Предпросмотр рассылки. Получателей: {recipients}.\n"
        "Следующее сообщение — текст в том виде, в каком его получат клиенты."
    )


def broadcast_report(delivered: int, total: int) -> str:
    return f"Рассылка завершена: доставлено {delivered} из {total}."


# --- Т1, Т4, Т5 ---


def confirmation(shop: Shop, name: str, service: Service, master: Master, start: datetime) -> str:
    """Т1."""
    return (
        f"🤜🤛 {name}, вы записаны в 💈{shop.kind} {shop.name} на услугу «{service.title}» "
        f"к мастеру {master.name}\n"
        f"⌚ {fmt_date(start.astimezone(TZ).date())} в {fmt_time(start)}\n"
        f"📍 {shop.address}\n"
        "Ждём вас!"
    )


def admin_new_booking(
    name: str, phone: str, service: Service, master: Master, start: datetime
) -> str:
    """Т4."""
    return (
        f"🆕 Новая запись: {name}, {phone}\n"
        f"{service.title}, мастер {master.name}, "
        f"{fmt_date(start.astimezone(TZ).date())} {fmt_time(start)}"
    )


def admin_cancelled(
    name: str, phone: str, service_title: str, master_name: str, start: datetime
) -> str:
    """Т5."""
    return (
        f"❌ Отмена записи: {name}, {phone}\n"
        f"{service_title}, мастер {master_name}, "
        f"{fmt_date(start.astimezone(TZ).date())} {fmt_time(start)}"
    )


def reminder(kind: str, name: str, service_title: str, master_name: str, start: datetime) -> str:
    """Т2 (kind «24h», «завтра») и Т3 (kind «2h», «сегодня»)."""
    day = "сегодня" if kind == "2h" else "завтра"
    return (
        f"💈 {name}, напоминаем: {day} в {fmt_time(start)} вы записаны к мастеру {master_name} "
        f"на «{service_title}». Если планы изменились, отмените запись кнопкой ниже, "
        "чтобы освободить время для других."
    )
