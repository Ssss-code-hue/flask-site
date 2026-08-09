"""Клавиатуры (инлайн-кнопки) бота."""
from urllib.parse import quote

from aiogram.types import WebAppInfo
from aiogram.utils.keyboard import InlineKeyboardBuilder

try:                                    # нативная кнопка «копировать» (Bot API 7.11+)
    from aiogram.types import CopyTextButton
except ImportError:                     # на старой aiogram — обойдёмся без неё
    CopyTextButton = None

import lolz
import platega

from .config import (GIVEAWAY_CHANNEL, PLANS, PRIVACY_URL, REFERRAL_BONUS_DAYS,
                     SALE_BONUS_DAYS, SALE_PLAN, plan_days, sale_active,
                     SUPPORT_BOT_USERNAME, TERMS_URL, TRIAL_DAYS,
                     WEBAPP_URL, OFFER_URL)


def card_available():
    """Доступна ли оплата картой/СБП (настроена любая из касс)."""
    return platega.is_configured() or lolz.can_invoice()


def main_menu():
    kb = InlineKeyboardBuilder()
    kb.button(text="⭐ Купить подписку", callback_data="buy")
    kb.button(text=f"🆓 Попробовать бесплатно ({TRIAL_DAYS} дн.)", callback_data="trial")
    kb.button(text="🎁 Промокод", callback_data="promo")
    kb.button(text="📡 Моя подписка", callback_data="status")
    kb.button(text=f"🎁 Пригласить друга (+{REFERRAL_BONUS_DAYS} дн.)", callback_data="ref")
    kb.button(text="📄 Документы", callback_data="docs")
    kb.button(text="💬 Поддержка", url=f"https://t.me/{SUPPORT_BOT_USERNAME}")
    # «Купить» и «Попробовать» на всю ширину, «Промокод»+«Моя подписка» в ряд, дальше по две
    kb.adjust(1, 1, 2, 2, 2)
    return kb.as_markup()


def promo_offer_kb(code, bonus_days):
    """Кнопка активации промокода в одно нажатие (для рассылки)."""
    kb = InlineKeyboardBuilder()
    kb.button(text=f"🎁 Получить +{bonus_days} дней", callback_data=f"promo_go:{code}")
    kb.button(text="◀ Меню", callback_data="menu")
    kb.adjust(1)
    return kb.as_markup()


def ref_share_kb(link, bonus_days=REFERRAL_BONUS_DAYS):
    """Клавиатура под рассылкой про рефералов: скопировать личную ссылку
    и отправить её другу.

    «Скопировать» — нативная кнопка Telegram (копирует в буфер без открытия
    чего-либо). На старых версиях aiogram её нет, тогда остаётся «Отправить
    другу» — ссылка всё равно есть в тексте сообщения тегом <code> (по тапу
    копируется)."""
    kb = InlineKeyboardBuilder()
    if CopyTextButton is not None:
        kb.button(text="📋 Скопировать ссылку", copy_text=CopyTextButton(text=link))
    # Telegram в share/url ставит сначала ссылку, потом текст, а превью для
    # t.me-ссылок со start-параметром не показывает — поэтому текст должен
    # сам объяснять, куда ведёт ссылка.
    pitch = (f"⬆️ Это бот 🛡 IKK VPN — быстрый VPN без логов и рекламы.\n"
             f"Открой ссылку выше, нажми «Запустить» — "
             f"и получи {TRIAL_DAYS} дней бесплатно 🎁")
    share = (f"https://t.me/share/url?url={quote(link, safe='')}"
             f"&text={quote(pitch, safe='')}")
    kb.button(text="📤 Отправить другу", url=share)
    kb.button(text="◀ Меню", callback_data="menu")
    kb.adjust(1)
    return kb.as_markup()


