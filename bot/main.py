"""IKK VPN — Telegram-бот: оплата звёздами и картой/СБП, подписки, рефералы."""
import asyncio
import contextvars
import json
import logging
import os
import random
import re
import time
import uuid
from datetime import datetime
from html import escape as html_escape
from pathlib import Path

from aiogram import Bot, Dispatcher, F
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.exceptions import (TelegramBadRequest,
                                TelegramForbiddenError,
                                TelegramRetryAfter)
from aiogram.filters import Command, CommandStart
from aiogram.types import (
    BotCommand,
    CallbackQuery,
    FSInputFile,
    InlineKeyboardMarkup,
    InputMediaAnimation,
    InputMediaPhoto,
    InputRichMessage,
    InputRichMessageMedia,
    LabeledPrice,
    MenuButtonCommands,
    Message,
    PreCheckoutQuery,
)

import lolz
import platega

from . import alerts, db, texts
from .config import (
    BOT_TOKEN,
    CONTACT_USERNAME,
    BOT_USERNAME,
    OWNER_ID,
    PLANS,
    PROMO_ON_TRIAL,
    REFERRAL_BONUS_DAYS,
    TRIAL_LIMIT_SINCE,
    REFERRAL_ON_TRIAL,
    SALE_BONUS,
    SALE_UNTIL,
    SITE_URL,
    SUPPORT_BOT_USERNAME,
    TRIAL_DAYS,
    GIVEAWAY_CHANNEL,
    GIVEAWAY_MAX_ENTRIES,
    GIVEAWAY_MIN_MINUTES,
    GIVEAWAY_PRIZE,
    GIVEAWAY_TAG,
    GIVEAWAY_UNTIL,
    giveaway_active,
    plan_days,
    sale_active,
    sale_bonus,
)
from .keyboards import (BROADCASTS, admin_back_kb, admin_broadcasts_kb,
                        admin_confirm_kb, admin_giveaway_kb, admin_kb,
                        admin_preview_kb,
                        back_kb, card_invoice_kb, connect_kb, devices_kb,
                        docs_kb, giveaway_kb, giveaway_post_kb, main_menu,
                        support_kb,
                        offer_consent_kb, paid_kb,
                        pay_method_kb, plans_kb, promo_offer_kb, ref_share_kb,
                        renew_kb, sale_kb, trial_consent_kb)
from .panel import (DATA_LIMIT_GB, TRIAL_DATA_LIMIT_GB, get_subscription_url,
                    online_usernames, site_sub_url, sub_token,
                    traffic_by_username, user_connected)

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
# Анимированный вариант того же баннера. Уходит только в богатых сообщениях:
# подпись к фото анимацию не покажет.
BANNER_ANIM = Path(__file__).parent / "assets" / "banner.mp4"
# file_id баннера внутри богатых сообщений — у фото с подписью он другой
_rich_banner_ids = {}

# Лимит подписи к фото у Telegram. У обычного сообщения он 4096, поэтому
# редкий длинный текст отправляем без баннера, а не теряем совсем.
CAPTION_LIMIT = 1024

# Богатые сообщения (Bot API 10.1): баннер блоком сверху, под ним заголовок,
# тонкие разделители и абзацы — тот же текст из texts.py, разложенный по
# секциям, — и цветные кнопки. С 14.09.2026 это основное оформление для
# всех, баннер анимированный. Старое (фото с подписью) осталось только
# запасным путём: если Telegram отвергнет разметку, сообщение уйдёт им —
# доставка важнее оформления. Вернуть старое для всех — BOT_RICH_MESSAGES=0,
# статичный баннер — BOT_BANNER_ANIM=0.
RICH_MESSAGES = os.environ.get("BOT_RICH_MESSAGES", "1") == "1"
BANNER_ANIMATED = os.environ.get("BOT_BANNER_ANIM", "1") == "1"
_rich_disabled = False      # Telegram отверг разметку — до перезапуска не пробуем

_TAGS = re.compile(r"<[^>]+>")


def _short_line(part):
    return "\n" not in part and len(_TAGS.sub("", part)) <= 60


# Пустая строка. Отступов у блоков богатого сообщения нет, а текст,
# прижатый к разделителю, выглядит тесно, — воздух даём строкой.
# Символ — пустая клетка Брайля (U+2800): невидим, но пробелом не считается.
# Пробел нулевой ширины (U+200B) Telegram срезал вместе со строкой —
# проверено на живом сообщении 14.09.2026.
_GAP = "\u2800"


def _unbold(part):
    return re.sub(r"</?b>", "", part)


def rich_html(text):
    """Раскладывает текст бота по блокам богатого сообщения, не меняя слов.

    Тексты в texts.py устроены одинаково: первая строка — заголовок, дальше
    абзацы через пустую строку, в конце короткий призыв («Выберите
    действие:»). Заголовок становится заголовком секции, между ним, телом
    и призывом встают разделители, а тело и призыв идут жирным. Внутренние
    <b> снимаются: жирный внутри жирного Telegram не различает.
    """
    parts = [p.strip() for p in text.strip().split("\n\n") if p.strip()]
    blocks = []
    has_top = len(parts) > 1 and _short_line(parts[0])
    if has_top:
        blocks.append(f"<h3>{_unbold(parts.pop(0))}</h3><hr/>")
    tail = parts.pop() if len(parts) > 1 and _short_line(parts[-1]) else None
    body = [_unbold(p).replace("\n", "<br/>") for p in parts]
    if body and has_top:
        body[0] = f"{_GAP}<br/>{body[0]}"         # отступ от верхнего разделителя
    if body and tail:
        body[-1] = f"{body[-1]}<br/>{_GAP}"       # и от нижнего
    blocks += [f"<p><b>{b}</b></p>" for b in body]
    if tail:
        blocks.append(f"<hr/><p><b>{_unbold(tail)}</b></p>")
    return "".join(blocks)


def _rich_message(text, animated=None):
    """Богатое сообщение: баннер блоком сверху, текст по секциям."""
    animated = BANNER_ANIMATED if animated is None else animated
    path = BANNER_ANIM if animated and BANNER_ANIM.exists() else BANNER
    if not path.exists():
        return InputRichMessage(html=rich_html(text))
    kind = "animation" if path == BANNER_ANIM else "photo"
    src = _rich_banner_ids.get(kind) or FSInputFile(path)
    if kind == "animation":
        tag = '<video src="tg://video?id=banner"></video>'
        media = InputMediaAnimation(media=src)
    else:
        tag = '<img src="tg://photo?id=banner"/>'
        media = InputMediaPhoto(media=src)
    return InputRichMessage(html=tag + rich_html(text),
                            media=[InputRichMessageMedia(id="banner", media=media)])


def _remember_banner(msg):
    """Запоминает file_id баннера из богатого сообщения — второй раз не грузим."""
    rich = getattr(msg, "rich_message", None)
    for block in (rich.blocks if rich else []):
        if getattr(block, "animation", None):
            _rich_banner_ids.setdefault("animation", block.animation.file_id)
        elif getattr(block, "photo", None):
            _rich_banner_ids.setdefault("photo", block.photo[-1].file_id)


def _has_animation(msg):
    rich = getattr(msg, "rich_message", None)
    return any(getattr(b, "animation", None) for b in (rich.blocks if rich else []))


# Главные действия — зелёные, остальное синее, «◀ Назад» без цвета: так
# навигация не спорит за внимание с тем, ради чего экран открыт.
_GREEN = {"buy", "trial", "trial_go", "renew"}


def paint(markup):
    """Цветные кнопки (поле style). Уже заданный цвет не трогаем."""
    if not isinstance(markup, InlineKeyboardMarkup):
        return markup
    for row in markup.inline_keyboard:
        for b in row:
            if b.style or b.text.startswith("◀"):
                continue
            data = b.callback_data or ""
            green = data in _GREEN or data.startswith("pay") or b.pay
            b.style = "success" if green else "primary"
    return markup


async def _send_rich(bot, chat_id, text, reply_markup=None, animated=None):
    """True — ушло богатым сообщением; False — слать по-старому."""
    global _rich_disabled
    try:
        sent = await bot.send_rich_message(
            chat_id=chat_id, rich_message=_rich_message(text, animated),
            reply_markup=paint(reply_markup))
    except TelegramForbiddenError:
        raise
    except TelegramBadRequest as e:
        # Разметку не приняли — у следующего не примут так же. Выключаем
        # до перезапуска и говорим владельцу один раз, а не на каждое сообщение.
        if "pars" in str(e).lower() or "rich" in str(e).lower():
            _rich_disabled = True
            await alert("Богатые сообщения выключены до перезапуска", e, chat_id)
        else:
            logging.warning("Богатое сообщение не ушло %s: %s", chat_id, e)
        return False
    except Exception:
        logging.exception("Богатое сообщение не ушло — шлём по-старому")
        return False
    _remember_banner(sent)
    return sent


async def send_banner_to(bot, chat_id, text, reply_markup=None):
    """Отправляет в чат НОВОЕ сообщение с баннером IKK VPN сверху."""
    if RICH_MESSAGES and not _rich_disabled:
        sent = await _send_rich(bot, chat_id, text, reply_markup)
        if sent:
            return sent
    return await _send_classic(bot, chat_id, text, reply_markup)


# Письмо: фоновые напоминания. Уходят тем, кто давно не открывал бота, —
# у них чаще старое приложение, которое богатое сообщение не покажет
# («не поддерживается вашей версией»). Поэтому письмо идёт проверенным
# способом — баннер с подписью, — а красоту даёт сама вёрстка: тема жирным,
# текст курсивом, подпись команды. Рассылки с 14.09.2026 идут в основном
# оформлении (send_banner_to) — так решил владелец.
LETTER_SIGN = "С заботой о вашем интернете,\nкоманда IKK VPN"
_letter_anim_id = None


