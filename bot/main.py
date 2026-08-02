"""IKK VPN — Telegram-бот: оплата звёздами и картой/СБП, подписки, рефералы."""
import asyncio
import json
import logging
import os
import time
import uuid
from datetime import datetime
from pathlib import Path

from aiogram import Bot, Dispatcher, F
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.exceptions import TelegramForbiddenError
from aiogram.filters import Command, CommandStart
from aiogram.types import (
    BotCommand,
    CallbackQuery,
    FSInputFile,
    LabeledPrice,
    MenuButtonCommands,
    Message,
    PreCheckoutQuery,
)

import lolz
import platega

from . import db, texts
from .config import (
    BOT_TOKEN,
    BOT_USERNAME,
    OWNER_ID,
    PLANS,
    REFERRAL_BONUS_DAYS,
    REFERRAL_ON_TRIAL,
    SALE_BONUS_DAYS,
    SALE_PLAN,
    SALE_UNTIL,
    SITE_URL,
    SUPPORT_BOT_USERNAME,
    TRIAL_DAYS,
    plan_days,
    sale_active,
)
from .keyboards import (back_kb, card_invoice_kb, connect_kb, devices_kb,
                        docs_kb, main_menu, offer_consent_kb, paid_kb,
                        pay_method_kb, plans_kb, promo_offer_kb, ref_share_kb,
                        renew_kb, sale_kb, trial_consent_kb)
from .panel import (get_subscription_url, online_usernames, site_sub_url,
                    sub_token, user_connected)

logging.basicConfig(level=logging.INFO)
dp = Dispatcher()


def fmt_date(ts):
    return datetime.fromtimestamp(ts).strftime("%d.%m.%Y")


# Баннер IKK VPN (в стиле сайта). Каждое сообщение бота — одно сообщение:
# фото + подпись + кнопки, так картинка и текст одной ширины.
# Кнопки Telegram растягивает по ширине самого широкого элемента сообщения,
# поэтому под фото они выходят ровными, а под голым текстом — рваными.
# Файл загружается один раз, дальше используем file_id из кэша.
BANNER = Path(__file__).parent / "assets" / "banner.png"
_banner_file_id = None

# Лимит подписи к фото у Telegram. У обычного сообщения он 4096, поэтому
# редкий длинный текст отправляем без баннера, а не теряем совсем.
CAPTION_LIMIT = 1024


async def send_banner_to(bot, chat_id, text, reply_markup=None):
    """Отправляет в чат НОВОЕ сообщение с баннером IKK VPN сверху."""
    global _banner_file_id
    if BANNER.exists() and len(text) <= CAPTION_LIMIT:
        try:
            photo = _banner_file_id or FSInputFile(BANNER)
            sent = await bot.send_photo(chat_id, photo, caption=text,
                                        reply_markup=reply_markup)
            if not _banner_file_id:
                _banner_file_id = sent.photo[-1].file_id
            return
        except TelegramForbiddenError:
            # чат недоступен (бот заблокирован) — текстом тоже не дойдёт,
            # второй запрос только зря нагружает API и засоряет журнал
            raise
        except Exception:
            logging.exception("Баннер не отправился — шлём текстом")
    await bot.send_message(chat_id, text, reply_markup=reply_markup)


async def broadcast(message: Message, users, make_message):
    """Общая механика рассылок: отправка, подсчёт, отчёт владельцу.

    make_message(uid) -> (text, клавиатура) — у рефералки текст свой на
    каждого. Заблокировавших помечаем в базе, чтобы следующая рассылка их
    не трогала и статистика не врала.
    """
    sent = blocked = failed = 0
    for uid in users:
        text, kb = make_message(uid)
        try:
            await send_banner_to(message.bot, uid, text, kb)
            sent += 1
        except TelegramForbiddenError:
            db.mark_blocked(uid)
            blocked += 1
        except Exception:
            logging.exception("Рассылка: сбой у пользователя %s", uid)
            failed += 1
        await asyncio.sleep(0.1)

    report = f"✅ Готово.\nДоставлено: {sent}\nЗаблокировали бота: {blocked}"
    if failed:
        report += f"\nПрочие ошибки: {failed} — причина в журнале"
    if blocked:
        report += (f"\n\nЭти {blocked} больше не получат рассылок — "
                   f"вернутся сами, если снова напишут боту.")
    await message.answer(report)


async def send_banner(message: Message, text, reply_markup=None):
    await send_banner_to(message.bot, message.chat.id, text, reply_markup)


async def send_welcome(message: Message):
    await send_banner(message, texts.WELCOME, main_menu())


async def show_screen(cq: CallbackQuery, text, reply_markup=None, **kwargs):
    """Меняет содержимое текущего сообщения по нажатию кнопки.

    Новых сообщений не шлём: у приветствия (фото) видоизменяем
    подпись — баннер остаётся сверху, — у обычного текста сам текст.
    Все экраны бота умещаются в лимит подписи (1024 символа).
    """
    m = cq.message
    if m.photo:
        await m.edit_caption(caption=text, reply_markup=reply_markup)
    else:
        await m.edit_text(text, reply_markup=reply_markup, **kwargs)


