"""REST API сервиса IKK VPN (документируется в Swagger UI на /apidocs).

Использует ту же базу SQLite, что и Telegram-бот (тарифы, подписки, рефералы).
"""
from datetime import datetime

from flask import Blueprint, jsonify

from bot import db
from bot.config import BOT_USERNAME, PLANS, REFERRAL_BONUS_DAYS

api = Blueprint("api", __name__, url_prefix="/api")


@api.get("/health")
def health():
    """
    Проверка работоспособности API.
    ---
    tags:
      - Система
    responses:
      200:
        description: Сервис работает
        examples:
          application/json: {"status": "ok"}
    """
    return jsonify({"status": "ok"})


@api.get("/plans")
def plans():
    """
    Список тарифов IKK VPN.
    ---
    tags:
      - Тарифы
    responses:
      200:
        description: Массив тарифов (код, название, дней, цена в звёздах)
    """
    data = [
        {"code": code, "title": p["title"], "days": p["days"], "stars": p["stars"]}
        for code, p in PLANS.items()
    ]
    return jsonify(data)


@api.get("/plans/<code>")
def plan_detail(code):
    """
    Один тариф по коду (1m, 3m, 12m).
    ---
    tags:
      - Тарифы
    parameters:
      - in: path
        name: code
        type: string
        required: true
        description: Код тарифа (1m, 3m или 12m)
    responses:
      200:
        description: Тариф (код, название, дней, цена в звёздах)
      404:
        description: Тариф не найден
    """
    p = PLANS.get(code)
    if not p:
        return jsonify({"error": "plan not found"}), 404
    return jsonify({"code": code, "title": p["title"], "days": p["days"], "stars": p["stars"]})


@api.get("/payments/<int:user_id>")
def payments(user_id):
    """
    История платежей пользователя.
    ---
    tags:
      - Подписки
    parameters:
      - in: path
        name: user_id
        type: integer
        required: true
        description: Telegram ID пользователя
    responses:
      200:
        description: Список платежей (тариф, звёзды, дата) — свежие сверху
      404:
        description: Пользователь не найден
    """
    if not db.get_user(user_id):
        return jsonify({"error": "user not found"}), 404
    items = [
        {
            "plan": p["plan"],
            "stars": p["stars"],
            "date": datetime.fromtimestamp(p["created_at"]).strftime("%d.%m.%Y %H:%M"),
        }
        for p in db.get_payments(user_id)
    ]
    return jsonify({"user_id": user_id, "count": len(items), "payments": items})


@api.get("/web/stats")
def web_stats():
    """
    Статистика аккаунтов на сайте (регистрация по почте).
    ---
    tags:
      - Система
    responses:
      200:
        description: Всего аккаунтов и сколько из них подтвердили почту
    """
    return jsonify(db.web_stats())


@api.get("/subscription/<int:user_id>")
def subscription(user_id):
    """
    Статус подписки пользователя.
    ---
    tags:
      - Подписки
    parameters:
      - in: path
        name: user_id
        type: integer
        required: true
        description: Telegram ID пользователя
    responses:
      200:
        description: Статус подписки
      404:
        description: Пользователь не найден
    """
    u = db.get_user(user_id)
    if not u:
        return jsonify({"error": "user not found"}), 404
    until = u["sub_until"] or 0
    active = db.is_active(user_id)
    return jsonify({
        "user_id": user_id,
        "active": active,
        "until": until,
        "until_human": datetime.fromtimestamp(until).strftime("%d.%m.%Y") if until else None,
    })


@api.get("/referral/<int:user_id>")
def referral(user_id):
    """
    Реферальная ссылка пользователя и статистика приглашений.
    ---
    tags:
      - Рефералы
    parameters:
      - in: path
        name: user_id
        type: integer
        required: true
        description: Telegram ID пользователя
    responses:
      200:
        description: Ссылка и количество приглашённых
    """
    count = db.count_referrals(user_id)
    return jsonify({
        "user_id": user_id,
        "link": f"https://t.me/{BOT_USERNAME}?start=ref_{user_id}",
        "bonus_days": REFERRAL_BONUS_DAYS,
        "invited": count,
        "days_earned": count * REFERRAL_BONUS_DAYS,
    })


@api.get("/stats")
def stats():
    """
    Общая статистика сервиса.
    ---
    tags:
      - Система
    responses:
      200:
        description: Пользователи, активные подписки, платежи, звёзды
    """
    return jsonify(db.stats())