def letter_html(text):
    """Оформляет текст бота как письмо, не меняя слов.

    Первая короткая строка — тема (жирным), абзацы — курсивом, выделенное
    внутри остаётся жирным курсивом. Вложенный <i> снимается: курсив внутри
    курсива Telegram не различает.
    """
    parts = [p.strip() for p in text.strip().split("\n\n") if p.strip()]
    out = []
    if len(parts) > 1 and _short_line(parts[0]):
        out.append(f"<b>{_unbold(parts.pop(0))}</b>")
    out += ["<i>" + re.sub(r"</?i>", "", p) + "</i>" for p in parts]
    out.append(f"<i>— {LETTER_SIGN}</i>")
    return "\n\n".join(out)


async def send_letter_to(bot, chat_id, text, reply_markup=None):
    """Отправляет письмо: баннер (анимацией, если включена) и текст-письмо."""
    global _letter_anim_id
    text, reply_markup = letter_html(text), paint(reply_markup)
    if BANNER_ANIMATED and BANNER_ANIM.exists() and len(text) <= CAPTION_LIMIT:
        try:
            sent = await bot.send_animation(chat_id, _letter_anim_id or FSInputFile(BANNER_ANIM),
                                            caption=text, reply_markup=reply_markup)
            if not _letter_anim_id and sent.animation:
                _letter_anim_id = sent.animation.file_id
            return sent
        except TelegramForbiddenError:
            raise
        except Exception:
            logging.exception("Письмо с анимацией не ушло — шлём с картинкой")
    return await _send_classic(bot, chat_id, text, reply_markup)


async def _send_classic(bot, chat_id, text, reply_markup=None):
    """Фото с подписью — понимает любая версия Telegram."""
    global _banner_file_id
    if BANNER.exists() and len(text) <= CAPTION_LIMIT:
        try:
            photo = _banner_file_id or FSInputFile(BANNER)
            sent = await bot.send_photo(chat_id, photo, caption=text,
                                        reply_markup=reply_markup)
            if not _banner_file_id:
                _banner_file_id = sent.photo[-1].file_id
            return sent
        except TelegramForbiddenError:
            # чат недоступен (бот заблокирован) — текстом тоже не дойдёт,
            # второй запрос только зря нагружает API и засоряет журнал
            raise
        except Exception:
            logging.exception("Баннер не отправился — шлём текстом")
    return await bot.send_message(chat_id, text, reply_markup=reply_markup)


# Сколько живёт сообщение рассылки. Сутки: за это время его увидят даже те,
# кто заходит в Telegram раз в день, а переписка не превращается в ленту
# рекламы. 0 — не убирать. Больше суток ставить нельзя: Telegram разрешает
# боту удалять свои сообщения только первые 48 часов.
BROADCAST_TTL = int(os.environ.get("BROADCAST_TTL", str(24 * 3600)))
CLEANUP_INTERVAL = 600


def _left(seconds):
    """«3 ч 20 мин» — сколько осталось."""
    seconds = max(0, int(seconds))
    h, m = seconds // 3600, seconds % 3600 // 60
    if h and m:
        return f"{h} ч {m} мин"
    return f"{h} ч" if h else f"{m} мин"


def _cleanup_line():
    """Когда уберутся только что отправленные сообщения."""
    when = datetime.fromtimestamp(time.time() + BROADCAST_TTL)
    return (f"Уберу из переписки через {_left(BROADCAST_TTL)} — "
            f"{when.strftime('%d.%m в %H:%M')}. "
            "Те, кто нажмёт кнопку, сообщение сохранят.")


def _cleanup_text():
    """Экран «Уборка рассылок» в панели: сколько ждёт и сколько осталось."""
    n, first, last = db.deletes_summary()
    if not n:
        return ("🧹 <b>Уборка рассылок</b>\n\n"
                "Очередь пуста — убирать нечего.\n\n"
                f"<i>Срок жизни рассылки: {_left(BROADCAST_TTL)}. "
                "Сообщения, которые человек открыл кнопкой, не удаляются.</i>")
    now = int(time.time())
    return ("🧹 <b>Уборка рассылок</b>\n\n"
            f"Ждут удаления: <b>{n}</b>\n"
            f"Ближайшее — через <b>{_left(first - now)}</b> "
            f"({datetime.fromtimestamp(first).strftime('%d.%m в %H:%M')})\n"
            f"Последнее — через <b>{_left(last - now)}</b> "
            f"({datetime.fromtimestamp(last).strftime('%d.%m в %H:%M')})\n\n"
            "<i>Считаются только нетронутые: если человек нажал кнопку, "
            "сообщение остаётся у него как рабочий экран.</i>")


def _plan_cleanup(chat_id, msg):
    """Ставит сообщение рассылки в очередь на удаление через сутки."""
    if BROADCAST_TTL and msg is not None and getattr(msg, "message_id", None):
        db.schedule_delete(chat_id, msg.message_id, int(time.time()) + BROADCAST_TTL)


async def clean_old_broadcasts(bot):
    """Убирает старые рассылки, которые человек так и не открыл."""
    while True:
        await asyncio.sleep(CLEANUP_INTERVAL)
        try:
            for chat_id, message_id in db.due_deletes(int(time.time())):
                try:
                    await bot.delete_message(chat_id, message_id)
                except Exception:
                    # уже удалено, бот заблокирован или прошло 48 часов —
                    # повторять нечего, просто снимаем с очереди
                    pass
                db.cancel_delete(chat_id, message_id)
                await asyncio.sleep(0.05)
        except Exception:
            logging.exception("Бот: ошибка уборки старых рассылок")


# Рассылки заканчиваются так же, как главное меню: разделителем и
# «Выберите действие:». Отдельной разметки не нужно — rich_html() сам
# превращает короткую последнюю строку в призыв под разделителем.
BROADCAST_CTA = "Выберите действие:"


def with_cta(text):
    text = text.rstrip()
    return text if text.endswith(BROADCAST_CTA) else f"{text}\n\n{BROADCAST_CTA}"


async def broadcast(message: Message, users, make_message):
    """Общая механика рассылок: отправка, подсчёт, отчёт владельцу.

    make_message(uid) -> (text, клавиатура) — у рефералки текст свой на
    каждого. Заблокировавших помечаем в базе, чтобы следующая рассылка их
    не трогала и статистика не врала.
    """
    if _PREVIEW.get():
        # Письмо собирается тем же make_message, но уходит только в чат
        # владельца: ссылка внутри — его собственная.
        text, kb = make_message(OWNER_ID)
        await send_banner_to(message.bot, message.chat.id, with_cta(text), kb)
        await message.answer(f"Пример письма — выше. Настоящая рассылка ушла бы "
                             f"<b>{len(users)}</b> получателям.")
        return
    sent = blocked = failed = 0
    for uid in users:
        text, kb = make_message(uid)
        try:
            msg = await send_banner_to(message.bot, uid, with_cta(text), kb)
            _plan_cleanup(uid, msg)
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
    if BROADCAST_TTL and sent:
        report += f"\n\n🧹 {_cleanup_line()}"
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
    db.cancel_delete(m.chat.id, m.message_id)   # открыли — это рабочий экран
    if getattr(m, "rich_message", None):
        # Баннер оставляем того же вида, что был у сообщения, — иначе
        # картинка сменится на анимацию прямо по нажатию кнопки.
        _remember_banner(m)
        try:
            await cq.bot.edit_message_text(
                chat_id=m.chat.id, message_id=m.message_id,
                rich_message=_rich_message(text, _has_animation(m)),
                reply_markup=paint(reply_markup))
        except TelegramBadRequest as e:
            if "not modified" not in str(e):
                raise
        return
    if m.photo or m.animation:
        await m.edit_caption(caption=text, reply_markup=reply_markup)
    else:
        await m.edit_text(text, reply_markup=reply_markup, **kwargs)


def ref_text(uid):
    """Экран рефералов.

    Раньше «заработано» считалось как переходы × бонус, хотя дни идут
    только за оплативших. Человек с 29 переходами видел «290 дней»,
    которых у него не было, — и приходил за ними. Теперь показываем
    факт: сколько пришло и за скольких начислено.
    """
    link = f"https://t.me/{BOT_USERNAME}?start=ref_{uid}"
    n = db.count_referrals(uid)
    paid = db.count_paid_referrals(uid)
    return texts.REF.format(link=link, days=REFERRAL_BONUS_DAYS,
                            count=n, paid=paid, earned=paid * REFERRAL_BONUS_DAYS)


def _full_limit(uid, u):
    """Положен ли человеку полный лимит трафика.

    Полный — платившим и тем, кто пришёл до введения урезанного лимита:
    у них в панели уже стоит 200 ГБ и часть израсходована, а снижение
    отключило бы их мгновенно и без предупреждения.
    """
    if db.has_paid(uid):
        return True
    return bool(TRIAL_LIMIT_SINCE and (u["created_at"] or 0) < TRIAL_LIMIT_SINCE)


def _promo_too_early(uid):
    """Промокод поверх идущего бесплатного периода — нельзя (PROMO_ON_TRIAL).

    Пробный период и дни промокода складывались, и человек оказывался с
    месяцем бесплатного доступа: решение о покупке отодвигалось так далеко,
    что до него не доходили.
    """
    if PROMO_ON_TRIAL:
        return False
    u = db.get_user(uid)
    return bool(u and u["sub_until"] > int(time.time()) and not db.has_paid(uid))