def ref_text(uid):
    link = f"https://t.me/{BOT_USERNAME}?start=ref_{uid}"
    n = db.count_referrals(uid)
    return texts.REF.format(link=link, days=REFERRAL_BONUS_DAYS, count=n, earned=n * REFERRAL_BONUS_DAYS)


def sync_panel(uid):
    """Синхронизирует срок подписки с панелью и возвращает её токен.

    Один вызов на все ссылки: обращение к панели идёт с логином, поэтому
    дёргать её отдельно ради ключа и ради deep link — двойная работа.
    """
    u = db.get_user(uid)
    if not (u and u["sub_until"]):
        return None
    token = sub_token(get_subscription_url(uid, u["sub_until"]))
    # Запоминаем токен: запросы подписки приходят на сайт без user_id, и без
    # этой связки понять, кто именно открыл приложение, невозможно (см. /funnel)
    db.remember_sub_token(uid, token)
    return token


def vpn_key(uid, token=None):
    """Ссылка-подписка пользователя бота, None — если панель её не дала.

    Отдаём через наш домен: сайт проксирует подписку по 443 и правит
    XHTTP-параметры (sub.py), без которых ТСПУ рвёт соединение.
    """
    token = token or sync_panel(uid)
    return f"{SITE_URL}/sub/{token}" if token else None


def happ_open_url(uid, token=None):
    """Кликабельная https-ссылка «Открыть в v2RayTun» (редирект на сайте →
    v2raytun://). Telegram отклоняет схему v2raytun:// в ссылках, https — нет."""
    token = token or sync_panel(uid)
    return f"{SITE_URL}/v2raytun/{token}" if token else None


def status_text(uid):
    u = db.get_user(uid)
    if u and u["sub_until"] and u["sub_until"] > int(time.time()):
        return texts.STATUS_ACTIVE_KEY.format(date=fmt_date(u["sub_until"]))
    return texts.STATUS_INACTIVE


def status_kb(uid):
    """Клавиатура «Моя подписка»: при активной подписке — кнопка
    «Подключиться сейчас» с токеном; иначе обычное меню."""
    u = db.get_user(uid)
    if u and u["sub_until"] and u["sub_until"] > int(time.time()):
        return connect_kb(sync_panel(uid))
    return main_menu()


# ============ /start (+ рефералы) ============
def _clean_source(param):
    """Метка источника из ссылки → безопасная строка для базы и отчёта.

    Telegram пропускает в start-параметре только [A-Za-z0-9_-], но пришедшее
    из внешнего мира всё равно режем по длине и приводим к нижнему регистру,
    чтобы «YT» и «yt» не разъехались на две строки в сводке.
    """
    src = "".join(ch for ch in (param or "").lower()
                  if ch.isalnum() or ch in "_-")[:32]
    return src or None


@dp.message(CommandStart())
async def cmd_start(message: Message):
    uid = message.from_user.id
    uname = message.from_user.username
    existed = db.get_user(uid) is not None
    db.create_user(uid, uname)
    db.update_username(uid, uname)

    # обработка реферальной ссылки: /start ref_<id>
    parts = (message.text or "").split(maxsplit=1)
    param = parts[1].strip() if len(parts) > 1 else ""

    # Метка источника: t.me/IKKvpnpbot?start=yt — «пришёл с YouTube».
    # Пишем только новичкам и только один раз (см. db.set_source), иначе
    # источник перепишется при следующем заходе по другой ссылке.
    if not existed and param:
        src = "ref" if param.startswith("ref_") else _clean_source(param)
        if src:
            db.set_source(uid, src)

    if not existed and param.startswith("ref_"):
        try:
            ref_id = int(param[4:])
        except ValueError:
            ref_id = None
        if ref_id and ref_id != uid and db.get_user(ref_id):
            # Только запоминаем, кто кого привёл. Дни начисляем позже — когда
            # приглашённый оплатит (см. reward_referrer). За нажатие Start
            # бонус давать нельзя: новый Telegram-аккаунт заводится за минуту,
            # и подписку можно было продлевать бесконечно бесплатно.
            db.set_referred_by(uid, ref_id)
            try:
                await send_banner_to(
                    message.bot, ref_id,
                    texts.REF_JOINED.format(days=REFERRAL_BONUS_DAYS))
            except TelegramForbiddenError:
                db.mark_blocked(ref_id)
            except Exception:
                logging.exception("Реферал: не смог уведомить %s", ref_id)

    await send_welcome(message)


async def reward_referrer(bot, uid, reason="оплата"):
    """Начисляет бонус пригласившему, когда приглашённый впервые оплатил.

    Вызывается из всех точек успешной оплаты. Награда выдаётся один раз на
    приглашённого — за это отвечает отметка ref_rewarded в базе.
    """
    ref_id = db.pending_referrer(uid)
    if not ref_id:
        return
    db.mark_ref_rewarded(uid)
    new_until = db.add_days(ref_id, REFERRAL_BONUS_DAYS)
    # Бонусные дни надо довести до панели, иначе ключ пригласившего
    # отключится по старому сроку, хотя бот показывает новый.
    token = sync_panel(ref_id)
    logging.info("Реферал: +%s дн. пользователю %s за %s приглашённого %s",
                 REFERRAL_BONUS_DAYS, ref_id, reason, uid)
    try:
        await send_banner_to(
            bot, ref_id,
            texts.REF_BONUS.format(days=REFERRAL_BONUS_DAYS, date=fmt_date(new_until)),
            connect_kb(token))
    except TelegramForbiddenError:
        db.mark_blocked(ref_id)
    except Exception:
        logging.exception("Реферал: бонус начислен, но %s не уведомлён", ref_id)