def admin_kb():
    """Главный экран панели владельца."""
    kb = InlineKeyboardBuilder()
    kb.button(text="📊 Статистика", callback_data="adm:stats")
    kb.button(text="📉 Воронка подключения", callback_data="adm:funnel")
    kb.button(text="🚦 Источники", callback_data="adm:sources")
    kb.button(text="🤝 Кто приводит людей", callback_data="adm:refs")
    kb.button(text="🎁 Розыгрыш", callback_data="adm:gw")
    kb.button(text="📢 Рассылки", callback_data="adm:bc")
    kb.adjust(1)
    return kb.as_markup()


def admin_back_kb(refresh=None):
    """«Обновить» + «Назад» под отчётом.

    Обновление отдельной кнопкой, потому что цифры смотрят по нескольку
    раз подряд — гонять команду заново неудобно.
    """
    kb = InlineKeyboardBuilder()
    if refresh:
        kb.button(text="🔄 Обновить", callback_data=refresh)
    kb.button(text="◀ В панель", callback_data="adm:home")
    kb.adjust(1)
    return kb.as_markup()


# Рассылки: код действия → (подпись кнопки, кому уходит).
# Держим одним словарём, чтобы кнопка, экран подтверждения и запуск
# не разъехались в трёх разных местах.
BROADCASTS = {
    "lapsed": ("Вернитесь (+промокод)", "у кого подписка закончилась"),
    "nc":     ("Вы не подключились", "с активной подпиской, но без единого подключения"),
    "ref":    ("Про рефералов", "всем живым"),
    "gift":   ("Подарок за приглашение", "всем живым · выдача вручную по скриншоту"),
    "promo":  ("Промокод", "всем живым"),
    "sale":   ("Акция", "всем живым"),
}


def admin_broadcasts_kb():
    kb = InlineKeyboardBuilder()
    for code, (title, _) in BROADCASTS.items():
        kb.button(text=f"📢 {title}", callback_data=f"adm:bc:{code}")
    kb.button(text="◀ В панель", callback_data="adm:home")
    kb.adjust(1)
    return kb.as_markup()


def admin_confirm_kb(code):
    """Подтверждение рассылки.

    Обязательный второй шаг: в панели кнопки стоят вплотную, и случайное
    нажатие означало бы сообщение всем пользователям сразу. Отменить
    отправленное нельзя.
    """
    kb = InlineKeyboardBuilder()
    kb.button(text="✅ Да, разослать", callback_data=f"adm:go:{code}")
    kb.button(text="◀ Отмена", callback_data="adm:bc")
    kb.adjust(1)
    return kb.as_markup()


def admin_giveaway_kb(active):
    kb = InlineKeyboardBuilder()
    if active:
        kb.button(text="👥 Список участников", callback_data="adm:gw:list")
        kb.button(text="🏆 Выбрать победителя", callback_data="adm:gw:pick")
        kb.button(text="📮 Опубликовать пост в канал", callback_data="adm:gw:post")
    kb.button(text="◀ В панель", callback_data="adm:home")
    kb.adjust(1)
    return kb.as_markup()


def giveaway_kb(need_key=False, need_sub=False):
    """Клавиатура экрана розыгрыша.

    Кнопка проверки есть всегда — она же кнопка «повторить». Недостающий
    шаг подсвечиваем отдельной кнопкой, чтобы человеку не приходилось
    искать канал или триал самому: каждый лишний поиск теряет участника.
    """
    kb = InlineKeyboardBuilder()
    if need_key:
        kb.button(text=f"🆓 Включить {TRIAL_DAYS} дней бесплатно",
                  callback_data="trial")
    if need_sub:
        ch = GIVEAWAY_CHANNEL.lstrip("@")
        kb.button(text="📢 Подписаться на канал", url=f"https://t.me/{ch}")
    kb.button(text="✅ Проверить и участвовать", callback_data="gw_check")
    kb.button(text="◀ Меню", callback_data="menu")
    kb.adjust(1)
    return kb.as_markup()