def sync_panel(uid):
    """Синхронизирует срок подписки с панелью и возвращает её токен.

    Один вызов на все ссылки: обращение к панели идёт с логином, поэтому
    дёргать её отдельно ради ключа и ради deep link — двойная работа.
    """
    u = db.get_user(uid)
    if not (u and u["sub_until"]):
        return None
    # Бесплатному — урезанный лимит, платящему — полный. Лимит уходит в
    # панель при каждой синхронизации, поэтому после первой оплаты он
    # поднимается сам, без отдельного действия.
    limit = None if _full_limit(uid, u) else TRIAL_DATA_LIMIT_GB
    token = sub_token(get_subscription_url(uid, u["sub_until"], data_limit_gb=limit))
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

    # Пришёл по ссылке из рекламы — показываем сразу то, за чем шёл, а не
    # общее приветствие. Человек кликнул из поста про подарок и ждёт увидеть
    # подарок; обычное меню он читать не станет и уйдёт.
    tag = _clean_source(param)

    # Кнопка «Пригласи друга и получи подарок» — сразу личная ссылка
    if tag == "gift":
        await send_banner(message, _gift_text(uid), ref_share_kb(_ref_link(uid)))
        return

    if giveaway_active() and tag in {GIVEAWAY_TAG, "giveaway"}:
        # Показываем УСЛОВИЯ, а не результат проверки. Человек только что
        # пришёл по ссылке из поста; встретить его отказом «ещё рано» или
        # «нет ключа» — верный способ потерять. Проверка сработает, когда
        # он сам нажмёт кнопку.
        u = db.get_user(uid)
        await send_banner(
            message,
            texts.GIVEAWAY_INTRO.format(
                prize=GIVEAWAY_PRIZE, channel=GIVEAWAY_CHANNEL,
                until=GIVEAWAY_UNTIL or "скоро", days=TRIAL_DAYS,
                limit=GIVEAWAY_MAX_ENTRIES),
            giveaway_kb(need_key=not (u and u["sub_until"]), need_sub=True))
        return

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
    text = texts.REF_BONUS.format(days=REFERRAL_BONUS_DAYS, date=fmt_date(new_until))
    # Звёзды обещаны за оплату — триал (REFERRAL_ON_TRIAL) их не даёт
    paid = bool(REF_PAID_STARS) and reason != "триал"
    if paid:
        text += texts.REF_PAID_EARNED.format(stars=REF_PAID_STARS)
    try:
        await send_banner_to(bot, ref_id, text, connect_kb(token))
    except TelegramForbiddenError:
        db.mark_blocked(ref_id)
    except Exception:
        logging.exception("Реферал: бонус начислен, но %s не уведомлён", ref_id)
    if paid:
        await _notify_paid_referral(bot, ref_id, uid, reason)


async def _notify_paid_referral(bot, ref_id, uid, reason):
    """Владельцу: кому вручить звёзды за приглашённого.

    Выдача ручная, поэтому здесь всё, что нужно для решения: кто привёл,
    кого, как тот заплатил и давно ли он в боте. Свежий аккаунт, оплативший
    сразу после пригласившего, — повод присмотреться: 100 ⭐ стоят дороже
    месяца подписки, и второй аккаунт окупается.
    """
    if not OWNER_ID:
        return

    def who(x):
        u = db.get_user(x)
        name = f"@{u['username']}" if u and u["username"] else f"id{x}"
        return f'<a href="tg://user?id={x}">{name}</a>', u

    ref, _ = who(ref_id)
    inv, u = who(uid)
    since = fmt_date(u["created_at"]) if u and u["created_at"] else "?"
    try:
        await bot.send_message(
            OWNER_ID,
            f"💸 <b>Реферал оплатил</b>\n\n"
            f"Пригласил: {ref}\n"
            f"Оплатил: {inv} {reason.replace('оплату ', '')}, в боте с {since}\n\n"
            f"Вручить <b>{REF_PAID_STARS} ⭐</b> подарком Telegram тому, кто "
            f"пригласил. Перед выдачей проверьте, что это не второй аккаунт "
            f"того же человека.")
    except Exception:
        logging.exception("Реферал: не смог сообщить владельцу про звёзды для %s", ref_id)


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
    await message.answer(_sources_text())


def _sources_text():
    """Отчёт «откуда приходят». Вынесен из команды, потому что тот же текст
    показывает кнопка в панели — иначе два места разъедутся при правке."""
    rows = db.source_stats()
    if not rows:
        return "Пользователей пока нет."
    lines = ["🚦 <b>Откуда приходят</b>\n",
             "<code>источник      всего  ключ  опл.</code>"]
    for src, total, activated, paid in rows:
        lines.append(f"<code>{src[:12]:<12} {total:>5} {activated:>5} {paid:>5}</code>")
    lines.append("\n<b>ключ</b> — активировали подписку (триал или оплата)")
    lines.append("<b>опл.</b> — заплатили хотя бы раз")
    lines.append(f"\nСсылка с меткой: <code>https://t.me/{BOT_USERNAME}?start=yt</code>")
    return "\n".join(lines)


@dp.message(Command("broadcast_nc"))
async def cmd_broadcast_nc(message: Message):
    """Разовая рассылка «вы ещё не подключились» — всем активным, кто ни разу
    не выходил в сеть. Только для владельца (OWNER_ID). Работает внутри бота,
    поэтому все настройки (панель, база) уже на месте."""
    if not OWNER_ID or message.from_user.id != OWNER_ID:
        return
    await _bc_nc(message)


# Порог «почти не пользовался», МБ. Меньше — хватило на рукопожатие и пару
# попыток, но не на работу. Больше — человек уже пользуется, и вопрос
# «всё ли работает» будет выглядеть странно.
TINY_TRAFFIC_MB = int(os.environ.get("TINY_TRAFFIC_MB", "100"))


async def _bc_howsitgoing(message):
    """Вопрос тем, кто подключился, но израсходовал считанные килобайты.

    Три недели гадали, что с этой группой: поломка, потеря интереса или
    тихое отключение VPN. Все технические версии проверены и не
    подтвердились — остаётся спросить самих людей.

    Рассылка ничего не продаёт: продажа превратит честный вопрос в
    рекламу, и отвечать перестанут.
    """
    traffic = await asyncio.to_thread(traffic_by_username)
    if traffic is None:
        await message.answer("Панель недоступна — расход трафика не узнать. "
                             "Попробуйте позже.")
        return

    limit = TINY_TRAFFIC_MB * 1024 * 1024
    users = []
    for uid, _ in db.active_users(int(time.time())):
        used = traffic.get(f"ikk_{uid}")
        if used and used < limit:     # подключался, но почти не пользовался
            users.append(uid)

    if not users:
        await message.answer(
            "Некому слать: либо ни у кого нет подключений, либо у всех "
            f"расход больше {TINY_TRAFFIC_MB} МБ.")
        return

    await message.answer(
        f"⏳ Спрашиваю «всё ли работает» у {len(users)} чел. — это те, кто "
        f"подключился, но израсходовал меньше {TINY_TRAFFIC_MB} МБ.\n\n"
        f"Ответы придут вам в личку — @{CONTACT_USERNAME}.")

    text = texts.HOWS_IT_GOING.format(support=f"@{CONTACT_USERNAME}")
    kb = support_kb()
    await broadcast(message, users, lambda uid: (text, kb))


async def _bc_nc(message):
    now = int(time.time())
    users = db.active_users(now)
    if _PREVIEW.get():
        # Без обхода панели по всем подпискам — это сотни запросов ради примера
        text = texts.NOT_CONNECTED_NUDGE.format(date=fmt_date(now + 7 * 86400))
        await send_banner_to(message.bot, message.chat.id, with_cta(text), connect_kb(PREVIEW_TOKEN))
        await message.answer(
            f"Пример письма — выше. Настоящая рассылка проверит {len(users)} "
            "активных подписок и напишет тем, кто ни разу не подключался.")
        return
    await message.answer(f"⏳ Проверяю {len(users)} активных подписок…")
    # «Уже пользуется» и «панель не ответила» — разные вещи, и в одном
    # счётчике они читаются как «не дошло». Первое — норма, этому человеку
    # писать нечего; второе — сбой, и его надо повторить.
    sent = already = unknown = blocked = failed = 0
    for uid, sub_until in users:
        connected = await asyncio.to_thread(user_connected, uid)
        if connected:
            already += 1                       # подключался — писать нечего
            continue
        if connected is None:
            unknown += 1                       # панель не ответила
            continue
        token = sync_panel(uid)
        text = texts.NOT_CONNECTED_NUDGE.format(date=fmt_date(sub_until))
        try:
            msg = await send_banner_to(message.bot, uid, with_cta(text), connect_kb(token))
            _plan_cleanup(uid, msg)
            sent += 1
        except TelegramForbiddenError:
            db.mark_blocked(uid)
            blocked += 1
        except Exception:
            logging.exception("Рассылка: сбой у пользователя %s", uid)
            failed += 1
        await asyncio.sleep(0.1)
    report = [
        "✅ <b>Готово.</b>", "",
        f"<b>{sent}</b> — отправлено (не подключались)",
        f"<b>{already}</b> — пропущено: уже пользуются VPN, писать нечего",
    ]
    if unknown:
        report.append(f"⚠️ <b>{unknown}</b> — панель не ответила, "
                      "статус неизвестен. Стоит повторить рассылку")
    if blocked:
        report.append(f"<b>{blocked}</b> — заблокировали бота")
    if failed:
        report.append(f"<b>{failed}</b> — прочие ошибки, причина в журнале")
    report += ["", f"<i>Проверено подписок: {len(users)}</i>"]
    await message.answer("\n".join(report))


@dp.message(Command("broadcast_promo"))
async def cmd_broadcast_promo(message: Message):
    """Рассылка про промокод ВСЕМ пользователям бота (owner-only).
    Каждому — баннер, текст и кнопка активации в одно нажатие."""
    if not OWNER_ID or message.from_user.id != OWNER_ID:
        return
    await _bc_promo(message)


async def _bc_promo(message):
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
    await _bc_sale(message)


async def _bc_sale(message):
    if not sale_active():
        await message.answer(
            f"⛔ Акция закончилась {_sale_until_ru()} — рассылка отменена.\n"
            f"Чтобы продлить, поменяйте SALE_UNTIL в bot/config.py.")
        return

    users = db.all_user_ids()
    await message.answer(
        f"⏳ Рассылаю акцию (год +{sale_bonus('12m')} дн., 3 месяца "
        f"+{sale_bonus('3m')}, месяц +{sale_bonus('1m')}, по {_sale_until_ru()}) "
        f"по {len(users)} пользователям…")
    text = texts.SALE_BROADCAST.format(until=_sale_until_ru(), **_sale_plans_kwargs())
    kb = sale_kb()
    await broadcast(message, users, lambda uid: (text, kb))


