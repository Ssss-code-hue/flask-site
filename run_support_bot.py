"""Запуск бота поддержки IKK: python run_support_bot.py

Нужны переменные окружения:
  SUPPORT_BOT_TOKEN — токен бота поддержки (@BotFather)
  OWNER_ID          — Telegram ID владельца (кому приходят тикеты)
"""
import asyncio

from support_bot.main import main

if __name__ == "__main__":
    asyncio.run(main())
