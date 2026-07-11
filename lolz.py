"""Оплата картой/СБП через Lolz Merchant (lzt.market) — счета (invoice).

Как это работает:
  1. Сайт создаёт счёт (create_invoice) и отправляет покупателя
     на страницу оплаты Lolz (invoice.url).
  2. После оплаты Lolz присылает webhook на /pay/callback со status=paid —
     сайт продлевает подписку и выдаёт ключ.

Настройка через переменные окружения:
  LOLZ_TOKEN           — Access Token с scope invoice (lzt.market → API)
  LOLZ_MERCHANT_ID     — ID мерчанта (число), из настроек мерчанта
  LOLZ_MERCHANT_SECRET — merchant token; приходит в заголовке x-secret-key
                         вебхука, им проверяем подлинность callback'а
  LOLZ_URL             — база API (по умолчанию https://api.lzt.market)

Callback URL задаётся при создании счёта (url_callback), отдельно
в кабинете настраивать не нужно.
"""
import os

import requests

TOKEN = os.environ.get("LOLZ_TOKEN", "").strip()
MERCHANT_ID = os.environ.get("LOLZ_MERCHANT_ID", "").strip()
SECRET = os.environ.get("LOLZ_MERCHANT_SECRET", "").strip()
BASE = os.environ.get("LOLZ_URL", "https://api.lzt.market").rstrip("/")
TIMEOUT = 25


def is_configured():
    return bool(TOKEN and MERCHANT_ID and SECRET)


def can_invoice():
    """Достаточно ли настроек для создания и проверки счетов.

    Вебхучный секрет не нужен: бот не принимает callback'и,
    а сам опрашивает статус счёта через API.
    """
    return bool(TOKEN and MERCHANT_ID)


def _headers():
    # Токен — только латиница и цифры. Кириллица появляется, если его вставляли
    # в .env через веб-консоль с русской раскладкой: ловим сразу и понятно.
    if not TOKEN.isascii():
        raise ValueError(
            "LOLZ_TOKEN содержит не-ASCII символы — токен испорчен при вставке в .env"
        )
    return {"Authorization": f"Bearer {TOKEN}", "Accept": "application/json"}


def create_invoice(amount_rub, payment_id, comment, success_url, callback_url,
                   additional_data=""):
    """Создаёт счёт, возвращает (invoice_id, ссылка_на_оплату).

    ВАЖНО: сумма в Lolz указывается в рублях (не в копейках).
    Параметры LZT-маркета передаются в query-строке.
    """
    params = {
        "currency": "rub",
        "amount": amount_rub,
        "payment_id": payment_id,          # наш уникальный ID (вернётся в webhook)
        "comment": comment,
        "url_success": success_url,
        "url_callback": callback_url,
        "merchant_id": int(MERCHANT_ID),
        "lifetime": 3600,                  # счёт живёт 1 час
    }
    if additional_data:
        params["additional_data"] = additional_data
    r = requests.post(f"{BASE}/invoice", headers=_headers(), params=params, timeout=TIMEOUT)
    r.raise_for_status()
    inv = r.json()["invoice"]
    return inv["invoice_id"], inv["url"]


def check_secret(header_value):
    """Проверка подлинности webhook: x-secret-key должен совпасть с merchant token."""
    from hmac import compare_digest
    return bool(header_value) and compare_digest(str(header_value), SECRET)


def get_invoice(payment_id):
    """Возвращает счёт по нашему payment_id (dict) или None.

    Используется ботом: вебхук Lolz приходит на сайт, а у бота своя
    база — поэтому бот сам опрашивает статус (status: paid/not_paid).
    """
    r = requests.get(
        f"{BASE}/invoice",
        headers=_headers(),
        params={"payment_id": payment_id},
        timeout=TIMEOUT,
    )
    r.raise_for_status()
    data = r.json()
    inv = data.get("invoice") or data.get("invoices")
    if isinstance(inv, list):
        inv = inv[0] if inv else None
    return inv