@dp.message(Command("broadcast_sale_more"))
async def cmd_broadcast_sale_more(message: Message):
    """Рассылка «бонус стал больше» ВСЕМ пользователям (owner-only)."""
    if not OWNER_ID or message.from_user.id != OWNER_ID:
        return
    await _bc_sale_more(message)


async def _bc_sale_more(message):
    """Повторный заход по той же акции — когда бонус увеличили.

    Числа берутся из тех же функций, что и начисление, поэтому «40 дней»
    в тексте и 40 дней при оплате разойтись не могут.
    """
    if not sale_active():
        await message.answer(
            f"⛔ Акция закончилась {_sale_until_ru()} — рассылка отменена.\n"
            "Продлите её переменной SALE_UNTIL и перезапустите бота.")
        return

    users = db.all_user_ids()
    await message.answer(
        f"⏳ Рассылаю «бонус стал больше» (месяц {plan_days('1m')} дн., "
        f"по {_sale_until_ru()}) по {len(users)} пользователям…")
    text = texts.SALE_MORE_BROADCAST.format(until=_sale_until_ru(),
                                            **_sale_plans_kwargs())
    kb = sale_kb()
    await broadcast(message, users, lambda uid: (text, kb))


@dp.message(Command("broadcast_ref"))
async def cmd_broadcast_ref(message: Message):
    """Рассылка про реферальную программу ВСЕМ пользователям (owner-only).
    Ссылка у каждого своя, поэтому текст и кнопки собираем на каждого."""
    if not OWNER_ID or message.from_user.id != OWNER_ID:
        return
    await _bc_ref(message)


async def _bc_ref(message):
    users = db.all_user_ids()
    await message.answer(
        f"⏳ Рассылаю про рефералы (+{REFERRAL_BONUS_DAYS} дн.) "
        f"по {len(users)} пользователям…")
    def make(uid):
        link = f"https://t.me/{BOT_USERNAME}?start=ref_{uid}"
        return (texts.REF_BROADCAST.format(days=REFERRAL_BONUS_DAYS, link=link),
                ref_share_kb(link))

    await broadcast(message, users, make)


# Номинал подарка за приглашение. Выдаётся ВРУЧНУЮ по скриншоту: за
# автоматическую выдачу человек заведёт второй аккаунт и перейдёт по
# своей же ссылке за минуту, а подарок стоит настоящих денег.
REF_GIFT_STARS = int(os.environ.get("REF_GIFT_STARS", "25"))


def _ref_link(uid):
    return f"https://t.me/{BOT_USERNAME}?start=ref_{uid}"


def _gift_text(uid):
    """Экран акции с личной ссылкой. Тот же текст, что в рассылке:
    человек может прийти и оттуда, и по кнопке из поста — условия
    должны совпадать слово в слово, иначе будут вопросы."""
    return texts.REF_GIFT_BROADCAST.format(
        stars=REF_GIFT_STARS, days=REFERRAL_BONUS_DAYS,
        support=f"@{SUPPORT_BOT_USERNAME}", link=_ref_link(uid))


async def _bc_gift(message):
    """Акция «подарок за приглашение друга».

    Ссылка у каждого своя, поэтому текст собирается на каждого отдельно.
    Скриншот человек присылает в бот поддержки — там уже есть тикеты,
    и обращение придёт владельцу с возможностью ответить.
    """
    users = db.all_user_ids()
    await message.answer(
        f"⏳ Рассылаю про подарок ({REF_GIFT_STARS} ⭐) "
        f"по {len(users)} пользователям…\n\n"
        f"Скриншоты придут в @{SUPPORT_BOT_USERNAME} тикетами.")

    def make(uid):
        return _gift_text(uid), ref_share_kb(_ref_link(uid))

    await broadcast(message, users, make)


# Звёзды за приглашённого, который ОПЛАТИЛ. 0 — акция выключена: рассылка
# не уходит, и никому ничего не обещается. Вручаются тоже вручную, подарком
# Telegram: переводить звёзды пользователю бот не умеет, а автоматическая
# выдача превратила бы акцию в обмен — месяц со второго аккаунта стоит
# 50 ₽, а подарок на 100 ⭐ дороже.
REF_PAID_STARS = int(os.environ.get("REF_PAID_STARS", "0"))


@dp.message(Command("broadcast_paidref"))
async def cmd_broadcast_paidref(message: Message):
    """Рассылка «звёзды за оплатившего друга» ВСЕМ пользователям (owner-only)."""
    if not OWNER_ID or message.from_user.id != OWNER_ID:
        return
    await _bc_paidref(message)


