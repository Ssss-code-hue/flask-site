"""IKK VPN — Telegram-бот: оплата звёздами, подписки, рефералы, инструкции."""
import asyncio
import logging
import time
from datetime import datetime

from aiogram import Bot, Dispatcher, F
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.filters import Command, CommandStart
from aiogram.types import (
    BotCommand,
    CallbackQuery,
    LabeledPrice,
    MenuButtonCommands,
    Message,
    PreCheckoutQuery,
)

from . import db, texts
from .config import (
    BOT_TOKEN,
    BOT_USERNAME,
    OWNER_ID,
    OWNER_USERNAME,
    PLANS,
    REFERRAL_BONUS_DAYS,
)
from .keyboards import back_kb, devices_kb, main_menu, offer_consent_kb, plans_kb
from .panel import get_subscription_url

logging.basicConfig(level=logging.INFO)
dp = Dispatcher()


def fmt_date(ts):
    return datetime.fromtimestamp(ts).strftime("%d.%m.%Y")


def ref_text(uid):
    link = f"https://t.me/{BOT_USERNAME}?start=ref_{uid}"
    n = db.count_referrals(uid)
    return texts.REF.format(link=link, days=REFERRAL_BONUS_DAYS, count=n, earned=n * REFERRAL_BONUS_DAYS)


def status_text(uid):
    u = db.get_user(uid)
    if u and u["sub_until"] and u["sub_until"] > int(time.time()):
        return texts.STATUS_ACTIVE.format(date=fmt_date(u["sub_until"]))
    return texts.STATUS_INACTIVE


# ============ /start (+ рефералы) ============
@dp.message(CommandStart())
async def cmd_start(message: Message):
    uid = message.from_user.id
    uname = message.from_user.username
    existed = db.get_user(uid) is not None
    db.create_user(uid, uname)
    db.update_username(uid, uname)

    # обработка реферальной ссылки: /start ref_<id>
    parts = (message.text or "").split(maxsplit=1)
    if not existed and len(parts) > 1 and parts[1].startswith("ref_"):
        try:
            ref_id = int(parts[1][4:])
        except ValueError:
            ref_id = None
        if ref_id and ref_id != uid and db.get_user(ref_id):
            db.set_referred_by(uid, ref_id)
            new_until = db.add_days(ref_id, REFERRAL_BONUS_DAYS)
            try:
                await message.bot.send_message(
                    ref_id,
                    texts.REF_BONUS.format(days=REFERRAL_BONUS_DAYS, date=fmt_date(new_until)),
                )
            except Exception:
                pass

    await message.answer(texts.WELCOME, reply_markup=main_menu())


# ============ Команды-функции (видны в меню слева от поля ввода) ============
@dp.message(Command("buy"))
async def cmd_buy(message: Message):
    await message.answer(texts.OFFER_INTRO, reply_markup=offer_consent_kb())


@dp.message(Command("devices"))
async def cmd_devices(message: Message):
    await message.answer(texts.DEVICES_INTRO, reply_markup=devices_kb())


@dp.message(Command("ref"))
async def cmd_ref(message: Message):
    db.create_user(message.from_user.id, message.from_user.username)
    await message.answer(
        ref_text(message.from_user.id), reply_markup=back_kb("menu"), disable_web_page_preview=True
    )


@dp.message(Command("status"))
async def cmd_status(message: Message):
    await message.answer(status_text(message.from_user.id), reply_markup=main_menu())


@dp.message(Command("help"))
async def cmd_help(message: Message):
    await message.answer(texts.HELP, reply_markup=main_menu())


# ============ Навигация по меню ============
@dp.callback_query(F.data == "menu")
async def cb_menu(cq: CallbackQuery):
    await cq.message.edit_text(texts.WELCOME, reply_markup=main_menu())
    await cq.answer()


@dp.callback_query(F.data == "buy")
async def cb_buy(cq: CallbackQuery):
    # перед покупкой — оферта и согласие
    await cq.message.edit_text(texts.OFFER_INTRO, reply_markup=offer_consent_kb())
    await cq.answer()


