"""Маршруты оплаты подписки через Platega (СБП/карта).

/pay/<code>        — создаёт платёж и уводит на страницу оплаты Platega
/pay/success       — сюда Platega возвращает покупателя после оплаты
/pay/fail          — сюда после неудачной оплаты
/platega/callback  — сервер Platega сообщает статус (CONFIRMED и т.д.)
"""
import json
import logging
import os
from hmac import compare_digest

from flask import Blueprint, abort, flash, redirect, request, session, url_for

import platega
from auth import login_required
from bot import db
from bot.config import PLANS
from bot.panel import get_subscription_url

log = logging.getLogger(__name__)
pay = Blueprint("pay", __name__)

# Публичный адрес сайта — для ссылок возврата с платёжной страницы
SITE_URL = os.environ.get("SITE_URL", "https://ikkvpn.com").rstrip("/")


@pay.route("/pay/<code>", methods=["POST"])
@login_required
def start(code):
    plan = PLANS.get(code)
    user = db.get_web_user(session["uid"])
    if not plan or not user:
        abort(404)
    if not platega.is_configured():
        flash("Оплата картой пока не подключена. Используйте бота или напишите в поддержку.")
        return redirect(url_for("tariffs"))

    try:
        tx_id, pay_url = platega.create_payment(
            amount_rub=plan["rub"],
            description=f"IKK VPN — подписка «{plan['title']}»",
            payload=json.dumps({"web_uid": user["id"], "plan": code}),
            return_url=f"{SITE_URL}/pay/success",
            failed_url=f"{SITE_URL}/pay/fail",
            user_id=user["id"],
            user_name=user["email"],
        )
    except Exception:
        log.exception("Platega: не удалось создать платёж (план %s)", code)
        flash("Не получилось создать платёж. Попробуйте позже или напишите в поддержку.")
        return redirect(url_for("tariffs"))

    db.create_web_payment(tx_id, user["id"], code, plan["rub"])
    return redirect(pay_url)


@pay.route("/pay/success")
def success():
    flash("Оплата прошла! Подписка активируется в течение минуты — "
          "ключ появится в разделе «VPN-ключ» (обновите страницу).")
    return redirect(url_for("auth.account"))


@pay.route("/pay/fail")
def fail():
    flash("Оплата не прошла или была отменена. Можно попробовать ещё раз.")
    return redirect(url_for("tariffs"))


@pay.route("/platega/callback", methods=["POST"])
def callback():
    """Приём статуса от Platega. Аутентификация — те же заголовки, что и в API."""
    if not platega.is_configured():
        abort(404)
    mid = request.headers.get("X-MerchantId", "")
    sec = request.headers.get("X-Secret", "")
    if not (compare_digest(mid, platega.MERCHANT_ID) and compare_digest(sec, platega.SECRET)):
        log.warning("Platega callback: неверные заголовки авторизации")
        abort(403)

    data = request.get_json(silent=True) or {}
    tx_id = str(data.get("id") or "")
    status = data.get("status")
    rec = db.get_web_payment(tx_id) if tx_id else None
    if not rec:
        # Отвечаем 200, чтобы Platega не слала повторы: транзакция не наша
        log.warning("Platega callback: неизвестная транзакция %s (%s)", tx_id, status)
        return {"ok": True}

    if status == "CONFIRMED" and rec["status"] == "PENDING":
        plan = PLANS.get(rec["plan"], {})
        days = plan.get("days", 30)
        new_until = db.web_add_days(rec["web_user_id"], days)
        sub_url = get_subscription_url(f"web_{rec['web_user_id']}", new_until)
        if sub_url:
            db.web_activate_sub(rec["web_user_id"], new_until, sub_url)
        else:
            # Дни зачислены, но панель не ответила — ключ появится при
            # следующей выдаче/продлении; смотрим логи.
            log.error("Platega: оплата %s зачислена, но панель не выдала ключ "
                      "(web_user %s)", tx_id, rec["web_user_id"])
        db.set_web_payment_status(tx_id, "CONFIRMED")
        log.info("Platega: подтверждена оплата %s — web_user %s, план %s (+%s дн.)",
                 tx_id, rec["web_user_id"], rec["plan"], days)
    elif status in ("CANCELED", "CHARGEBACKED"):
        db.set_web_payment_status(tx_id, status)

    return {"ok": True}