async def _bc_paidref(message):
    """Акция «звёзды за приглашённого, который оплатил».

    Отказывает в двух случаях, когда обещание нельзя было бы выполнить:
    акция не включена или награда за приглашённого переведена на триал —
    тогда бот отмечает реферала ещё до оплаты и об оплате уже не узнает.
    """
    if not REF_PAID_STARS:
        await message.answer(
            "⛔ Акция не включена — рассылка отменена.\n\n"
            "Задайте <code>REF_PAID_STARS</code> (сколько звёзд за оплатившего) "
            "в настройках бота и перезапустите его.")
        return
    if REFERRAL_ON_TRIAL:
        await message.answer(
            "⛔ Включён <code>REFERRAL_ON_TRIAL</code>: награда за приглашённого "
            "срабатывает на пробном периоде, и об оплатах бот не узнает — "
            "звёзды засчитать будет не за что.\n\n"
            "Уберите эту переменную и перезапустите бота.")
        return

    users = db.all_user_ids()
    await message.answer(
        f"⏳ Рассылаю «{REF_PAID_STARS} ⭐ за оплатившего друга» "
        f"по {len(users)} пользователям…\n\n"
        "О каждой оплате по реферальной ссылке придёт сообщение сюда — "
        "с тем, кому вручить звёзды.")

    def make(uid):
        link = _ref_link(uid)
        return (texts.REF_PAID_BROADCAST.format(
                    stars=REF_PAID_STARS, days=REFERRAL_BONUS_DAYS, link=link),
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
    await _bc_lapsed(message)


async def _bc_sale_return(message):
    """Акция «месяц + бонусные дни» тем, у кого подписка закончилась.

    Самая тёплая аудитория из неплатящих: человек уже пробовал сервис и
    знает, что получает. Ему нужен не рассказ, а повод вернуться.

    Отправка невозможна при выключенной акции — и это главное здесь.
    Пообещать бонус, которого plan_days() не начислит, значит взять
    деньги и выдать меньше обещанного. Проверка не косметическая.
    """
    if not sale_active():
        await message.answer(
            f"⛔ Акция не активна (закончилась {_sale_until_ru()}) — "
            "рассылка отменена.\n\n"
            "Иначе люди заплатят, а бонусные дни не начислятся: их даёт "
            "<code>plan_days()</code>, и он смотрит на ту же дату.\n\n"
            "Задайте <code>SALE_UNTIL</code> и <code>SALE_BONUS_DAYS</code> "
            "в настройках бота и перезапустите его.")
        return

    users = db.lapsed_users(int(time.time()))
    if not users:
        await message.answer("Некому слать: ни у кого подписка не заканчивалась.")
        return

    p = PLANS.get("1m", {})
    base, total = p.get("days", 30), plan_days("1m")
    await message.answer(
        f"⏳ Рассылаю возврат с акцией (+{sale_bonus('1m')} дн., "
        f"итого {total} дн. за {_month_price()}, по {_sale_until_ru()}) "
        f"по {len(users)} ушедшим…")

    text = texts.SALE_RETURN.format(bonus=sale_bonus("1m"), price=_month_price(),
                                    total=total, base=base,
                                    until=_sale_until_ru())
    kb = sale_kb()
    await broadcast(message, [uid for uid, _ in users], lambda uid: (text, kb))


async def _bc_lapsed(message):
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


# Статусы, при которых человек считается подписанным. «restricted» — это
# участник с ограничениями, он в канале, поэтому проверяется отдельно.
_IN_CHANNEL = {"creator", "administrator", "member"}


async def _is_subscribed(bot, uid):
    """Подписан ли человек на канал розыгрыша. None — проверить не удалось.

    Именно None, а не False: не сумев проверить, нельзя молча выкинуть
    человека из розыгрыша — он выполнил условия и об этом не узнает.
    """
    for attempt in (1, 2):
        try:
            m = await bot.get_chat_member(chat_id=GIVEAWAY_CHANNEL, user_id=uid)
        except TelegramRetryAfter as e:
            if attempt == 1:
                await asyncio.sleep(e.retry_after + 1)
                continue
            return None
        except Exception as e:
            # Молчать здесь нельзя: самая частая причина — бот не админ
            # в канале, и без этой строки в журнале искать нечего.
            logging.warning("Розыгрыш: не смог проверить подписку %s на %s — %s",
                            uid, GIVEAWAY_CHANNEL, e)
            return None
        st = getattr(m, "status", None)
        logging.info("Розыгрыш: подписка %s на %s — статус %s",
                     uid, GIVEAWAY_CHANNEL, st)
        if st in _IN_CHANNEL:
            return True
        if st == "restricted":
            return bool(getattr(m, "is_member", False))
        return False
    return None


async def _send_chunks(message, header, items, footer=""):
    """Длинный список — несколькими сообщениями: у Telegram лимит 4096."""
    chunk, size = [], 0
    first = True
    for it in items:
        if size + len(it) > 3500:
            await message.answer(("" if first else "…\n") +
                                 (header if first else "") + "\n".join(chunk))
            chunk, size, first = [], 0, False
        chunk.append(it)
        size += len(it) + 1
    tail = ((header if first else "") + "\n".join(chunk) + footer).strip()
    if tail:
        await message.answer(tail)


async def _giveaway_screen(uid, bot):
    """Текст и клавиатура экрана розыгрыша по текущему состоянию человека.

    Возвращает (текст, клавиатура, участвует_ли). Вынесено отдельно, потому
    что этот же экран показывается из трёх мест: по ссылке из рекламы,
    по кнопке под постом в канале и из меню бота.
    """
    if not giveaway_active():
        return texts.GIVEAWAY_OFF.format(channel=GIVEAWAY_CHANNEL), None, False

    common = dict(prize=GIVEAWAY_PRIZE, channel=GIVEAWAY_CHANNEL,
                  until=GIVEAWAY_UNTIL or "скоро", days=TRIAL_DAYS,
                  limit=GIVEAWAY_MAX_ENTRIES)

    u = db.get_user(uid)
    has_key = bool(u and u["sub_until"])
    already_in = bool(u and u["giveaway_at"])

    # Потолок проверяем раньше всего, но уже попавших он не выкидывает:
    # место занято, и отбирать его закрытием набора нечестно.
    if not already_in and db.giveaway_count() >= GIVEAWAY_MAX_ENTRIES:
        logging.info("Розыгрыш: %s пришёл к закрытому набору", uid)
        return (texts.GIVEAWAY_FULL.format(limit=GIVEAWAY_MAX_ENTRIES,
                                           days=TRIAL_DAYS,
                                           channel=GIVEAWAY_CHANNEL),
                giveaway_kb(), False)

    # Пауза после первого запуска бота. Скрипт её пересидит, но массовая
    # регистрация аккаунтов растягивается во времени — а растянутая
    # регистрация видна в отчёте, в отличие от мгновенной.
    created = (u["created_at"] if u else 0) or 0
    waited = (int(time.time()) - created) // 60
    if not already_in and created and waited < GIVEAWAY_MIN_MINUTES:
        logging.info("Розыгрыш: %s жмёт участие через %s мин. после старта",
                     uid, waited)
        return (texts.GIVEAWAY_TOO_FRESH.format(
            minutes=GIVEAWAY_MIN_MINUTES,
            left=max(1, GIVEAWAY_MIN_MINUTES - waited)), giveaway_kb(), False)

    subscribed = await _is_subscribed(bot, uid)

    if subscribed is None:
        return (texts.GIVEAWAY_CHECK_FAILED,
                giveaway_kb(need_key=not has_key), False)
    if not has_key:
        return (texts.GIVEAWAY_NEED_KEY.format(**common),
                giveaway_kb(need_key=True, need_sub=not subscribed), False)
    if not subscribed:
        return (texts.GIVEAWAY_NEED_SUB.format(**common),
                giveaway_kb(need_sub=True), False)

    db.mark_giveaway_entry(uid)
    count = db.giveaway_count()
    return (texts.GIVEAWAY_OK.format(count=count,
                                     people=_plural_people(count), **common),
            giveaway_kb(), True)


def _sale_kwargs():
    """Числа акции для напоминаний о конце подписки — по месячному тарифу.

    Напоминание приходит тому, у кого срок вот-вот кончится: ему решать
    про ближайший месяц, а не про год. Числа берутся из тех же функций,
    что и начисление, — поэтому обещание не может разойтись с фактом.
    """
    p = PLANS.get("1m", {})
    return dict(until=_sale_until_ru(), price=_month_price(),
                total=plan_days("1m"), base=p.get("days", 30))


def _plan_price(code):
    """Цена тарифа строкой — рублями, если касса настроена, иначе звёздами."""
    p = PLANS.get(code, {})
    if platega.is_configured() or lolz.can_invoice():
        return f"{p.get('rub', 0)} ₽"
    return f"{p.get('stars', 0)} ⭐"


def _per_day(code):
    """Во сколько обходится день по тарифу — с учётом бонусных дней."""
    days = plan_days(code) or 1
    p = PLANS.get(code, {})
    card = platega.is_configured() or lolz.can_invoice()
    value = (p.get("rub", 0) if card else p.get("stars", 0)) / days
    return f"{value:.2f}".replace(".", ",") + (" ₽" if card else " ⭐")


def _sale_plans_kwargs():
    """Числа акции по всем трём тарифам для рассылки.

    Префиксы: y — год, q — три месяца, m — месяц. Всё считается теми же
    функциями, что и начисление: пообещать 455 дней и выдать 365 нельзя.
    """
    out = {}
    for prefix, code in (("y", "12m"), ("q", "3m"), ("m", "1m")):
        p = PLANS.get(code, {})
        out[f"{prefix}_price"] = _plan_price(code)
        out[f"{prefix}_base"] = p.get("days", 30)
        out[f"{prefix}_total"] = plan_days(code)
        out[f"{prefix}_bonus"] = sale_bonus(code)
    out["y_perday"] = _per_day("12m")
    return out


def _month_price():
    """Цена месяца строкой — рублями, если касса настроена, иначе звёздами.

    Обещать оплату картой при выключенной кассе нельзя: человек нажмёт
    и упрётся в тупик ровно в тот момент, когда решился заплатить.
    """
    p = PLANS.get("1m", {})
    if platega.is_configured() or lolz.can_invoice():
        return f"{p.get('rub')} ₽"
    return f"{p.get('stars')} ⭐"


def _plural_people(n):
    """1 человек, 2 человека, 5 человек."""
    if n % 100 in (11, 12, 13, 14):
        return "человек"
    if n % 10 == 1:
        return "человек"
    if n % 10 in (2, 3, 4):
        return "человека"
    return "человек"


@dp.callback_query(F.data == "gw_check")
async def cb_giveaway_check(cq: CallbackQuery):
    """Проверка условий по кнопке — и из бота, и из-под поста в своём канале.

    На callback Telegram принимает ровно ОДИН ответ, и пока он не пришёл,
    кнопка крутит часики. Поэтому здесь единственный вызов answer, он же
    последний, и до него ни один путь не имеет права оборваться.

    Результат всегда показываем всплывающим окном: если условия с прошлого
    раза не изменились, экран остаётся прежним, и без окна нажатие выглядит
    как «ничего не произошло».
    """
    uid = cq.from_user.id
    where = cq.message.chat.type if cq.message else "нет сообщения"
    logging.info("Розыгрыш: нажатие от %s, чат %s", uid, where)

    alert = None
    try:
        # Проверка ходит в Telegram, и зависший запрос держал бы часики
        # до самого таймаута — ограничиваем ожидание сами.
        text, kb, joined = await asyncio.wait_for(
            _giveaway_screen(uid, cq.bot), timeout=8)
    except asyncio.TimeoutError:
        logging.warning("Розыгрыш: проверка не уложилась в 8 с (uid %s)", uid)
        text, kb, joined = texts.GIVEAWAY_CHECK_FAILED, giveaway_kb(), False
        alert = "Telegram не ответил вовремя. Попробуйте ещё раз."
    except Exception:
        logging.exception("Розыгрыш: проверка условий сорвалась (uid %s)", uid)
        text, kb, joined = texts.GIVEAWAY_CHECK_FAILED, giveaway_kb(), False
        alert = "Не получилось проверить. Попробуйте через минуту."

    if alert is None:
        alert = ("Вы в списке участников ✅" if joined
                 else "Условия ещё не выполнены — смотрите сообщение")

    # Под постом в канале сообщение опубликовано от имени канала,
    # редактировать его нельзя — отвечаем в личку.
    if cq.message is None or where == "channel":
        try:
            await send_banner_to(cq.bot, uid, text, kb)
        except Exception as e:
            logging.warning("Розыгрыш: личка недоступна для %s — %s", uid, e)
            alert = (f"Откройте @{BOT_USERNAME}, нажмите «Старт» "
                     "и вернитесь сюда")
    else:
        try:
            await show_screen(cq, text, kb)
        except TelegramBadRequest as e:
            # «not modified» — условия те же, экран менять нечего.
            # Остальное стоит увидеть в журнале.
            if "not modified" not in str(e):
                logging.exception("Розыгрыш: не смог обновить экран (uid %s)", uid)
        except Exception:
            logging.exception("Розыгрыш: не смог обновить экран (uid %s)", uid)

    try:
        await cq.answer(alert, show_alert=True)
    except Exception as e:
        logging.warning("Розыгрыш: ответ на нажатие не прошёл (uid %s) — %s",
                        uid, e)


@dp.message(Command("giveaway_post"))
async def cmd_giveaway_post(message: Message):
    """Публикует пост розыгрыша в свой канал — с живой кнопкой проверки.

    Именно командой, а не через сторонний постер: callback-кнопка работает
    только у того бота, который опубликовал сообщение. Пост из чужого
    постера кнопку покажет, но нажатие уйдёт не нам.
    """
    if not OWNER_ID or message.from_user.id != OWNER_ID:
        return
    await _publish_giveaway_post(message)


async def _publish_giveaway_post(message):
    if not giveaway_active():
        await message.answer("Розыгрыш выключен: не задан GIVEAWAY_PRIZE.")
        return
    text = texts.GIVEAWAY_POST.format(prize=GIVEAWAY_PRIZE, days=TRIAL_DAYS,
                                      until=GIVEAWAY_UNTIL or "скоро",
                                      limit=GIVEAWAY_MAX_ENTRIES)
    try:
        await message.bot.send_message(GIVEAWAY_CHANNEL, text,
                                       reply_markup=giveaway_post_kb(BOT_USERNAME, REF_GIFT_STARS))
        await message.answer(f"✅ Опубликовано в {GIVEAWAY_CHANNEL}.")
    except Exception as e:
        await message.answer(
            f"Не вышло опубликовать: {e}\n\n"
            f"Проверьте, что бот — администратор в {GIVEAWAY_CHANNEL} "
            "и умеет отправлять сообщения.")


@dp.message(Command("giveaway"))
async def cmd_giveaway(message: Message):
    """Список участников и выбор победителя (owner-only).

      /giveaway               — список участников с номерами
      /giveaway pick          — выбрать победителя
      /giveaway gamechan      — только пришедшие по этой метке

    Два шага намеренно. Список публикуется в канале ДО розыгрыша, чтобы
    зрители видели, из кого выбирают, и сверили номер победителя. Розыгрыш,
    где список показывают вместе с итогом, доверия не вызывает — и
    справедливо, там можно нарисовать что угодно.

    Участник — тот, кто нажал «Проверить и участвовать» и прошёл проверку.
    Подписку перепроверяем прямо сейчас: иначе победителем окажется тот,
    кто отписался на следующий день после регистрации.
    """
    if not OWNER_ID or message.from_user.id != OWNER_ID:
        return
    await _giveaway_report(message, args=(message.text or '').split()[1:])


async def _giveaway_report(message, args=(), pick=False):

    args = [a.lower() for a in args]
    do_pick = pick or "pick" in args
    tags = [a for a in args if a != "pick"]
    source = _clean_source(tags[0]) if tags else None

    rows = db.giveaway_entries()
    if source and source != "all":
        rows = [r for r in rows if r[2] == source]
    if not rows:
        await message.answer("Участников пока нет.")
        return

    await message.answer(f"⏳ Проверяю подписку у {len(rows)} человек…")
    eligible, not_subbed, unknown = [], 0, []
    for uid, username, _src in rows:
        sub = await _is_subscribed(message.bot, uid)
        if sub is True:
            eligible.append((uid, username))
        elif sub is False:
            not_subbed += 1
        else:
            unknown.append((uid, username))
        await asyncio.sleep(0.05)          # не упираемся в лимиты Telegram

    def who(uid, username):
        return f"@{username}" if username else f"id{uid}"

    susp = db.giveaway_suspicious()
    # Участник, ни разу не открывавший страницу подключения, пришёл
    # только за подарком. Единицы — норма, большинство — накрутка.
    warn = ""
    if susp["no_touch"] >= 5 and susp["no_touch"] * 2 >= len(rows):
        warn = (f"⚠️ {susp['no_touch']} из {len(rows)} даже не открывали "
                "подключение — пришли только за подарком\n")
    head = (f"🎲 <b>Розыгрыш{f' · метка {source}' if source else ''}</b>\n\n"
            f"Мест занято: <b>{db.giveaway_count()}</b> из {GIVEAWAY_MAX_ENTRIES}\n"
            f"{warn}"
            f"Подтвердили участие: <b>{len(rows)}</b>\n"
            f"Подписка на канал сейчас: <b>{len(eligible)}</b>\n"
            f"Отписались после регистрации: {not_subbed}\n")
    if unknown:
        head += (f"⚠️ Не удалось проверить: {len(unknown)} — "
                 "бот не админ в канале или человек скрыт\n")
    if not eligible:
        await message.answer(head + "\nУчастников нет.")
        return

    if not do_pick:
        items = [f"{i}. {who(u, n)}" for i, (u, n) in enumerate(eligible, 1)]
        await _send_chunks(
            message, head + "\n<b>Участники:</b>\n", items,
            "\n\n<i>Опубликуйте этот список в канале до розыгрыша, "
            "затем запустите</i> <code>/giveaway"
            + (f" {source}" if source else "") + " pick</code>")
        return

    # Победитель. SystemRandom, а не обычный random: тот детерминирован
    # от зерна, и при вопросе «а не подкручено ли» ответить было бы нечем.
    i = random.SystemRandom().randrange(len(eligible))
    uid, username = eligible[i]
    await message.answer(
        head + f"\n🏆 <b>Победитель — №{i + 1} из {len(eligible)}</b>\n"
               f"{who(uid, username)}  (<code>{uid}</code>)\n\n"
        "<i>Номер совпадает с опубликованным списком.</i>")


@dp.errors()
async def on_error(event):
    """Ловит всё, что упало в любом хендлере, и шлёт владельцу.

    Один обработчик на весь бот: оборачивать каждый хендлер в try
    пришлось бы вручную, и первый же новый хендлер про это забыл бы.

    Возвращаем True — aiogram считает ошибку обработанной и не роняет
    опрос. Пользователь при этом всё равно останется без ответа, но
    остальные продолжат пользоваться ботом.
    """
    uid = None
    try:
        upd = event.update
        for part in (upd.message, upd.callback_query, upd.pre_checkout_query):
            if part and part.from_user:
                uid = part.from_user.id
                break
    except Exception:
        pass
    where = "обработка сообщения" if uid else "бот"
    logging.exception("Необработанная ошибка у %s", uid, exc_info=event.exception)
    await alerts.report(event.bot if hasattr(event, "bot") else bot_instance[0],
                        OWNER_ID, where, event.exception, uid)
    return True


# Ссылка на живой Bot нужна оповещениям из фоновых задач: там нет ни
# message, ни callback, а слать владельцу всё равно надо.
bot_instance = [None]


async def alert(where, exc, uid=None):
    """Короткий вызов для мест, где ошибку ловим сами (оплата, ключи)."""
    await alerts.report(bot_instance[0], OWNER_ID, where, exc, uid)


# ============ Панель владельца ============
ADMIN_HOME = (
    "🛠 <b>Панель управления</b>\n\n"
    "Аналитика, розыгрыш и рассылки. Всё то же, что командами, "
    "только не надо их помнить.\n\n"
    "Выберите раздел 👇"
)


def _is_owner(x):
    return bool(OWNER_ID) and x.from_user.id == OWNER_ID


def _broadcast_fn(code):
    return {"lapsed": _bc_lapsed, "nc": _bc_nc, "ref": _bc_ref,
            "promo": _bc_promo, "sale": _bc_sale, "gift": _bc_gift,
            "howru": _bc_howsitgoing, "salert": _bc_sale_return,
            "paidref": _bc_paidref, "salemore": _bc_sale_more}.get(code)


# Предпросмотр писем. Флаг живёт в контексте текущей задачи: параллельная
# настоящая рассылка его не видит и уйдёт как обычно.
_PREVIEW = contextvars.ContextVar("broadcast_preview", default=False)
# Заглушка вместо токена подписки: кнопка «Подключиться» в примере видна,
# но пользователя в панели ради предпросмотра не заводим.
PREVIEW_TOKEN = "preview"


class _PreviewMessage:
    """Подменяет message в рассылке: её отчёты помечаются как предпросмотр."""

    def __init__(self, message):
        self._message = message

    def __getattr__(self, name):
        return getattr(self._message, name)

    async def answer(self, text, **kwargs):
        return await self._message.answer(
            "👁 <i>Предпросмотр. В настоящей рассылке:</i>\n\n" + text, **kwargs)


async def _preview_reminder(message, code):
    """Пример напоминания с примерными датами — только владельцу."""
    now = int(time.time())
    common = dict(ending="ось", days=3, word=_plural_days(3))
    date = fmt_date(now + 3 * 86400)
    if code == "r_trial_on":
        if sale_active():
            text = texts.TRIAL_ENDING_SALE.format(date=date, **_sale_kwargs(), **common)
        else:
            text = texts.TRIAL_ENDING_ACTIVE.format(date=date, price=_month_price(), **common)
        kb = renew_kb()
    elif code == "r_trial_off":
        text, kb = texts.TRIAL_ENDING.format(**common), connect_kb(PREVIEW_TOKEN)
    elif code == "r_sub":
        common["date"] = date
        text = (texts.SUB_ENDING_SALE.format(**_sale_kwargs(), **common) if sale_active()
                else texts.SUB_ENDING.format(**common))
        kb = renew_kb()
    elif code == "r_nc":
        text, kb = texts.NOT_CONNECTED, connect_kb(PREVIEW_TOKEN)
    elif code == "r_ask":
        link = f"https://t.me/{BOT_USERNAME}?start=ref_{OWNER_ID}"
        text = texts.REF_ASK.format(days=ADVOCACY_AFTER_DAYS, bonus=REFERRAL_BONUS_DAYS, link=link)
        kb = ref_share_kb(link)
    else:
        await message.answer("Неизвестное напоминание.")
        return
    await send_letter_to(message.bot, message.chat.id, text, kb)
    await message.answer("👁 Пример напоминания — выше. Даты в нём примерные.")


async def _preview_letter(message, code):
    """Пример письма рассылки или напоминания — только в чат владельца."""
    if code.startswith("r_"):
        await _preview_reminder(message, code)
        return
    fn = _broadcast_fn(code)
    if not fn:
        await message.answer("Неизвестная рассылка.")
        return
    flag = _PREVIEW.set(True)
    try:
        await fn(_PreviewMessage(message))
    finally:
        _PREVIEW.reset(flag)


async def _run_broadcast(message, code):
    """Запуск рассылки из панели. Владельца проверили до вызова."""
    fn = _broadcast_fn(code)
    if not fn:
        await message.answer("Неизвестная рассылка.")
        return
    await fn(message)
    await message.answer("Вернуться в панель — /admin")


@dp.message(Command("admin"))
async def cmd_admin(message: Message):
    """Единая панель вместо восьми команд наизусть (owner-only)."""
    if not _is_owner(message):
        return
    await message.answer(ADMIN_HOME, reply_markup=admin_kb())


@dp.message(Command("letter_preview"))
async def cmd_letter_preview(message: Message):
    """Список писем для предпросмотра (owner-only)."""
    if not _is_owner(message):
        return
    await message.answer(
        "👁 <b>Предпросмотр писем</b>\n\n"
        "Выберите, что показать, — пример придёт только вам. Рассылка "
        "собирается так же, как настоящая: если её сейчас нельзя отправить "
        "(например, акция закончилась), увидите тот же отказ.",
        reply_markup=admin_preview_kb())


@dp.message(Command("style_preview"))
async def cmd_style_preview(message: Message):
    """Главное меню в новом оформлении — только владельцу (owner-only).

    Чтобы увидеть богатые сообщения и цветные кнопки в настоящем Telegram
    до того, как включать их всем: оба варианта баннера подряд. Кнопки под
    предпросмотром рабочие — заодно проверяется переключение экранов.
    """
    if not _is_owner(message):
        return
    variants = [(False, "статичный баннер")]
    if BANNER_ANIM.exists():
        variants.append((True, "анимированный баннер"))
    for animated, label in variants:
        try:
            sent = await message.bot.send_rich_message(
                chat_id=message.chat.id,
                rich_message=_rich_message(texts.WELCOME, animated),
                reply_markup=paint(main_menu()))
            _remember_banner(sent)
        except Exception as e:
            await message.answer(f"⛔ Не вышло ({label}):\n"
                                 f"<code>{html_escape(str(e))[:700]}</code>")
    await message.answer(
        "👆 Предпросмотр нового оформления — видите только вы.\n\n"
        f"Сейчас у всех: <b>{'новое' if RICH_MESSAGES else 'старое'}</b>, "
        f"баннер <b>{'анимированный' if BANNER_ANIMATED else 'статичный'}</b>.\n"
        "Включить для всех — <code>BOT_RICH_MESSAGES=1</code>, "
        "анимацию — <code>BOT_BANNER_ANIM=1</code>.")


async def _admin_show(cq, text, refresh=None):
    """Показать отчёт в панели.

    Отчёты длинные и легко перерастают лимит подписи к фото, поэтому
    экран панели — обычный текст: его можно переписывать целиком.
    """
    try:
        await cq.message.edit_text(text, reply_markup=admin_back_kb(refresh))
    except TelegramBadRequest as e:
        if "not modified" in str(e):
            return
        # длиннее лимита или сообщение не редактируется — шлём новым
        await cq.message.answer(text, reply_markup=admin_back_kb(refresh))


@dp.callback_query(F.data.startswith("adm:"))
async def cb_admin(cq: CallbackQuery):
    """Все кнопки панели. Одна точка входа — проверка владельца тоже одна."""
    if not _is_owner(cq):
        await cq.answer("Не для вас", show_alert=True)
        return
    action = cq.data[4:]

    if action == "home":
        await cq.message.edit_text(ADMIN_HOME, reply_markup=admin_kb())
        await cq.answer()
        return

    if action == "cleanup":
        await cq.answer()
        await _admin_show(cq, _cleanup_text(), "adm:cleanup")
        return

    if action == "stats":
        await cq.answer("Считаю…")
        await _admin_show(cq, _stats_text(), "adm:stats")
        return

    if action == "funnel":
        await cq.answer("Спрашиваю панель…")
        await _admin_show(cq, await _funnel_text(), "adm:funnel")
        return

    if action == "sources":
        await cq.answer("Считаю…")
        await _admin_show(cq, _sources_text(), "adm:sources")
        return

    if action == "refs":
        await cq.answer("Считаю…")
        await _admin_show(cq, _referrals_text(), "adm:refs")
        return

    # ---------- Розыгрыш ----------
    if action == "gw":
        n = db.giveaway_count()
        susp = db.giveaway_suspicious()
        text = (f"🎁 <b>Розыгрыш: {GIVEAWAY_PRIZE}</b>\n\n"
                f"Участников: <b>{n}</b> из {GIVEAWAY_MAX_ENTRIES}\n"
                f"Пришло за час: {susp['recent']}\n"
                f"Не открывали подключение: {susp['no_touch']}\n\n"
                f"Канал: {GIVEAWAY_CHANNEL}\n"
                f"Итоги: {GIVEAWAY_UNTIL or 'не заданы'}\n"
                f"Пауза перед участием: {GIVEAWAY_MIN_MINUTES} мин."
                ) if giveaway_active() else (
            "🎁 <b>Розыгрыш выключен</b>\n\n"
            "Чтобы включить, задайте <code>GIVEAWAY_PRIZE</code> "
            "в настройках бота.")
        await cq.message.edit_text(text, reply_markup=admin_giveaway_kb(giveaway_active()))
        await cq.answer()
        return

    if action in ("gw:list", "gw:pick"):
        await cq.answer("Проверяю подписки…")
        await _giveaway_report(cq.message, pick=action.endswith("pick"))
        return

    if action == "gw:post":
        await cq.answer("Публикую…")
        await _publish_giveaway_post(cq.message)
        return

    # ---------- Рассылки ----------
    if action == "bc":
        await cq.message.edit_text(
            "📢 <b>Рассылки</b>\n\nВыберите, что разослать. "
            "Перед отправкой спрошу подтверждение.",
            reply_markup=admin_broadcasts_kb())
        await cq.answer()
        return

    if action.startswith("bc:"):
        code = action[3:]
        title, whom = BROADCASTS.get(code, ("?", "?"))
        await cq.message.edit_text(
            f"📢 <b>{title}</b>\n\nУйдёт: {whom}.\n\n"
            "⚠️ Отменить отправленное нельзя. Рассылать?",
            reply_markup=admin_confirm_kb(code))
        await cq.answer()
        return

    if action.startswith("pv:"):
        await cq.answer("Готовлю пример…")
        await _preview_letter(cq.message, action[3:])
        return

    if action.startswith("go:"):
        code = action[3:]
        await cq.answer("Запускаю…")
        await _run_broadcast(cq.message, code)
        return

    await cq.answer()


def _referrals_text():
    """Кто кого привёл — с качеством приглашённых (owner-only).

    Одного числа приглашений недостаточно: человек может нагнать сотню
    переходов, из которых ключ не откроет никто. Поэтому рядом стоят
    «пользуются» и «платят» — по ним и видно, работа это или клики.
    """
    rows = db.referral_leaders()
    if not rows:
        return "По реферальным ссылкам ещё никто не приходил."
    lines = ["🤝 <b>Кто приводит людей</b>\n",
             "<code>кто          пришло  ключ  польз.  опл.</code>"]
    for r in rows:
        who = (f"@{r['username']}" if r["username"] else f"id{r['uid']}")[:12]
        lines.append(f"<code>{who:<12} {r['invited']:>6} {r['with_key']:>5} "
                     f"{r['used']:>6} {r['paying']:>5}</code>")
    lines += [
        "",
        "<b>ключ</b> — взяли подписку в боте",
        "<b>польз.</b> — реально скачали ключ в приложение",
        "<b>опл.</b> — заплатили хотя бы раз",
        "",
        "<i>Смотреть надо на «польз.»: большое «пришло» при нулевом "
        "«польз.» значит, что люди кликали, но VPN им не нужен.</i>",
    ]
    return "\n".join(lines)


@dp.message(Command("referrals"))
async def cmd_referrals(message: Message):
    """Кто сколько привёл и какого качества (owner-only)."""
    if not OWNER_ID or message.from_user.id != OWNER_ID:
        return
    await message.answer(_referrals_text())


@dp.message(Command("stats"))
async def cmd_stats(message: Message):
    """Своя статистика бота (owner-only).

    Нужна потому, что «N пользователей в месяц» в профиле бота — это MAU
    Telegram: те, кто ЗА 30 ДНЕЙ что-то нажимал. Человек, взявший ключ и
    молча пользующийся VPN, оттуда выпадает, оставаясь клиентом. Своей
    цифры до этого было негде посмотреть, и рост приходилось оценивать
    по чужой метрике, которая измеряет не то.
    """
    if not OWNER_ID or message.from_user.id != OWNER_ID:
        return
    await message.answer(_stats_text())


def _backup_line():
    """Строка о свежести резервной копии.

    Бэкап по расписанию молчалив: сломается — узнаешь в тот момент, когда
    копия понадобится, то есть в худший из возможных. Поэтому его возраст
    показывается там, куда и так смотрят каждый день.
    """
    d = Path(os.environ.get("BACKUP_DIR", "/root/backups"))
    try:
        files = sorted(d.glob("*.db"), key=lambda f: f.stat().st_mtime)
    except OSError:
        files = []
    if not files:
        return "💾 Копий базы нет — резервное копирование не настроено"
    last = files[-1].stat()
    hours = (time.time() - last.st_mtime) / 3600
    if hours > 48:
        return (f"⚠️ Последняя копия базы {hours / 24:.0f} дн. назад — "
                "похоже, копирование сломалось")
    when = f"{hours:.0f} ч назад" if hours >= 1 else "меньше часа назад"
    return (f"💾 Копия базы: {when}, {last.st_size / 1024:.0f} КБ · "
            f"всего копий {len(files)}")


def _stats_text():
    """Сводка бота. Общая для команды /stats и кнопки в панели —
    иначе два места разъедутся при первой же правке."""
    s = db.owner_stats()
    live = s["total"] - s["blocked"]
    lines = [
        "📊 <b>Статистика бота</b>", "",
        f"<code>{s['total']:>4}</code>  всего запускали бота",
        f"<code>{live:>4}</code>  из них не заблокировали",
        "",
        f"<code>{s['new_7']:>4}</code>  пришло за 7 дней",
        f"<code>{s['new_30']:>4}</code>  пришло за 30 дней",
        "",
        f"<code>{s['with_key']:>4}</code>  получали ключ",
        f"<code>{s['active']:>4}</code>  с действующей подпиской",
        f"<code>{s['paying']:>4}</code>  когда-либо платили",
    ]
    if s["giveaway"]:
        lines.append(f"<code>{s['giveaway']:>4}</code>  участвуют в розыгрыше")
    lines += [
        "",
        _backup_line(),
        "",
        "<i>«Пользователей в месяц» в профиле бота — это счётчик Telegram: "
        "он считает только тех, кто за 30 дней что-то нажимал. Тот, кто "
        "взял ключ и просто пользуется VPN, туда не попадает.</i>",
    ]
    return "\n".join(lines)


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

    await message.answer(await _funnel_text())


async def _funnel_text():
    """Воронка подключения. Общая для команды /funnel и кнопки в панели.

    Запрос в панель Marzban блокирующий — уводим его в поток, иначе на
    время ответа встаёт весь бот и остальные пользователи ждут.
    """
    rows = db.funnel_rows()
    if not rows:
        return "Ключей ещё никому не выдавали."

    online = await asyncio.to_thread(online_usernames)   # None — панель молчит
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

    return "\n".join(lines)


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
        # Владельцу про каждый триал больше не пишем: при двух десятках
        # активаций в день поток перестаёт читаться, а важное — оплаты
        # и сбои — в нём тонет. Вместо этого раз в сутки уходит сводка
        # (см. daily_digest).
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
    if _promo_too_early(uid):
        await show_screen(cq, texts.PROMO_AFTER_TRIAL.format(
            date=fmt_date(db.get_user(uid)["sub_until"])), reply_markup=main_menu())
        await cq.answer()
        return
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
    if _promo_too_early(uid):
        await send_banner(message, texts.PROMO_AFTER_TRIAL.format(
            date=fmt_date(db.get_user(uid)["sub_until"])), main_menu())
        return
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
                          code, p["rub"], provider=provider, pay_url=pay_url)
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