# ============ Команды-функции (видны в меню слева от поля ввода) ============
@dp.message(Command("buy"))
async def cmd_buy(message: Message):
    await send_banner(message, texts.OFFER_INTRO, offer_consent_kb())


@dp.message(Command("devices"))
async def cmd_devices(message: Message):
    await send_banner(message, texts.DEVICES_INTRO, devices_kb())


@dp.message(Command("ref"))
async def cmd_ref(message: Message):
    db.create_user(message.from_user.id, message.from_user.username)
    await send_banner(message, ref_text(message.from_user.id), back_kb("menu"))


@dp.message(Command("status"))
async def cmd_status(message: Message):
    await send_banner(message, status_text(message.from_user.id),
                      status_kb(message.from_user.id))


@dp.message(Command("help"))
async def cmd_help(message: Message):
    await send_banner(message, texts.HELP.format(trial=TRIAL_DAYS, ref=REFERRAL_BONUS_DAYS),
                      main_menu())


@dp.message(Command("sources"))
async def cmd_sources(message: Message):
    """Откуда приходят пользователи (owner-only).

    Ссылки-метки: t.me/<бот>?start=yt, ?start=tiktok, ?start=vk и т.д.
    «—» в отчёте — пришли до появления меток или по голой ссылке.
    """
    if not OWNER_ID or message.from_user.id != OWNER_ID:
        return
    rows = db.source_stats()
    if not rows:
        await message.answer("Пользователей пока нет.")
        return
    lines = ["📊 <b>Откуда приходят</b>\n",
             "<code>источник      всего  ключ  опл.</code>"]
    for src, total, activated, paid in rows:
        lines.append(f"<code>{src[:12]:<12} {total:>5} {activated:>5} {paid:>5}</code>")
    lines.append("\n<b>ключ</b> — активировали подписку (триал или оплата)")
    lines.append("<b>опл.</b> — заплатили хотя бы раз")
    lines.append(f"\nСсылка с меткой: <code>https://t.me/{BOT_USERNAME}?start=yt</code>")
    await message.answer("\n".join(lines))


@dp.message(Command("broadcast_nc"))
async def cmd_broadcast_nc(message: Message):
    """Разовая рассылка «вы ещё не подключились» — всем активным, кто ни разу
    не выходил в сеть. Только для владельца (OWNER_ID). Работает внутри бота,
    поэтому все настройки (панель, база) уже на месте."""
    if not OWNER_ID or message.from_user.id != OWNER_ID:
        return
    now = int(time.time())
    users = db.active_users(now)
    await message.answer(f"⏳ Проверяю {len(users)} активных подписок…")
    sent = skipped = blocked = failed = 0
    for uid, sub_until in users:
        connected = await asyncio.to_thread(user_connected, uid)
        if connected is None or connected:
            skipped += 1                       # уже пользуется / панель молчит
            continue
        token = sync_panel(uid)
        text = texts.NOT_CONNECTED_NUDGE.format(date=fmt_date(sub_until))
        try:
            await send_banner_to(message.bot, uid, text, connect_kb(token))
            sent += 1
        except TelegramForbiddenError:
            db.mark_blocked(uid)
            blocked += 1
        except Exception:
            logging.exception("Рассылка: сбой у пользователя %s", uid)
            failed += 1
        await asyncio.sleep(0.1)
    await message.answer(
        f"✅ Готово.\nДоставлено: {sent}\nПропущено (подключены/недоступны): "
        f"{skipped}\nЗаблокировали бота: {blocked}\nПрочие ошибки: {failed}")


@dp.message(Command("broadcast_promo"))
async def cmd_broadcast_promo(message: Message):
    """Рассылка про промокод ВСЕМ пользователям бота (owner-only).
    Каждому — баннер, текст и кнопка активации в одно нажатие."""
    if not OWNER_ID or message.from_user.id != OWNER_ID:
        return
    users = db.all_user_ids()
    await message.answer(f"⏳ Рассылаю промокод {PROMO_CODE} по {len(users)} пользователям…")
    text = texts.PROMO_BROADCAST.format(code=PROMO_CODE, days=PROMO_DAYS)
    kb = promo_offer_kb(PROMO_CODE, PROMO_DAYS)
    await broadcast(message, users, lambda uid: (text, kb))


_MONTHS_RU = ("января", "февраля", "марта", "апреля", "мая", "июня", "июля",
              "августа", "сентября", "октября", "ноября", "декабря")


def _sale_until_ru():
    """«2026-08-03» → «3 августа» — для текста рассылки."""
    d = datetime.strptime(SALE_UNTIL, "%Y-%m-%d").date()
    return f"{d.day} {_MONTHS_RU[d.month - 1]}"


