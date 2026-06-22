"""Запуск бота: python run_bot.py (из корня проекта)."""
import asyncio

from bot.main import main

if __name__ == "__main__":
    asyncio.run(main())