TRAFFIC_CHECK_INTERVAL = 3 * 3600     # трафик считается не мгновенно
TRAFFIC_WARN_AT = 0.8                 # доля лимита, после которой пишем


async def warn_traffic_running_out(bot):
    """Предлагает оплату тем, у кого кончается бесплатный трафик.

    Это самый тёплый момент для предложения: человек пользуется VPN каждый
    день и упирается в лимит — ему есть что терять. Пишем один раз.
    """
    while True:
        await asyncio.sleep(TRAFFIC_CHECK_INTERVAL)
        try:
            used = await asyncio.to_thread(traffic_by_username)
            if used is None:
                continue                      # панель молчит — попробуем позже
            limit = TRIAL_DATA_LIMIT_GB * 1024 ** 3
            if not limit:
                continue                      # лимита нет — предупреждать не о чем
            for uid in db.traffic_warn_candidates(int(time.time())):
                if db.has_paid(uid):
                    continue                  # у платящих лимит другой
                spent = used.get(f"ikk_{uid}") or 0
                if spent < limit * TRAFFIC_WARN_AT:
                    continue
                db.mark_traffic_warned(uid)   # до отправки: второй раз не напишем
                try:
                    await send_letter_to(
                        bot, uid,
                        texts.TRAFFIC_LOW.format(
                            used=f"{spent / 1024 ** 3:.0f}", limit=TRIAL_DATA_LIMIT_GB,
                            full=DATA_LIMIT_GB, price=_month_price()),
                        renew_kb())
                except TelegramForbiddenError:
                    db.mark_blocked(uid)
                except Exception:
                    logging.exception("Трафик: не смог написать %s", uid)
                await asyncio.sleep(0.1)
        except Exception:
            logging.exception("Бот: ошибка проверки трафика")


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
                else:
                    await _nudge_unpaid(bot, rec)
        except Exception:
            logging.exception("Бот: ошибка фоновой проверки счетов")


