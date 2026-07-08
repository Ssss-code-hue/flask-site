"""Клавиатуры (инлайн-кнопки) бота."""
from aiogram.types import WebAppInfo
from aiogram.utils.keyboard import InlineKeyboardBuilder

from .config import (PLANS, PRIVACY_URL, TERMS_URL, TRIAL_DAYS, WEBAPP_URL,
                     OFFER_URL, OWNER_USERNAME)


def main_menu():
    kb = InlineKeyboardBuilder()
    kb.button(text="⭐ Купить подписку", callback_data="buy")
    kb.button(text=f"🆓 Попробовать бесплатно ({TRIAL_DAYS} дн.)", callback_data="trial")
    kb.button(text="📲 Инструкция", callback_data="devices")
    kb.button(text="📡 Моя подписка", callback_data="status")
    kb.button(text="🎁 Пригласить друга (+3 дня)", callback_data="ref")
    kb.button(text="📄 Документы", callback_data="docs")
    kb.button(text="💬 Поддержка", url=f"https://t.me/{OWNER_USERNAME}")
    # «Купить» и «Попробовать» на всю ширину, потом по две в ряд
    kb.adjust(1, 1, 2, 1, 2)
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
    for code, p in PLANS.items():
        kb.button(text=f"{p['title']} — {p['stars']} ⭐", callback_data=f"plan:{code}")
    kb.button(text="◀ Назад", callback_data="buy")
    kb.adjust(1)
    return kb.as_markup()


def devices_kb():
    kb = InlineKeyboardBuilder()
    if WEBAPP_URL.startswith("https://"):
        kb.button(text="📱 Открыть инструкцию", web_app=WebAppInfo(url=WEBAPP_URL))
    kb.button(text="◀ Назад", callback_data="menu")
    kb.adjust(1)
    return kb.as_markup()


def back_kb(target="menu"):
    kb = InlineKeyboardBuilder()
    kb.button(text="◀ Назад", callback_data=target)
    return kb.as_markup()