def giveaway_post_kb(bot_username, gift_stars=None):
    """Кнопка под постом розыгрыша.

    В СВОЁМ канале пост публикует наш бот, поэтому callback до него дойдёт
    и проверка отработает прямо там. В купленном канале постит их админ —
    нажатие ушло бы его боту, поэтому туда идёт обычная ссылка в бота.
    """
    kb = InlineKeyboardBuilder()
    kb.button(text="✅ Проверить подписки и участвовать",
              callback_data="gw_check")
    if gift_stars:
        kb.button(text=f"🎁 Пригласи друга — подарок {gift_stars} ⭐",
                  url=f"https://t.me/{bot_username}?start=gift")
    kb.button(text="🤖 Открыть бота",
              url=f"https://t.me/{bot_username}?start=giveaway")
    kb.adjust(1)
    return kb.as_markup()


def connect_kb(sub_token=None):
    """Клавиатура при активной подписке: одна большая кнопка на страницу
    подключения + возврат в меню.

    Ссылку открываем обычной URL-кнопкой (в браузере), а НЕ web_app: внутри
    мини-приложения Telegram кастомная схема v2raytun:// не открывается, а
    в браузере — работает."""
    kb = InlineKeyboardBuilder()
    if sub_token and WEBAPP_URL.startswith("https://"):
        kb.button(text="🚀 ПОДКЛЮЧИТЬСЯ СЕЙЧАС!", url=f"{WEBAPP_URL}?t={sub_token}")
    kb.button(text="◀ Назад", callback_data="menu")
    kb.adjust(1)
    return kb.as_markup()


def paid_kb(sub_token=None):
    """После оплаты: подключиться + позвать друга.

    Момент сразу после покупки — единственный, когда человек точно доволен и
    готов рекомендовать. Прятать реферальную ссылку в меню значит потерять его.
    """
    kb = InlineKeyboardBuilder()
    if sub_token and WEBAPP_URL.startswith("https://"):
        kb.button(text="🚀 ПОДКЛЮЧИТЬСЯ СЕЙЧАС!", url=f"{WEBAPP_URL}?t={sub_token}")
    kb.button(text=f"🎁 Позвать друга (+{REFERRAL_BONUS_DAYS} дн.)", callback_data="ref")
    kb.button(text="◀ Меню", callback_data="menu")
    kb.adjust(1)
    return kb.as_markup()


def renew_kb():
    """Под напоминанием об окончании подписки — сразу к тарифам."""
    kb = InlineKeyboardBuilder()
    kb.button(text="⭐ Продлить подписку", callback_data="buy")
    kb.button(text="◀ Меню", callback_data="menu")
    kb.adjust(1)
    return kb.as_markup()


def docs_kb():
    """Документы сервиса: оферта, соглашение, политика — мини-приложениями."""
    kb = InlineKeyboardBuilder()
    kb.button(text="📄 Публичная оферта", web_app=WebAppInfo(url=OFFER_URL))
    kb.button(text="📜 Пользовательское соглашение", web_app=WebAppInfo(url=TERMS_URL))
    kb.button(text="🔒 Политика конфиденциальности", web_app=WebAppInfo(url=PRIVACY_URL))
    kb.button(text="◀ Назад", callback_data="menu")
    kb.adjust(1)
    return kb.as_markup()


def offer_consent_kb():
    kb = InlineKeyboardBuilder()
    if OFFER_URL.startswith("https://"):
        kb.button(text="📄 Открыть оферту", web_app=WebAppInfo(url=OFFER_URL))
    else:
        kb.button(text="📄 Читать оферту", callback_data="offer_text")
    kb.button(text="✅ Принимаю — к тарифам", callback_data="plans")
    kb.button(text="◀ Назад", callback_data="menu")
    kb.adjust(1)
    return kb.as_markup()


