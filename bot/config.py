"""Настройки бота IKK VPN. Всё чувствительное берётся из переменных окружения."""
import os
from datetime import date, datetime

# Токен бота от @BotFather (обязательно)
BOT_TOKEN = os.environ.get("BOT_TOKEN", "")

# Юзернеймы (без @)
BOT_USERNAME = os.environ.get("BOT_USERNAME", "IKKvpnpbot")
OWNER_USERNAME = os.environ.get("OWNER_USERNAME", "IKKvpndev")
# Бот поддержки — сюда ведут кнопка «Поддержка» и все упоминания в текстах
SUPPORT_BOT_USERNAME = os.environ.get("SUPPORT_BOT_USERNAME", "IKKvpnsupport_bot")

# ID владельца для уведомлений об оплатах (число). Узнать можно у @userinfobot
OWNER_ID = int(os.environ.get("OWNER_ID", "0")) or None

# HTTPS-адрес мини-приложения (страница /app сайта).
# Если переменная пустая/кривая (не начинается с https://) — берём рабочий адрес по умолчанию.
WEBAPP_URL = os.environ.get("WEBAPP_URL", "").strip()
if not WEBAPP_URL.startswith("https://"):
    WEBAPP_URL = "https://ikkvpn.com/app"

# HTTPS-адрес мини-приложения с офертой (страница /offer-app).
OFFER_URL = os.environ.get("OFFER_URL", "").strip()
if not OFFER_URL.startswith("https://"):
    OFFER_URL = "https://ikkvpn.com/offer-app"

# Мини-приложения с пользовательским соглашением и политикой конфиденциальности
TERMS_URL = os.environ.get("TERMS_URL", "").strip()
if not TERMS_URL.startswith("https://"):
    TERMS_URL = "https://ikkvpn.com/terms-app"
PRIVACY_URL = os.environ.get("PRIVACY_URL", "").strip()
if not PRIVACY_URL.startswith("https://"):
    PRIVACY_URL = "https://ikkvpn.com/privacy-app"

# Публичный адрес сайта — для callback-ссылок счетов Lolz
SITE_URL = os.environ.get("SITE_URL", "https://ikkvpn.com").rstrip("/")

# Сколько дней бесплатно даёт реферальная ссылка
REFERRAL_BONUS_DAYS = int(os.environ.get("REFERRAL_BONUS_DAYS", "10"))

# Когда начислять бонус пригласившему. По умолчанию — только за первую ОПЛАТУ
# приглашённого. Раньше дни давались за нажатие Start, и подписку можно было
# бесконечно продлевать себе новыми Telegram-аккаунтами.
# REFERRAL_ON_TRIAL=1 переводит триггер на активацию пробного периода: рефералов
# станет больше, но дыра для накрутки вернётся — триал тоже бесплатный.
REFERRAL_ON_TRIAL = os.environ.get("REFERRAL_ON_TRIAL", "0") == "1"

# Пробный период «Попробовать бесплатно» (дней, раз на пользователя)
TRIAL_DAYS = int(os.environ.get("TRIAL_DAYS", "15"))

# Путь к базе SQLite
DB_PATH = os.environ.get("DB_PATH", "ikk_bot.db")

# Тарифы. stars — цена в Telegram Stars (XTR), rub — в рублях (Platega), days — длительность.
PLANS = {
    "1m":  {"title": "1 месяц",    "days": 30,  "stars": 50,  "rub": 50},
    "3m":  {"title": "3 месяца",   "days": 90,  "stars": 135, "rub": 135},
    "12m": {"title": "12 месяцев", "days": 365, "stars": 450, "rub": 450},
}

# Акция «конец месяца»: к тарифу «1 месяц» добавляем бонусные дни при покупке
# В БОТЕ (на сайте акции нет — там месяц остаётся 30 дней).
# Дата окончания включительно. После неё тариф сам вернётся к 30 дням, вручную
# откатывать ничего не надо — поэтому и сделано датой, а не правкой PLANS.
SALE_PLAN = "1m"
SALE_BONUS_DAYS = int(os.environ.get("SALE_BONUS_DAYS", "20"))
SALE_UNTIL = os.environ.get("SALE_UNTIL", "2026-08-03")


def sale_active():
    """Идёт ли акция сейчас. Дата берётся по часам сервера."""
    try:
        return date.today() <= datetime.strptime(SALE_UNTIL, "%Y-%m-%d").date()
    except ValueError:
        return False                     # кривая дата — считаем, что акции нет


def plan_days(code):
    """Сколько дней даёт тариф при покупке в боте — уже с учётом акции.

    Единая точка: и начисление, и тексты берут длительность отсюда, иначе
    легко пообещать 50 дней, а начислить 30.
    """
    days = (PLANS.get(code) or {}).get("days", 30)
    if code == SALE_PLAN and sale_active():
        days += SALE_BONUS_DAYS
    return days
