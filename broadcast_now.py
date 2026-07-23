"""Разовая рассылка «вы ещё не подключились» — всем с активной подпиской,
кто ни разу не выходил в сеть через VPN.

Запуск на сервере, из каталога бота (где есть BOT_TOKEN и доступ к панели):

    python broadcast_now.py           # разослать
    python broadcast_now.py --dry     # показать список, НЕ отправляя

Идёт по всем активным пользователям, проверяет каждого в панели Marzban:
подключался ли (онлайн/трафик). Кому не подключался — шлёт напоминание с
кнопкой «Подключиться сейчас». Подключившихся не трогает.

Это одноразовая акция; регулярные напоминания за N дней до конца триала
шлёт сам бот (см. remind_trial_ending в bot/main.py).
"""
import asyncio
import sys
import time

from aiogram import Bot
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode

from bot import db, texts
from bot.config import BOT_TOKEN
from bot.keyboards import connect_kb
from bot.panel import get_subscription_url, sub_token, user_connected


def _token(uid, sub_until):
    return sub_token(get_subscription_url(uid, sub_until))


async def main(dry=False):
    if not BOT_TOKEN:
        raise SystemExit("BOT_TOKEN не задан")
    db.init_db()
    now = int(time.time())
    users = db.active_users(now)
    print(f"активных пользователей: {len(users)}")

    targets = []
    for uid, sub_until in users:
        connected = user_connected(uid)
        if connected is None:
            print(f"  {uid}: панель не ответила — пропуск")
            continue
        if connected:
            continue                      # уже пользуется — не трогаем
        targets.append((uid, sub_until))

    print(f"не подключались (кандидаты на рассылку): {len(targets)}")
    if dry:
        for uid, _ in targets:
            print("  ->", uid)
        return

    bot = Bot(BOT_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
    sent = failed = 0
    for uid, sub_until in targets:
        token = _token(uid, sub_until)
        text = texts.NOT_CONNECTED_NUDGE.format(
            date=time.strftime("%d.%m.%Y", time.localtime(sub_until)))
        try:
            await bot.send_message(uid, text, reply_markup=connect_kb(token))
            sent += 1
        except Exception as e:
            failed += 1
            print(f"  {uid}: не отправлено ({e})")
        await asyncio.sleep(0.1)          # мягкий темп, не упереться в лимиты
    await bot.session.close()
    print(f"готово: отправлено {sent}, ошибок {failed}")


if __name__ == "__main__":
    asyncio.run(main(dry="--dry" in sys.argv))
