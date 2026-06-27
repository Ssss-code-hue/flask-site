"""Настройки бота IKK VPN. Всё чувствительное берётся из переменных окружения."""
import os

# Токен бота от @BotFather (обязательно)
BOT_TOKEN = os.environ.get("BOT_TOKEN", "")

# Юзернеймы (без @)
BOT_USERNAME = os.environ.get("BOT_USERNAME", "IKKvpnpbot")
OWNER_USERNAME = os.environ.get("OWNER_USERNAME", "IKKvpndev")

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

# Сколько дней бесплатно даёт реферальная ссылка
REFERRAL_BONUS_DAYS = int(os.environ.get("REFERRAL_BONUS_DAYS", "3"))

# Путь к базе SQLite
DB_PATH = os.environ.get("DB_PATH", "ikk_bot.db")

# Тарифы. stars — цена в Telegram Stars (XTR), days — длительность.
PLANS = {
    "1m":  {"title": "1 месяц",    "days": 30,  "stars": 100},
    "3m":  {"title": "3 месяца",   "days": 90,  "stars": 270},
    "12m": {"title": "12 месяцев", "days": 365, "stars": 900},
}
