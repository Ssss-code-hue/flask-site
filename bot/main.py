"""IKK VPN — Telegram-бот: оплата звёздами и картой/СБП, подписки, рефералы."""
import asyncio
import logging
import time
import uuid
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

import lolz

from . import db, texts
from .config import (
    BOT_TOKEN,
    BOT_USERNAME,
    OWNER_ID,
    OWNER_USERNAME,
    PLANS,
    REFERRAL_BONUS_DAYS,
    SITE_URL,
    TRIAL_DAYS,
)
from .keyboards import (back_kb, card_invoice_kb, devices_kb, docs_kb,
                        main_menu, offer_consent_kb, pay_method_kb, plans_kb,
                        trial_consent_kb)
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
    await message.answer(texts.HELP.format(trial=TRIAL_DAYS), reply_markup=main_menu())


# ============ Пробный период ============
async def _give_trial(uid, username, send):
    """Общая логика «Попробовать бесплатно» для команды и кнопки.

    send(text, kb) — как отправить ответ. Возвращает False, если пробный
    период уже использован (нужно показать alert), иначе True.
    """
    db.create_user(uid, username)
    u = db.get_user(uid)
    if u["trial_used"]:
        return False

    new_until = db.add_days(uid, TRIAL_DAYS)
    db.mark_trial_used(uid)
    sub_url = get_subscription_url(uid, new_until)
    if sub_url:
        await send(texts.TRIAL_OK.format(date=fmt_date(new_until), url=sub_url), devices_kb())
    else:
        await send(
            texts.TRIAL_NO_KEY.format(date=fmt_date(new_until), owner=OWNER_USERNAME),
            devices_kb(),
        )
    return True


def _trial_available(uid, username):
    """Проверяет, доступен ли пробный период (заодно регистрирует пользователя)."""
    db.create_user(uid, username)
    return not db.get_user(uid)["trial_used"]


@dp.message(Command("trial"))
async def cmd_trial(message: Message):
    if _trial_available(message.from_user.id, message.from_user.username):
        # сначала — согласие с офертой, активация кнопкой «Принимаю»
        await message.answer(
            texts.TRIAL_OFFER.format(days=TRIAL_DAYS), reply_markup=trial_consent_kb()
        )
    else:
        await message.answer(
            "🆓 Пробный период уже был использован.\n"
            "Оформите подписку — или получите бесплатные дни за друзей 🎁.",
            reply_markup=main_menu(),
        )


@dp.callback_query(F.data == "trial")
async def cb_trial(cq: CallbackQuery):
    if _trial_available(cq.from_user.id, cq.from_user.username):
        await cq.message.edit_text(
            texts.TRIAL_OFFER.format(days=TRIAL_DAYS), reply_markup=trial_consent_kb()
        )
        await cq.answer()
    else:
        await cq.answer(
            "🆓 Пробный период уже был использован. "
            "Оформите подписку или пригласите друга 🎁",
            show_alert=True,
        )


@dp.callback_query(F.data == "offer_text_trial")
async def cb_offer_text_trial(cq: CallbackQuery):
    # текстовая оферта (если мини-приложение не настроено), назад — к пробному периоду
    await cq.message.edit_text(texts.OFFER_TEXT, reply_markup=back_kb("trial"), disable_web_page_preview=True)
    await cq.answer()


@dp.callback_query(F.data == "trial_go")
async def cb_trial_go(cq: CallbackQuery):
    # пользователь нажал «Принимаю» — активируем пробный период
    async def send(text, kb):
        await cq.message.edit_text(text, reply_markup=kb)
    if await _give_trial(cq.from_user.id, cq.from_user.username, send):
        await cq.answer()
        if OWNER_ID:
            try:
                who = (f"@{cq.from_user.username}" if cq.from_user.username
                       else f"id{cq.from_user.id}")
                await cq.bot.send_message(OWNER_ID, f"🆓 Пробный период активирован: {who}")
            except Exception:
                pass
    else:
        await cq.answer(
            "🆓 Пробный период уже был использован. "
            "Оформите подписку или пригласите друга 🎁",
            show_alert=True,
        )


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


