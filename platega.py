"""Оплата картой/СБП через platega.io.

Как это работает:
  1. Сайт создаёт транзакцию (create_payment) и отправляет покупателя
     на страницу оплаты Platega (redirect).
  2. После оплаты Platega присылает callback на /platega/callback
     со статусом CONFIRMED — сайт продлевает подписку и выдаёт ключ.

Настройка через переменные окружения:
  PLATEGA_MERCHANT_ID — идентификатор магазина (из ЛК Platega, «Настройки»)
  PLATEGA_SECRET      — API-ключ (там же)
  PLATEGA_METHOD      — способ оплаты: 2 = СБП (по умолчанию), 11 = карта РФ,
                        12 = зарубежная карта, 13 = криптовалюта
  PLATEGA_URL         — база API (по умолчанию https://app.platega.io)

Callback URL указывается в ЛК Platega: Настройки → Callback URLs →
https://ikkvpn.com/platega/callback (только HTTPS).
"""
import os

import requests

MERCHANT_ID = os.environ.get("PLATEGA_MERCHANT_ID", "")
SECRET = os.environ.get("PLATEGA_SECRET", "")
BASE = os.environ.get("PLATEGA_URL", "https://app.platega.io").rstrip("/")
METHOD = int(os.environ.get("PLATEGA_METHOD", "2"))
TIMEOUT = 25


def is_configured():
    return bool(MERCHANT_ID and SECRET)


def _headers():
    return {
        "X-MerchantId": MERCHANT_ID,
        "X-Secret": SECRET,
        "Content-Type": "application/json",
    }


def create_payment(amount_rub, description, payload, return_url, failed_url,
                   user_id, user_name):
    """Создаёт транзакцию, возвращает (transaction_id, ссылка_на_оплату)."""
    body = {
        "paymentMethod": METHOD,
        # Сумма передаётся в копейках
        "paymentDetails": {"amount": int(amount_rub) * 100, "currency": "RUB"},
        "description": description,
        "return": return_url,
        "failedUrl": failed_url,
        # payload вернётся в callback как есть — кладём туда свои данные
        "payload": payload,
        "metadata": {"userId": str(user_id), "userName": str(user_name)},
    }
    r = requests.post(
        f"{BASE}/transaction/process",
        headers=_headers(), json=body, timeout=TIMEOUT,
    )
    r.raise_for_status()
    d = r.json()
    return d["transactionId"], d["redirect"]


def get_status(tx_id):
    """Статус транзакции: PENDING / CONFIRMED / CANCELED / CHARGEBACKED."""
    r = requests.get(
        f"{BASE}/transaction/{tx_id}",
        headers=_headers(), timeout=TIMEOUT,
    )
    r.raise_for_status()
    return r.json().get("status")