@dp.message(Command("broadcast_sale"))
async def cmd_broadcast_sale(message: Message):
    """Рассылка про акцию «конец месяца» ВСЕМ пользователям (owner-only).

    Не даёт разослать после SALE_UNTIL: иначе люди придут за бонусом,
    которого уже нет, — а начисление считается по той же дате.
    """
    if not OWNER_ID or message.from_user.id != OWNER_ID:
        return
    if not sale_active():
        await message.answer(
            f"⛔ Акция закончилась {_sale_until_ru()} — рассылка отменена.\n"
            f"Чтобы продлить, поменяйте SALE_UNTIL в bot/config.py.")
        return

    p = PLANS.get(SALE_PLAN, {})
    base, total = p.get("days", 30), plan_days(SALE_PLAN)
    price = f"{p.get('rub')} ₽" if (platega.is_configured() or lolz.can_invoice()) \
        else f"{p.get('stars')} ⭐"
    users = db.all_user_ids()
    await message.answer(
        f"⏳ Рассылаю акцию (+{SALE_BONUS_DAYS} дн. к месяцу, по {_sale_until_ru()}) "
        f"по {len(users)} пользователям…")
    text = texts.SALE_BROADCAST.format(bonus=SALE_BONUS_DAYS, price=price,
                                       total=total, base=base,
                                       until=_sale_until_ru())
    kb = sale_kb()
    await broadcast(message, users, lambda uid: (text, kb))


@dp.message(Command("broadcast_ref"))
async def cmd_broadcast_ref(message: Message):
    """Рассылка про реферальную программу ВСЕМ пользователям (owner-only).
    Ссылка у каждого своя, поэтому текст и кнопки собираем на каждого."""
    if not OWNER_ID or message.from_user.id != OWNER_ID:
        return
    users = db.all_user_ids()
    await message.answer(
        f"⏳ Рассылаю про рефералы (+{REFERRAL_BONUS_DAYS} дн.) "
        f"по {len(users)} пользователям…")
    def make(uid):
        link = f"https://t.me/{BOT_USERNAME}?start=ref_{uid}"
        return (texts.REF_BROADCAST.format(days=REFERRAL_BONUS_DAYS, link=link),
                ref_share_kb(link))

    await broadcast(message, users, make)


@dp.message(Command("broadcast_lapsed"))
async def cmd_broadcast_lapsed(message: Message):
    """Рассылка «вернитесь» тем, у кого подписка уже закончилась (owner-only).

    Самая тёплая аудитория из бесплатных: человек уже платил или хотя бы
    пробовал. Даём отдельный промокод (не рекламный START7), чтобы по
    статистике активаций было видно, сколько людей вернула именно эта рассылка.
    """
    if not OWNER_ID or message.from_user.id != OWNER_ID:
        return
    users = db.lapsed_users(int(time.time()))
    if not users:
        await message.answer("Некому слать: у всех подписка активна.")
        return

    p = PLANS.get("1m", {})
    price = f"{p.get('rub')} ₽" if (platega.is_configured() or lolz.can_invoice()) \
        else f"{p.get('stars')} ⭐"
    await message.answer(
        f"⏳ Рассылаю «возвращайтесь» (промокод {RETURN_PROMO_CODE}, "
        f"+{RETURN_PROMO_DAYS} дн.) по {len(users)} ушедшим…")
    text = texts.RETURN_BROADCAST.format(price=price, code=RETURN_PROMO_CODE,
                                         days=RETURN_PROMO_DAYS)
    kb = promo_offer_kb(RETURN_PROMO_CODE, RETURN_PROMO_DAYS)
    await broadcast(message, [uid for uid, _ in users], lambda uid: (text, kb))