@dp.callback_query(F.data == "docs")
async def cb_docs(cq: CallbackQuery):
    await cq.message.edit_text(texts.DOCS, reply_markup=docs_kb())
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


# ============ Выбор тарифа и способа оплаты ============
async def _send_stars_invoice(cq: CallbackQuery, code, p):
    await cq.message.answer_invoice(
        title=f"IKK VPN — {p['title']}",
        description=f"Подписка на {p['title']} ({p['days']} дней). Работает в Happ.",
        payload=f"plan:{code}",
        provider_token="",          # для Telegram Stars токен не нужен
        currency="XTR",             # Telegram Stars
        prices=[LabeledPrice(label=p["title"], amount=p["stars"])],
    )
    await cq.answer()


@dp.callback_query(F.data.startswith("plan:"))
async def cb_plan(cq: CallbackQuery):
    code = cq.data.split(":", 1)[1]
    p = PLANS.get(code)
    if not p:
        await cq.answer("Тариф не найден", show_alert=True)
        return
    if lolz.can_invoice():
        # доступны два способа — даём выбрать
        await cq.message.edit_text(
            texts.PAY_METHOD.format(title=p["title"], days=p["days"]),
            reply_markup=pay_method_kb(code),
        )
        await cq.answer()
    else:
        # карта не настроена — сразу счёт в звёздах, как раньше
        await _send_stars_invoice(cq, code, p)


# ============ Оплата звёздами (XTR) ============
@dp.callback_query(F.data.startswith("paystars:"))
async def cb_paystars(cq: CallbackQuery):
    code = cq.data.split(":", 1)[1]
    p = PLANS.get(code)
    if not p:
        await cq.answer("Тариф не найден", show_alert=True)
        return
    await _send_stars_invoice(cq, code, p)


# ============ Оплата картой/СБП (Lolz Merchant) ============
@dp.callback_query(F.data.startswith("paycard:"))
async def cb_paycard(cq: CallbackQuery):
    code = cq.data.split(":", 1)[1]
    p = PLANS.get(code)
    if not p:
        await cq.answer("Тариф не найден", show_alert=True)
        return
    db.create_user(cq.from_user.id, cq.from_user.username)
    payment_id = uuid.uuid4().hex
    try:
        # requests — блокирующий, уводим в поток, чтобы не тормозить бота
        invoice_id, pay_url = await asyncio.to_thread(
            lolz.create_invoice,
            p["rub"],
            payment_id,
            f"IKK VPN — подписка «{p['title']}» (Telegram)",
            f"https://t.me/{BOT_USERNAME}",       # после оплаты — назад в бот
            f"{SITE_URL}/pay/callback",           # вебхук уйдёт на сайт; бот опрашивает сам
        )
    except Exception:
        logging.exception("Lolz (бот): не удалось создать счёт (план %s)", code)
        await cq.answer(texts.CARD_FAIL, show_alert=True)
        return
    db.create_bot_invoice(payment_id, str(invoice_id), cq.from_user.id, code, p["rub"])
    await cq.message.edit_text(
        texts.CARD_INVOICE.format(rub=p["rub"], title=p["title"]),
        reply_markup=card_invoice_kb(pay_url, payment_id),
    )
    await cq.answer()


