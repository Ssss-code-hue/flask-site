"""Оплата через кассу Platega (platega.io).

Как это работает:
  1. Создаём платёж (create_payment) — Platega отдаёт ссылку на страницу
     оплаты, где покупатель сам выбирает способ (СБП, карта и т.д.).
  2. Сайт узнаёт об оплате из callback'а (POST на /pay/platega/callback,
     подлинность — заголовки X-MerchantId/X-Secret должны совпасть с нашими).
  3. Бот вебхуков не имеет — он опрашивает статус сам (get_status).

Настройка через переменные окружения:
  PLATEGA_MERCHANT_ID — ID кассы (UUID из кабинета my.platega.io)
  PLATEGA_SECRET      — API-ключ кассы (страница «Настройки»)
  PLATEGA_URL         — база API (по умолчанию https://app.platega.io)

У сайта и бота — свои кассы (свои пары ID+ключ). Callback URL кассы
сайта задаётся в кабинете Platega: Настройки → Callback URLs.
"""
import os

import requests

MERCHANT_ID = os.environ.get("PLATEGA_MERCHANT_ID", "").strip()
SECRET = os.environ.get("PLATEGA_SECRET", "").strip()
BASE = os.environ.get("PLATEGA_URL", "https://app.platega.io").rstrip("/")
TIMEOUT = 25

# статусы транзакций Platega
PENDING, CONFIRMED, CANCELED, CHARGEBACKED = (
    "PENDING", "CONFIRMED", "CANCELED", "CHARGEBACKED")


def is_configured():
    return bool(MERCHANT_ID and SECRET)


def _headers():
    if not (MERCHANT_ID.isascii() and SECRET.isascii()):
        raise ValueError("PLATEGA_MERCHANT_ID/PLATEGA_SECRET содержат не-ASCII "
                         "символы — значения испорчены при вставке в .env")
    return {"X-MerchantId": MERCHANT_ID, "X-Secret": SECRET}


def create_payment(amount_rub, description, return_url, failed_url, payload=""):
    """Создаёт платёж, возвращает (transaction_id, ссылка_на_оплату).

    Метод оплаты не задаём — покупатель выберет его на странице Platega.
    payload вернётся в callback'е как есть (кладём туда свои данные).
    """
    body = {
        "paymentDetails": {"amount": int(amount_rub), "currency": "RUB"},
        "description": description,
        "return": return_url,
        "failedUrl": failed_url,
        "payload": payload,
    }
    r = requests.post(f"{BASE}/v2/transaction/process",
                      headers=_headers(), json=body, timeout=TIMEOUT)
    r.raise_for_status()
    data = r.json()
    return data["transactionId"], data["url"]


def get_status(tx_id):
    """Статус транзакции: PENDING/CONFIRMED/CANCELED/CHARGEBACKED, None — не найдена."""
    r = requests.get(f"{BASE}/transaction/{tx_id}", headers=_headers(), timeout=TIMEOUT)
    if r.status_code == 404:
        return None
    r.raise_for_status()
    return r.json().get("status")


def check_callback_auth(headers):
    """Подлинность callback'а: заголовки должны совпасть с нашими ключами."""
    from hmac import compare_digest
    mid = str(headers.get("X-MerchantId", ""))
    sec = str(headers.get("X-Secret", ""))
    return (bool(mid) and bool(sec)
            and compare_digest(mid, MERCHANT_ID)
            and compare_digest(sec, SECRET))