# Через сколько напомнить о неоплаченном счёте. Полчаса: раньше — как
# будто торопим, позже — человек уже занят другим.
INVOICE_NUDGE_AFTER = int(os.environ.get("INVOICE_NUDGE_AFTER", "1800"))


async def _nudge_unpaid(bot, rec):
    """Один раз напоминает про счёт, который создали и не оплатили."""
    if rec.get("nudged") or not rec.get("pay_url"):
        return
    if int(time.time()) - rec["created_at"] < INVOICE_NUDGE_AFTER:
        return
    db.mark_invoice_nudged(rec["payment_id"])      # до отправки: не задвоим
    p = PLANS.get(rec["plan"], {})
    try:
        await send_letter_to(
            bot, rec["user_id"],
            texts.INVOICE_UNPAID.format(title=p.get("title", rec["plan"]),
                                        rub=rec["amount_rub"]),
            card_invoice_kb(rec["pay_url"], rec["payment_id"]))
    except TelegramForbiddenError:
        db.mark_blocked(rec["user_id"])
    except Exception:
        logging.exception("Счёт %s: напоминание не ушло", rec["payment_id"])


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
# Через сколько часов после выдачи ключа напомнить, если человек так и не
# подключился. Три часа — компромисс: человек успевает отвлечься и забыть,
# но ещё помнит, зачем вообще заходил в бота.
CONNECT_REMIND_AFTER_HOURS = int(os.environ.get("CONNECT_REMIND_AFTER_HOURS", "3"))
CONNECT_REMIND_INTERVAL = 3600
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
    """Напоминание об окончании триала — своё для каждого случая.

    Шлётся один раз каждому, у кого до конца триала осталось не больше
    TRIAL_REMIND_BEFORE_DAYS дней.

    Раньше тех, кто уже подключился, пропускали «чтобы не беспокоить» —
    и это стоило почти всех денег. Человек 15 дней пользовался VPN, потом
    доступ молча пропадал, и предложить продлить было некому. На 42
    пользователей приходился ОДИН платящий.

    Теперь наоборот: кто пользуется — тому и предлагаем оплату, это самая
    тёплая аудитория. Кто не подключился — тому прежний текст с кнопкой
    подключения: продавать человеку, не видевшему сервис, бессмысленно.
    """
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
                days = max(1, round((sub_until - now) / 86400))
                common = dict(ending="ся" if days == 1 else "ось",
                              days=days, word=_plural_days(days))
                if connected:
                    # Пока идёт акция, зовём ею: человеку, который уже
                    # пользуется, выгода понятнее любого описания сервиса.
                    if sale_active():
                        text = texts.TRIAL_ENDING_SALE.format(
                            date=fmt_date(sub_until), **_sale_kwargs(), **common)
                    else:
                        text = texts.TRIAL_ENDING_ACTIVE.format(
                            date=fmt_date(sub_until), price=_month_price(), **common)
                    kb = renew_kb()
                else:
                    text = texts.TRIAL_ENDING.format(**common)
                    kb = connect_kb(token)
                try:
                    await send_letter_to(bot, uid, text, kb)
                except TelegramForbiddenError:
                    db.mark_blocked(uid)
                except Exception:
                    logging.exception("Триал: не смог напомнить %s", uid)
                db.mark_trial_reminded(uid)
        except Exception:
            logging.exception("Ошибка авторассылки напоминаний о триале")


