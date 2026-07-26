"""Клавиатуры (инлайн-кнопки) бота."""
from aiogram.types import WebAppInfo
from aiogram.utils.keyboard import InlineKeyboardBuilder

import lolz
import platega

from .config import (PLANS, PRIVACY_URL, REFERRAL_BONUS_DAYS,
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
        kb.button(text=f"{p['title']} — {price}", callback_data=f"plan:{code}")
    kb.button(text="◀ Назад", callback_data="buy")
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