async def _credit_card_payment(bot, rec):
    """Зачисляет оплаченный счёт Lolz. True — если зачислили именно сейчас.

    Идемпотентно: статус в базе меняется одним UPDATE'ом со сверкой на
    pending, поэтому кнопка «Я оплатил» и фоновый опрос не задвоят дни.
    """
    if not db.settle_bot_invoice(rec["payment_id"], "paid"):
        return False
    p = PLANS.get(rec["plan"], {})
    days = p.get("days", 30)
    uid = rec["user_id"]
    new_until = db.add_days(uid, days)
    db.record_payment(uid, rec["plan"], 0, f"lolz:{rec['payment_id']}")

    sub_url = get_subscription_url(uid, new_until)
    if sub_url:
        text = texts.PAID_WITH_KEY.format(date=fmt_date(new_until), url=sub_url)
    else:
        text = texts.PAID_NO_KEY.format(date=fmt_date(new_until), owner=OWNER_USERNAME)
    try:
        await bot.send_message(uid, text, reply_markup=devices_kb())
    except Exception:
        logging.exception("Lolz (бот): оплату %s зачислили, но сообщение "
                          "пользователю %s не ушло", rec["payment_id"], uid)

    if OWNER_ID:
        try:
            u = db.get_user(uid)
            who = f"@{u['username']}" if u and u["username"] else f"id{uid}"
            await bot.send_message(
                OWNER_ID,
                f"💳 Оплата картой/СБП: {who} — {p.get('title', rec['plan'])}, "
                f"{rec['amount_rub']} ₽. Активно до {fmt_date(new_until)}.",
            )
        except Exception:
            pass
    return True


@dp.callback_query(F.data.startswith("paycheck:"))
async def cb_paycheck(cq: CallbackQuery):
    payment_id = cq.data.split(":", 1)[1]
    rec = db.get_bot_invoice(payment_id)
    if not rec or rec["user_id"] != cq.from_user.id:
        await cq.answer("Счёт не найден", show_alert=True)
        return
    if rec["status"] == "paid":
        await cq.answer("Оплата уже зачислена ✅", show_alert=True)
        return
    try:
        inv = await asyncio.to_thread(lolz.get_invoice, payment_id)
    except Exception:
        logging.exception("Lolz (бот): не удалось проверить счёт %s", payment_id)
        inv = None
    if inv and inv.get("status") == "paid":
        await _credit_card_payment(cq.bot, rec)
        await cq.answer()
    else:
        await cq.answer(texts.CARD_PENDING_ALERT, show_alert=True)


CARD_POLL_INTERVAL = 60          # раз в минуту опрашиваем неоплаченные счета
CARD_INVOICE_TTL = 2 * 3600      # счёт живёт час; ещё час запаса — и бросаем опрос


async def poll_card_invoices(bot):
    """Фоновая проверка счетов: подписка активируется без нажатия кнопки."""
    while True:
        await asyncio.sleep(CARD_POLL_INTERVAL)
        try:
            for rec in db.pending_bot_invoices():
                if int(time.time()) - rec["created_at"] > CARD_INVOICE_TTL:
                    db.settle_bot_invoice(rec["payment_id"], "expired")
                    continue
                try:
                    inv = await asyncio.to_thread(lolz.get_invoice, rec["payment_id"])
                except Exception:
                    continue                     # сеть/API упали — вернёмся через минуту
                if inv and inv.get("status") == "paid":
                    await _credit_card_payment(bot, rec)
        except Exception:
            logging.exception("Lolz (бот): ошибка фоновой проверки счетов")


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
        BotCommand(command="trial", description=f"Попробовать бесплатно ({TRIAL_DAYS} дн.)"),
        BotCommand(command="buy", description="Купить подписку"),
        BotCommand(command="devices", description="Инструкция подключения"),
        BotCommand(command="status", description="Моя подписка"),
        BotCommand(command="ref", description="Пригласить друга (+3 дня)"),
        BotCommand(command="help", description="Помощь"),
    ])
    # кнопка слева показывает список функций (команд)
    await bot.set_chat_menu_button(menu_button=MenuButtonCommands())

    if lolz.can_invoice():
        asyncio.create_task(poll_card_invoices(bot))
        logging.info("Lolz: оплата картой/СБП включена (фоновая проверка счетов)")
    else:
        logging.info("Lolz: LOLZ_TOKEN/LOLZ_MERCHANT_ID не заданы — в боте только звёзды")

    logging.info("IKK VPN bot запущен")
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