DIGEST_HOUR = int(os.environ.get("DIGEST_HOUR", "21"))     # час по времени сервера
DIGEST_ENABLED = os.environ.get("DIGEST_ENABLED", "1") != "0"


async def daily_digest(bot):
    """Одна сводка в сутки вместо сообщения на каждый пробный период.

    Считаем по базе, а не по счётчику в памяти: перезапуск бота не должен
    терять статистику, а он случается чаще, чем раз в сутки.
    """
    while True:
        await asyncio.sleep(300)
        if not (DIGEST_ENABLED and OWNER_ID):
            continue
        try:
            now = datetime.now()
            # Дата последней отправки лежит в базе, а не в памяти: бот
            # перезапускается при каждом обновлении, и сводка уходила бы
            # по второму разу за те же сутки.
            if now.hour != DIGEST_HOUR or db.meta_get("digest_date") == str(now.date()):
                continue
            s = db.period_summary(int(time.time()) - 86400)
            db.meta_set("digest_date", now.date())
            if not any(s.values()):
                continue                 # пустой день — не тревожим
            lines = [f"📅 <b>Сводка за сутки</b>", "",
                     f"<code>{s['new_users']:>4}</code>  новых пользователей",
                     f"<code>{s['trials']:>4}</code>  получили ключ",
                     f"<code>{s['connected']:>4}</code>  впервые подключились",
                     f"<code>{s['payments']:>4}</code>  оплат"]
            if s["blocked"]:
                lines.append(f"<code>{s['blocked']:>4}</code>  заблокировали бота")
            lines += ["", "Подробнее — /admin"]
            await bot.send_message(OWNER_ID, "\n".join(lines))
        except Exception:
            logging.exception("Ошибка суточной сводки")


async def remind_not_connected(bot):
    """Подталкивает тех, у кого ключ есть, а VPN так и не включён.

    Замеры воронки показали, что теряем людей на самом первом шаге: ключ
    выдан, а кнопку «Подключиться сейчас» человек не нажимал вовсе. У тех,
    кто до страницы подключения дошёл, всё работает — значит чинить надо
    не инструкцию, а сам переход к ней.

    Одно напоминание на человека и не раньше чем через несколько часов:
    сразу после выдачи оно выглядит как слежка, а второе — как навязчивость.
    """
    after = CONNECT_REMIND_AFTER_HOURS * 3600
    while True:
        await asyncio.sleep(CONNECT_REMIND_INTERVAL)
        try:
            now = int(time.time())
            uids = db.connect_reminder_candidates(now, after)
            if not uids:
                continue
            # Один запрос в панель на весь проход вместо запроса на каждого:
            # у user_connected() на каждую проверку идёт свой логин
            online = await asyncio.to_thread(online_usernames)
            if online is None:
                continue                       # панель молчит — повторим позже
            for uid in uids:
                if f"ikk_{uid}" in online:
                    db.mark_connect_reminded(uid)   # уже пользуется, не трогаем
                    continue
                try:
                    await send_letter_to(bot, uid, texts.NOT_CONNECTED,
                                         connect_kb(sync_panel(uid)))
                except TelegramForbiddenError:
                    db.mark_blocked(uid)
                except Exception:
                    pass
                db.mark_connect_reminded(uid)
        except Exception:
            logging.exception("Ошибка напоминания о подключении")


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
                common = dict(ending="ся" if days == 1 else "ось",
                              days=days, word=_plural_days(days),
                              date=fmt_date(sub_until))
                # Продление в разгар акции выгоднее — и об этом стоит
                # сказать ровно тогда, когда человек и так решает, платить ли
                if sale_active():
                    text = texts.SUB_ENDING_SALE.format(**_sale_kwargs(), **common)
                else:
                    text = texts.SUB_ENDING.format(**common)
                try:
                    await send_letter_to(bot, uid, text, renew_kb())
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
                    await send_letter_to(bot, uid, text, ref_share_kb(link))
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
    bot_instance[0] = bot          # для оповещений из фоновых задач

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
    asyncio.create_task(remind_not_connected(bot))
    asyncio.create_task(daily_digest(bot))
    asyncio.create_task(remind_sub_ending(bot))
    asyncio.create_task(ask_for_advocacy(bot))
    asyncio.create_task(warn_traffic_running_out(bot))
    asyncio.create_task(clean_old_broadcasts(bot))
    logging.info("Авторассылки включены: триал за %s дн., продление за %s дн., "
                 "просьба порекомендовать через %s дн.",
                 TRIAL_REMIND_BEFORE_DAYS, SUB_REMIND_BEFORE_DAYS,
                 ADVOCACY_AFTER_DAYS)

    logging.info("IKK VPN bot запущен")
    if OWNER_ID:
        try:
            await bot.send_message(
                OWNER_ID, "🟢 Бот перезапущен. Оповещения о сбоях включены.")
        except Exception:
            logging.exception("Не смог сообщить владельцу о старте")
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
