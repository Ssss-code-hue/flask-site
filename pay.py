"""Маршруты оплаты подписки через Lolz Merchant (СБП/карта).

/pay/<code>    — создаёт счёт и уводит на страницу оплаты Lolz
/pay/success   — сюда Lolz возвращает покупателя после оплаты
/pay/fail      — сюда после неудачной/отменённой оплаты
/pay/callback  — сервер Lolz сообщает статус оплаты (webhook)
"""
import json
import logging
import os
import uuid

from flask import Blueprint, abort, flash, redirect, request, session, url_for

import lolz
from auth import login_required
from bot import db
from bot.config import PLANS
from bot.panel import get_subscription_url

log = logging.getLogger(__name__)
pay = Blueprint("pay", __name__)

# Публичный адрес сайта — для ссылок возврата и callback
SITE_URL = os.environ.get("SITE_URL", "https://ikkvpn.com").rstrip("/")


@pay.route("/pay/<code>", methods=["POST"])
@login_required
def start(code):
    plan = PLANS.get(code)
    user = db.get_web_user(session["uid"])
    if not plan or not user:
        abort(404)
    if not lolz.is_configured():
        flash("Оплата картой пока не подключена. Используйте бота или напишите в поддержку.")
        return redirect(url_for("tariffs"))

    payment_id = uuid.uuid4().hex  # наш уникальный ID платежа
    try:
        invoice_id, pay_url = lolz.create_invoice(
            amount_rub=plan["rub"],
            payment_id=payment_id,
            comment=f"IKK VPN — подписка «{plan['title']}»",
            success_url=f"{SITE_URL}/pay/success",
            callback_url=f"{SITE_URL}/pay/callback",
            additional_data=json.dumps({"web_uid": user["id"], "plan": code}),
        )
    except Exception:
        log.exception("Lolz: не удалось создать счёт (план %s)", code)
        flash("Не получилось создать платёж. Попробуйте позже или напишите в поддержку.")
        return redirect(url_for("tariffs"))

    db.create_web_payment(payment_id, user["id"], code, plan["rub"])
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


@pay.route("/pay/callback", methods=["POST"])
def callback():
    """Приём статуса от Lolz. Подлинность — заголовок x-secret-key."""
    if not lolz.is_configured():
        abort(404)
    if not lolz.check_secret(request.headers.get("x-secret-key", "")):
        log.warning("Lolz callback: неверный x-secret-key")
        abort(403)

    data = request.get_json(silent=True) or {}
    payment_id = str(data.get("payment_id") or "")
    status = data.get("status")
    rec = db.get_web_payment(payment_id) if payment_id else None
    if not rec:
        # Отвечаем 200, чтобы Lolz не слал повторы: платёж не наш
        log.warning("Lolz callback: неизвестный платёж %s (%s)", payment_id, status)
        return {"ok": True}

    if status == "paid" and rec["status"] == "PENDING":
        plan = PLANS.get(rec["plan"], {})
        days = plan.get("days", 30)
        new_until = db.web_add_days(rec["web_user_id"], days)
        sub_url = get_subscription_url(f"web_{rec['web_user_id']}", new_until)
        if sub_url:
            db.web_activate_sub(rec["web_user_id"], new_until, sub_url)
        else:
            log.error("Lolz: оплата %s зачислена, но панель не выдала ключ "
                      "(web_user %s)", payment_id, rec["web_user_id"])
        db.set_web_payment_status(payment_id, "CONFIRMED")
        log.info("Lolz: подтверждена оплата %s — web_user %s, план %s (+%s дн.)",
                 payment_id, rec["web_user_id"], rec["plan"], days)

    return {"ok": True}
