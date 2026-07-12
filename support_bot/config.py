"""Настройки бота поддержки IKK. Всё чувствительное — из переменных окружения."""
import os

# Токен бота поддержки от @BotFather (обязательно)
BOT_TOKEN = os.environ.get("SUPPORT_BOT_TOKEN", "")

# Кому приходят тикеты (Telegram ID владельца)
OWNER_ID = int(os.environ.get("OWNER_ID", "0")) or None
OWNER_USERNAME = os.environ.get("OWNER_USERNAME", "IKKvpndev")

# Основной бот (для кнопки-ссылки)
MAIN_BOT_USERNAME = os.environ.get("BOT_USERNAME", "IKKvpnpbot")

# Своя база: тикеты и переписка
DB_PATH = os.environ.get("SUPPORT_DB_PATH", "ikk_support.db")