@dp.callback_query(F.data == "offer_text")
async def cb_offer_text(cq: CallbackQuery):
    # текстовая оферта (запасной вариант, если мини-приложение не настроено)
    await cq.message.edit_text(texts.OFFER_TEXT, reply_markup=back_kb("buy"), disable_web_page_preview=True)
    await cq.answer()


@dp.callback_query(F.data == "plans")
async def cb_plans(cq: CallbackQuery):
    # пользователь принял оферту — показываем тарифы
    await cq.message.edit_text(texts.PLANS_INTRO, reply_markup=plans_kb())
    await cq.answer()


@dp.callback_query(F.data == "devices")
async def cb_devices(cq: CallbackQuery):
    await cq.message.edit_text(texts.DEVICES_INTRO, reply_markup=devices_kb())
    await cq.answer()


@dp.callback_query(F.data == "ref")
async def cb_ref(cq: CallbackQuery):
    await cq.message.edit_text(
        ref_text(cq.from_user.id), reply_markup=back_kb("menu"), disable_web_page_preview=True
    )
    await cq.answer()


@dp.callback_query(F.data == "status")
async def cb_status(cq: CallbackQuery):
    await cq.message.edit_text(status_text(cq.from_user.id), reply_markup=main_menu())
    await cq.answer()


# ============ Оплата звёздами (XTR) ============
@dp.callback_query(F.data.startswith("plan:"))
async def cb_plan(cq: CallbackQuery):
    code = cq.data.split(":", 1)[1]
    p = PLANS.get(code)
    if not p:
        await cq.answer("Тариф не найден", show_alert=True)
        return
    await cq.message.answer_invoice(
        title=f"IKK VPN — {p['title']}",
        description=f"Подписка на {p['title']} ({p['days']} дней). Работает в Happ.",
        payload=f"plan:{code}",
        provider_token="",          # для Telegram Stars токен не нужен
        currency="XTR",             # Telegram Stars
        prices=[LabeledPrice(label=p["title"], amount=p["stars"])],
    )
    await cq.answer()


@dp.pre_checkout_query()
async def pre_checkout(q: PreCheckoutQuery):
    # подтверждаем, что готовы принять оплату
    await q.answer(ok=True)


@dp.message(F.successful_payment)
async def on_paid(message: Message):
    sp = message.successful_payment
    code = sp.invoice_payload.split(":", 1)[1] if ":" in sp.invoice_payload else None
    p = PLANS.get(code)
    uid = message.from_user.id
    days = p["days"] if p else 30

    new_until = db.add_days(uid, days)
    db.record_payment(uid, code, sp.total_amount, sp.telegram_payment_charge_id)

    sub_url = get_subscription_url(uid, new_until)
    if sub_url:
        await message.answer(
            texts.PAID_WITH_KEY.format(date=fmt_date(new_until), url=sub_url),
            reply_markup=devices_kb(),
        )
    else:
        await message.answer(
            texts.PAID_NO_KEY.format(date=fmt_date(new_until), owner=OWNER_USERNAME),
            reply_markup=devices_kb(),
        )

    # уведомление владельцу
    if OWNER_ID:
        try:
            who = f"@{message.from_user.username}" if message.from_user.username else f"id{uid}"
            await message.bot.send_message(
                OWNER_ID,
                f"💰 Оплата: {who} — {p['title'] if p else code}, {sp.total_amount} ⭐. "
                f"Активно до {fmt_date(new_until)}.",
            )
        except Exception:
            pass


async def main():
    if not BOT_TOKEN:
        raise SystemExit("Ошибка: задайте переменную окружения BOT_TOKEN (токен от @BotFather).")
    db.init_db()
    bot = Bot(BOT_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))

    # список функций для кнопки «Меню» слева от поля ввода
    await bot.set_my_commands([
        BotCommand(command="start", description="Главное меню"),
        BotCommand(command="buy", description="Купить подписку"),
        BotCommand(command="devices", description="Инструкция подключения"),
        BotCommand(command="status", description="Моя подписка"),
        BotCommand(command="ref", description="Пригласить друга (+3 дня)"),
        BotCommand(command="help", description="Помощь"),
    ])
    # кнопка слева показывает список функций (команд)
    await bot.set_chat_menu_button(menu_button=MenuButtonCommands())

    logging.info("IKK VPN bot запущен")
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