@dp.message(Command("funnel"))
async def cmd_funnel(message: Message):
    """Где теряются люди между «выдали ключ» и «человек в сети» (owner-only).

    Пять шагов подряд, каждый следующий — подмножество предыдущего:
      ключ → открыл страницу → нажал «добавить» → приложение скачало → в сети.
    Провал между двумя соседними и есть ответ, что чинить. Раньше видно было
    только крайние точки, поэтому «не подключился» означало что угодно.

    «В сети» берём из панели: наш сервер видит только скачивание подписки,
    состоялось ли соединение — знает Marzban.
    """
    if not OWNER_ID or message.from_user.id != OWNER_ID:
        return

    rows = db.funnel_rows()
    if not rows:
        await message.answer("Ключей ещё никому не выдавали.")
        return

    online = online_usernames()            # None, если панель недоступна
    total = len(rows)
    lines = ["📉 <b>Воронка подключения</b>", "", f"Выдано ключей: <b>{total}</b>"]

    # «В сети» приходит из панели и знает всю историю, а промежуточные шаги
    # пишутся только с момента обновления. Ставить их в одну лесенку нельзя:
    # вычитание даст отрицательный «провал» и картину, которой нет.
    if online is None:
        lines.append("В сети: <i>панель недоступна</i>")
        offline = rows
    else:
        offline = [r for r in rows if f"ikk_{r['user_id']}" not in online]
        lines.append(f"В сети: <b>{total - len(offline)}</b> · "
                     f"не подключились: <b>{len(offline)}</b>")

    # Измерить шаги можно только у тех, чей токен уже записан: он появляется,
    # когда человек открывает ключ в боте после обновления.
    measured = [r for r in rows if r["sub_token"]]
    lines += ["", f"<b>Шаги</b> (замерено {len(measured)} из {total}):"]
    if not measured:
        lines.append("<i>пока пусто — после обновления ключ ещё никто "
                     "не открывал</i>")
    else:
        steps = [("открыл страницу", "page_at"),
                 ("нажал «добавить»", "import_at"),
                 ("приложение скачало", "fetch_at")]
        prev = len(measured)
        for name, col in steps:
            n = sum(1 for r in measured if r[col])
            drop = f"  −{prev - n}" if prev > n else ""
            lines.append(f"<code>{n:>3}</code>  {name}{drop}")
            prev = n

    # Кто не подключился и что про него известно — по этому списку и писать
    if offline:
        out = []
        for r in offline:
            if r["fetch_at"]:
                where = "скачал ключ, но не подключился"
                ua = (r["fetch_ua"] or "")[:28]
                where += f" ({ua})" if ua else ""
            elif r["import_at"]:
                where = "жал «добавить», ключ не скачался"
            elif r["page_at"]:
                where = "открыл страницу и бросил"
            elif r["sub_token"]:
                where = "ключ не открывал"
            else:
                where = "замеров ещё нет"
            who = f"@{r['username']}" if r["username"] else f"id{r['user_id']}"
            out.append(f"• {who} — {where}")
        lines += ["", f"<b>Не подключились ({len(offline)}):</b>"] + out[:25]
        if len(out) > 25:
            lines.append(f"…и ещё {len(out) - 25}")

    await message.answer("\n".join(lines))


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
    token = sync_panel(uid)
    sub_url = vpn_key(uid, token)
    if sub_url:
        await send(texts.TRIAL_OK.format(date=fmt_date(new_until)), connect_kb(token))
    else:
        await send(
            texts.TRIAL_NO_KEY.format(date=fmt_date(new_until), owner=SUPPORT_BOT_USERNAME),
            main_menu(),
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
        await send_banner(
            message, texts.TRIAL_OFFER.format(days=TRIAL_DAYS), trial_consent_kb()
        )
    else:
        await send_banner(
            message,
            "🆓 Пробный период уже был использован.\n"
            "Оформите подписку — или получите бесплатные дни за друзей 🎁.",
            main_menu(),
        )


@dp.callback_query(F.data == "trial")
async def cb_trial(cq: CallbackQuery):
    if _trial_available(cq.from_user.id, cq.from_user.username):
        await show_screen(cq, 
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
    await show_screen(cq, texts.OFFER_TEXT, reply_markup=back_kb("trial"), disable_web_page_preview=True)
    await cq.answer()


@dp.callback_query(F.data == "trial_go")
async def cb_trial_go(cq: CallbackQuery):
    # пользователь нажал «Принимаю» — активируем пробный период
    async def send(text, kb):
        await show_screen(cq, text, reply_markup=kb)
    if await _give_trial(cq.from_user.id, cq.from_user.username, send):
        await cq.answer()
        if REFERRAL_ON_TRIAL:
            await reward_referrer(cq.bot, cq.from_user.id, reason="триал")
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
    await show_screen(cq, texts.WELCOME, reply_markup=main_menu())
    await cq.answer()


@dp.callback_query(F.data == "buy")
async def cb_buy(cq: CallbackQuery):
    # перед покупкой — оферта и согласие
    await show_screen(cq, texts.OFFER_INTRO, reply_markup=offer_consent_kb())
    await cq.answer()


@dp.callback_query(F.data == "offer_text")
async def cb_offer_text(cq: CallbackQuery):
    # текстовая оферта (запасной вариант, если мини-приложение не настроено)
    await show_screen(cq, texts.OFFER_TEXT, reply_markup=back_kb("buy"), disable_web_page_preview=True)
    await cq.answer()


@dp.callback_query(F.data == "plans")
async def cb_plans(cq: CallbackQuery):
    # пользователь принял оферту — показываем тарифы
    await show_screen(cq, texts.PLANS_INTRO, reply_markup=plans_kb())
    await cq.answer()


@dp.callback_query(F.data == "devices")
async def cb_devices(cq: CallbackQuery):
    await show_screen(cq, texts.DEVICES_INTRO, reply_markup=devices_kb())
    await cq.answer()


@dp.callback_query(F.data == "docs")
async def cb_docs(cq: CallbackQuery):
    await show_screen(cq, texts.DOCS, reply_markup=docs_kb())
    await cq.answer()


@dp.callback_query(F.data == "ref")
async def cb_ref(cq: CallbackQuery):
    await show_screen(cq, 
        ref_text(cq.from_user.id), reply_markup=back_kb("menu"), disable_web_page_preview=True
    )
    await cq.answer()


@dp.callback_query(F.data == "status")
async def cb_status(cq: CallbackQuery):
    await show_screen(cq, status_text(cq.from_user.id),
                      reply_markup=status_kb(cq.from_user.id))
    await cq.answer()


# ============ Промокоды ============
@dp.callback_query(F.data == "promo")
async def cb_promo(cq: CallbackQuery):
    await show_screen(cq, texts.PROMO_ENTER, reply_markup=back_kb())
    await cq.answer()


@dp.callback_query(F.data.startswith("promo_go:"))
async def cb_promo_go(cq: CallbackQuery):
    """Активация промокода по кнопке (из рассылки) — в одно нажатие."""
    code = cq.data.split(":", 1)[1]
    uid = cq.from_user.id
    db.create_user(uid, cq.from_user.username)
    bonus, err = db.redeem_promo(code, uid)
    if err == "already":
        await cq.answer("Вы уже активировали этот промокод ☝️", show_alert=True)
    elif err in ("not_found", "exhausted"):
        await cq.answer("Промокод больше недоступен 😔", show_alert=True)
    else:
        new_until = db.add_days(uid, bonus)
        token = sync_panel(uid)
        await show_screen(cq, texts.PROMO_OK.format(days=bonus, date=fmt_date(new_until)),
                          reply_markup=connect_kb(token))
        await cq.answer(f"+{bonus} дней! 🎉")


@dp.message(F.text & ~F.text.startswith("/"))
async def on_promo_text(message: Message):
    """Любой обычный текст трактуем как попытку ввести промокод."""
    code = (message.text or "").strip()
    # явно не промокод (фраза с пробелами или слишком длинно) — вернём в меню
    if not code or " " in code or len(code) > 32:
        await send_banner(message, texts.UNKNOWN_INPUT, main_menu())
        return
    uid = message.from_user.id
    db.create_user(uid, message.from_user.username)   # на случай без /start
    bonus, err = db.redeem_promo(code, uid)
    if err == "not_found":
        await send_banner(message, texts.PROMO_NOT_FOUND, main_menu())
    elif err == "already":
        await send_banner(message, texts.PROMO_ALREADY, main_menu())
    elif err == "exhausted":
        await send_banner(message, texts.PROMO_EXHAUSTED, main_menu())
    else:
        new_until = db.add_days(uid, bonus)
        token = sync_panel(uid)
        await send_banner(message,
            texts.PROMO_OK.format(days=bonus, date=fmt_date(new_until)),
            connect_kb(token))


# ============ Выбор тарифа и способа оплаты ============
async def _send_stars_invoice(cq: CallbackQuery, code, p):
    await cq.message.answer_invoice(
        title=f"IKK VPN — {p['title']}",
        description=f"Подписка на {p['title']} ({plan_days(code)} дней). Работает в v2RayTun.",
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
    if platega.is_configured() or lolz.can_invoice():
        # доступны два способа — даём выбрать
        await show_screen(cq, 
            texts.PAY_METHOD.format(title=p["title"], days=plan_days(code)),
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


# ============ Оплата картой/СБП (Platega или Lolz Merchant) ============
def card_provider():
    """Активный провайдер карты/СБП: Platega в приоритете, если настроена."""
    return "platega" if platega.is_configured() else "lolz"


@dp.callback_query(F.data.startswith("paycard:"))
async def cb_paycard(cq: CallbackQuery):
    # новый формат paycard:<провайдер>:<тариф>; старые кнопки — paycard:<тариф>
    parts = cq.data.split(":")
    if len(parts) == 3:
        provider, code = parts[1], parts[2]
    else:
        provider, code = card_provider(), parts[1]
    p = PLANS.get(code)
    if not p:
        await cq.answer("Тариф не найден", show_alert=True)
        return
    # если выбранная касса не настроена — берём доступную
    if provider == "platega" and not platega.is_configured():
        provider = "lolz"
    if provider == "lolz" and not lolz.can_invoice():
        provider = "platega" if platega.is_configured() else "lolz"
    db.create_user(cq.from_user.id, cq.from_user.username)
    try:
        # requests — блокирующий, уводим в поток, чтобы не тормозить бота
        if provider == "platega":
            payment_id, pay_url = await asyncio.to_thread(
                platega.create_payment,
                p["rub"],
                f"IKK VPN — подписка «{p['title']}» (Telegram)",
                f"https://t.me/{BOT_USERNAME}",   # после оплаты — назад в бот
                f"https://t.me/{BOT_USERNAME}",
                json.dumps({"tg": cq.from_user.id, "plan": code}),
            )
            invoice_id = "platega"
        else:
            payment_id = uuid.uuid4().hex
            invoice_id, pay_url = await asyncio.to_thread(
                lolz.create_invoice,
                p["rub"],
                payment_id,
                f"IKK VPN — подписка «{p['title']}» (Telegram)",
                f"https://t.me/{BOT_USERNAME}",   # после оплаты — назад в бот
                f"{SITE_URL}/pay/callback",       # вебхук уйдёт на сайт; бот опрашивает сам
            )
    except Exception:
        logging.exception("%s (бот): не удалось создать счёт (план %s)", provider, code)
        await cq.answer(texts.CARD_FAIL, show_alert=True)
        return
    db.create_bot_invoice(str(payment_id), str(invoice_id), cq.from_user.id,
                          code, p["rub"], provider=provider)
    await show_screen(cq,
        texts.CARD_INVOICE.format(rub=p["rub"], title=p["title"]),
        reply_markup=card_invoice_kb(pay_url, payment_id),
    )
    await cq.answer()


async def _check_card_status(rec):
    """Статус счёта у провайдера: 'paid' / 'pending' / 'dead'."""
    if rec["provider"] == "platega":
        st = await asyncio.to_thread(platega.get_status, rec["payment_id"])
        if st == platega.CONFIRMED:
            return "paid"
        if st in (platega.CANCELED, platega.CHARGEBACKED):
            return "dead"
        return "pending"
    inv = await asyncio.to_thread(lolz.get_invoice, rec["payment_id"])
    return "paid" if (inv and inv.get("status") == "paid") else "pending"


async def _credit_card_payment(bot, rec):
    """Зачисляет оплаченный счёт Lolz. True — если зачислили именно сейчас.

    Идемпотентно: статус в базе меняется одним UPDATE'ом со сверкой на
    pending, поэтому кнопка «Я оплатил» и фоновый опрос не задвоят дни.
    """
    if not db.settle_bot_invoice(rec["payment_id"], "paid"):
        return False
    p = PLANS.get(rec["plan"], {})
    days = plan_days(rec["plan"])
    uid = rec["user_id"]
    new_until = db.add_days(uid, days)
    db.record_payment(uid, rec["plan"], 0, f"lolz:{rec['payment_id']}")

    token = sync_panel(uid)
    sub_url = vpn_key(uid, token)
    if sub_url:
        text = texts.PAID_WITH_KEY.format(date=fmt_date(new_until))
    else:
        text = texts.PAID_NO_KEY.format(date=fmt_date(new_until), owner=SUPPORT_BOT_USERNAME)
    await reward_referrer(bot, uid, reason="оплату картой")
    try:
        kb = paid_kb(token) if sub_url else main_menu()
        await send_banner_to(bot, uid, text, kb)
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
        state = await _check_card_status(rec)
    except Exception:
        logging.exception("Бот: не удалось проверить счёт %s", payment_id)
        state = "pending"
    if state == "paid":
        await _credit_card_payment(cq.bot, rec)
        await cq.answer()
    elif state == "dead":
        db.settle_bot_invoice(rec["payment_id"], "expired")
        await cq.answer("Платёж отменён или истёк. Создайте новый счёт.", show_alert=True)
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
                    state = await _check_card_status(rec)
                except Exception:
                    continue                     # сеть/API упали — вернёмся через минуту
                if state == "paid":
                    await _credit_card_payment(bot, rec)
                elif state == "dead":
                    db.settle_bot_invoice(rec["payment_id"], "expired")
        except Exception:
            logging.exception("Бот: ошибка фоновой проверки счетов")


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
    days = plan_days(code)

    new_until = db.add_days(uid, days)
    db.record_payment(uid, code, sp.total_amount, sp.telegram_payment_charge_id)

    token = sync_panel(uid)
    sub_url = vpn_key(uid, token)
    await reward_referrer(message.bot, uid, reason="оплату звёздами")
    if sub_url:
        await send_banner(
            message,
            texts.PAID_WITH_KEY.format(date=fmt_date(new_until)),
            paid_kb(token),
        )
    else:
        await send_banner(
            message,
            texts.PAID_NO_KEY.format(date=fmt_date(new_until), owner=SUPPORT_BOT_USERNAME),
            main_menu(),
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


TRIAL_REMIND_BEFORE_DAYS = int(os.environ.get("TRIAL_REMIND_BEFORE_DAYS", "3"))
TRIAL_REMIND_INTERVAL = 3 * 3600   # как часто проверяем (раз в 3 часа)

# Рекламный промокод, создаётся автоматически при старте бота.
PROMO_CODE = os.environ.get("PROMO_CODE", "START7").upper()
PROMO_DAYS = int(os.environ.get("PROMO_DAYS", "7"))
PROMO_MAX_USES = int(os.environ.get("PROMO_MAX_USES", "0"))   # 0 = без лимита

# Промокод для рассылки «возвращайтесь» — отдельный, чтобы по счётчику
# активаций было видно отдачу именно от возвратной рассылки.
RETURN_PROMO_CODE = os.environ.get("RETURN_PROMO_CODE", "COMEBACK").upper()
RETURN_PROMO_DAYS = int(os.environ.get("RETURN_PROMO_DAYS", "5"))

# За сколько дней до конца ПЛАТНОЙ подписки напоминать о продлении
SUB_REMIND_BEFORE_DAYS = int(os.environ.get("SUB_REMIND_BEFORE_DAYS", "3"))
SUB_REMIND_INTERVAL = 6 * 3600

# Через сколько дней пользования просить порекомендовать сервис
ADVOCACY_AFTER_DAYS = int(os.environ.get("ADVOCACY_AFTER_DAYS", "10"))
ADVOCACY_INTERVAL = 12 * 3600


def _plural_days(n):
    """день / дня / дней по числу."""
    if 11 <= n % 100 <= 14:
        return "дней"
    d = n % 10
    return "день" if d == 1 else ("дня" if 2 <= d <= 4 else "дней")


async def remind_trial_ending(bot):
    """Авторассылка «пробный период заканчивается» с кнопкой подключения.

    Шлётся один раз каждому, у кого до конца триала осталось не больше
    TRIAL_REMIND_BEFORE_DAYS дней. Возвращает мёртвые триалы и подталкивает
    подключиться, пока доступ ещё бесплатный."""
    within = TRIAL_REMIND_BEFORE_DAYS * 86400
    while True:
        await asyncio.sleep(TRIAL_REMIND_INTERVAL)
        try:
            now = int(time.time())
            for uid, sub_until in db.trial_reminder_candidates(now, within):
                token = sync_panel(uid)
                connected = await asyncio.to_thread(user_connected, uid)
                if connected is None:
                    continue                      # панель недоступна — повторим позже
                if connected:
                    db.mark_trial_reminded(uid)   # уже пользуется — не беспокоим
                    continue
                days = max(1, round((sub_until - now) / 86400))
                text = texts.TRIAL_ENDING.format(
                    ending="ся" if days == 1 else "ось",
                    days=days, word=_plural_days(days))
                try:
                    await send_banner_to(bot, uid, text, connect_kb(token))
                except Exception:
                    pass                          # заблокировал бота / удалил чат
                db.mark_trial_reminded(uid)
        except Exception:
            logging.exception("Ошибка авторассылки напоминаний о триале")


async def remind_sub_ending(bot):
    """Напоминание о продлении ПЛАТНОЙ подписки за SUB_REMIND_BEFORE_DAYS дней.

    Продление — самый дешёвый рост: удержать платящего дешевле, чем найти
    нового. Флаг expiry_reminded сбрасывается в add_days(), поэтому после
    каждого продления напоминание сработает заново.
    """
    within = SUB_REMIND_BEFORE_DAYS * 86400
    while True:
        await asyncio.sleep(SUB_REMIND_INTERVAL)
        try:
            now = int(time.time())
            for uid, sub_until in db.expiry_reminder_candidates(now, within):
                days = max(1, round((sub_until - now) / 86400))
                text = texts.SUB_ENDING.format(
                    ending="ся" if days == 1 else "ось",
                    days=days, word=_plural_days(days), date=fmt_date(sub_until))
                try:
                    await send_banner_to(bot, uid, text, renew_kb())
                except TelegramForbiddenError:
                    db.mark_blocked(uid)
                except Exception:
                    logging.exception("Напоминание о продлении: сбой у %s", uid)
                db.mark_expiry_reminded(uid)
                await asyncio.sleep(0.1)
        except Exception:
            logging.exception("Ошибка авторассылки напоминаний о продлении")


async def ask_for_advocacy(bot):
    """Один раз просим довольного пользователя позвать друга.

    Момент выбран так, чтобы человек успел попользоваться (ADVOCACY_AFTER_DAYS)
    и подписка была ещё активна — просить рекомендацию у того, у кого VPN уже
    не работает, бессмысленно.
    """
    while True:
        await asyncio.sleep(ADVOCACY_INTERVAL)
        try:
            now = int(time.time())
            for uid in db.advocacy_candidates(now, ADVOCACY_AFTER_DAYS):
                link = f"https://t.me/{BOT_USERNAME}?start=ref_{uid}"
                text = texts.REF_ASK.format(days=ADVOCACY_AFTER_DAYS,
                                            bonus=REFERRAL_BONUS_DAYS, link=link)
                try:
                    await send_banner_to(bot, uid, text, ref_share_kb(link))
                except TelegramForbiddenError:
                    db.mark_blocked(uid)
                except Exception:
                    logging.exception("Просьба порекомендовать: сбой у %s", uid)
                db.mark_ref_asked(uid)
                await asyncio.sleep(0.1)
        except Exception:
            logging.exception("Ошибка авторассылки просьб порекомендовать")


async def main():
    if not BOT_TOKEN:
        raise SystemExit("Ошибка: задайте переменную окружения BOT_TOKEN (токен от @BotFather).")
    db.init_db()
    bot = Bot(BOT_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))

    # в меню слева от поля ввода — только «старт»; остальная навигация
    # кнопками в сообщении (команды /buy и т.д. работают, если их ввести)
    await bot.set_my_commands([BotCommand(command="start", description="Главное меню")])
    await bot.set_chat_menu_button(menu_button=MenuButtonCommands())

    if platega.is_configured() or lolz.can_invoice():
        asyncio.create_task(poll_card_invoices(bot))
        logging.info("Карта/СБП включена, провайдер: %s (фоновая проверка счетов)",
                     card_provider())
    else:
        logging.info("Касса не настроена (Platega/Lolz) — в боте только звёзды")

    db.create_promo(PROMO_CODE, PROMO_DAYS, PROMO_MAX_USES)
    db.create_promo(RETURN_PROMO_CODE, RETURN_PROMO_DAYS, 0)
    logging.info("Промокоды готовы: %s (+%s дн.), %s (+%s дн., для /broadcast_lapsed)",
                 PROMO_CODE, PROMO_DAYS, RETURN_PROMO_CODE, RETURN_PROMO_DAYS)

    asyncio.create_task(remind_trial_ending(bot))
    asyncio.create_task(remind_sub_ending(bot))
    asyncio.create_task(ask_for_advocacy(bot))
    logging.info("Авторассылки включены: триал за %s дн., продление за %s дн., "
                 "просьба порекомендовать через %s дн.",
                 TRIAL_REMIND_BEFORE_DAYS, SUB_REMIND_BEFORE_DAYS,
                 ADVOCACY_AFTER_DAYS)

    logging.info("IKK VPN bot запущен")
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