def trial_consent_kb():
    """Согласие с офертой перед активацией пробного периода."""
    kb = InlineKeyboardBuilder()
    if OFFER_URL.startswith("https://"):
        kb.button(text="📄 Открыть оферту", web_app=WebAppInfo(url=OFFER_URL))
    else:
        kb.button(text="📄 Читать оферту", callback_data="offer_text_trial")
    kb.button(text="✅ Принимаю — активировать", callback_data="trial_go")
    kb.button(text="◀ Назад", callback_data="menu")
    kb.adjust(1)
    return kb.as_markup()


def plans_kb():
    kb = InlineKeyboardBuilder()
    card = card_available()     # показываем ₽ только когда карта включена
    for code, p in PLANS.items():
        price = f"{p['stars']} ⭐ / {p['rub']} ₽" if card else f"{p['stars']} ⭐"
        # пока идёт акция — сразу видно, на каком тарифе бонус
        bonus = f" +{SALE_BONUS_DAYS} дн. 🎁" if code == SALE_PLAN and sale_active() else ""
        kb.button(text=f"{p['title']}{bonus} — {price}", callback_data=f"plan:{code}")
    kb.button(text="◀ Назад", callback_data="buy")
    kb.adjust(1)
    return kb.as_markup()


def sale_kb():
    """Кнопка из рассылки об акции — ведёт в обычную покупку через оферту."""
    kb = InlineKeyboardBuilder()
    p = PLANS.get(SALE_PLAN, {})
    price = f"{p.get('rub')} ₽" if card_available() else f"{p.get('stars')} ⭐"
    kb.button(text=f"🎁 Забрать {plan_days(SALE_PLAN)} дней за {price}",
              callback_data="buy")
    kb.button(text="◀ Меню", callback_data="menu")
    kb.adjust(1)
    return kb.as_markup()


def pay_method_kb(code):
    """Выбор способа оплаты: звёзды + отдельная кнопка на каждую кассу."""
    p = PLANS[code]
    kb = InlineKeyboardBuilder()
    kb.button(text=f"⭐ Звёзды Telegram — {p['stars']} ⭐", callback_data=f"paystars:{code}")
    # обе кассы — если настроены; название показываем, когда касс больше одной
    cards = []
    if platega.is_configured():
        cards.append(("platega", "Platega"))
    if lolz.can_invoice():
        cards.append(("lolz", "Lolzteam"))
    for pid, label in cards:
        suffix = f" · {label}" if len(cards) > 1 else ""
        kb.button(text=f"💳 СБП / карта{suffix} — {p['rub']} ₽",
                  callback_data=f"paycard:{pid}:{code}")
    kb.button(text="◀ Назад", callback_data="plans")
    kb.adjust(1)
    return kb.as_markup()


def card_invoice_kb(pay_url, payment_id):
    """Счёт Lolz: оплатить и проверить зачисление."""
    kb = InlineKeyboardBuilder()
    kb.button(text="💳 Оплатить", url=pay_url)
    kb.button(text="✅ Я оплатил — проверить", callback_data=f"paycheck:{payment_id}")
    kb.button(text="◀ Назад к тарифам", callback_data="plans")
    kb.adjust(1)
    return kb.as_markup()


def devices_kb(sub_token=None):
    """Клавиатура под ключом. Если передан токен подписки — открываем
    персональную страницу-подключайку (кнопка «Добавить подписку» там
    импортирует ключ в одно нажатие); иначе — общую инструкцию."""
    kb = InlineKeyboardBuilder()
    if WEBAPP_URL.startswith("https://"):
        url = f"{WEBAPP_URL}?t={sub_token}" if sub_token else WEBAPP_URL
        text = "🚀 Подключить" if sub_token else "📱 Открыть инструкцию"
        kb.button(text=text, web_app=WebAppInfo(url=url))
    kb.button(text="◀ Назад", callback_data="menu")
    kb.adjust(1)
    return kb.as_markup()


def back_kb(target="menu"):
    kb = InlineKeyboardBuilder()
    kb.button(text="◀ Назад", callback_data=target)
    return kb.as_markup()
