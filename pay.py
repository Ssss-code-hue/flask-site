"""Маршруты оплаты подписки на сайте: Platega (СБП/карта) и Lolz Merchant.

/pay/<code>            — создаёт платёж и уводит на страницу оплаты
/pay/success           — сюда покупатель возвращается после оплаты
/pay/fail              — сюда после неудачной/отменённой оплаты
/pay/callback          — webhook Lolz (x-secret-key)
/pay/platega/callback  — webhook Platega (X-MerchantId/X-Secret)

Активный провайдер: PAY_PROVIDER=platega|lolz; если не задан —
Platega при заполненных ключах, иначе Lolz.
"""
import json
import logging
import os
import uuid

from flask import (Blueprint, abort, flash, redirect, render_template, request,
                   session, url_for)

import lolz
import platega
from auth import login_required
from bot import db
from bot.config import PLANS
from bot.panel import get_subscription_url

log = logging.getLogger(__name__)
pay = Blueprint("pay", __name__)

# Публичный адрес сайта — для ссылок возврата и callback
SITE_URL = os.environ.get("SITE_URL", "https://ikkvpn.com").rstrip("/")
PAY_PROVIDER = os.environ.get("PAY_PROVIDER", "").strip().lower()


# человекочитаемые названия касс для кнопок
PROVIDER_LABELS = {"platega": "Platega", "lolz": "Lolzteam"}


def _is_ready(provider):
    return platega.is_configured() if provider == "platega" else lolz.is_configured()


def available_providers():
    """Список настроенных касс: [('platega','Platega'), ('lolz','Lolzteam')].

    Если задан PAY_PROVIDER — оставляем только его (принудительный выбор).
    """
    order = [PAY_PROVIDER] if PAY_PROVIDER in ("platega", "lolz") else ["platega", "lolz"]
    return [(p, PROVIDER_LABELS[p]) for p in order if _is_ready(p)]


def active_provider():
    """Провайдер по умолчанию (первый доступный)."""
    prov = available_providers()
    return prov[0][0] if prov else "platega"


def _payments_available():
    return bool(available_providers())


@pay.route("/pay/<code>", methods=["GET"])
@login_required
def checkout(code):
    """Страница выбора кассы для тарифа (открывается кнопкой «Оплатить»)."""
    plan = PLANS.get(code)
    if not plan:
        abort(404)
    if not _payments_available():
        flash("Оплата картой пока не подключена. Используйте бота или напишите в поддержку.")
        return redirect(url_for("tariffs"))
    return render_template("checkout.html", plan=plan, code=code,
                           providers=available_providers())


@pay.route("/pay/<code>", methods=["POST"])
@login_required
def start(code):
    plan = PLANS.get(code)
    user = db.get_web_user(session["uid"])
    if not plan or not user:
        abort(404)
    if not _payments_available():
        flash("Оплата картой пока не подключена. Используйте бота или напишите в поддержку.")
        return redirect(url_for("tariffs"))

    # провайдер выбирает пользователь на странице тарифов; если выбранная
    # касса не настроена — берём первую доступную
    provider = request.form.get("provider", "")
    if provider not in ("platega", "lolz") or not _is_ready(provider):
        provider = active_provider()
    try:
        if provider == "platega":
            tx_id, pay_url = platega.create_payment(
                amount_rub=plan["rub"],
                description=f"IKK VPN — подписка «{plan['title']}»",
                return_url=f"{SITE_URL}/pay/success",
                failed_url=f"{SITE_URL}/pay/fail",
                payload=json.dumps({"web_uid": user["id"], "plan": code}),
            )
        else:
            tx_id = uuid.uuid4().hex
            _, pay_url = lolz.create_invoice(
                amount_rub=plan["rub"],
                payment_id=tx_id,
                comment=f"IKK VPN — подписка «{plan['title']}»",
                success_url=f"{SITE_URL}/pay/success",
                callback_url=f"{SITE_URL}/pay/callback",
                additional_data=json.dumps({"web_uid": user["id"], "plan": code}),
            )
    except Exception:
        log.exception("%s: не удалось создать платёж (план %s)", provider, code)
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


def _confirm_web_payment(rec):
    """Зачисляет подтверждённый платёж: продлевает подписку.

    Ключ — Hysteria2, он привязан к токену пользователя и работает,
    пока активна подписка; отдельно выдавать ничего не нужно.
    """
    plan = PLANS.get(rec["plan"], {})
    days = plan.get("days", 30)
    new_until = db.web_add_days(rec["web_user_id"], days)
    db.web_activate_sub(rec["web_user_id"], new_until, "hysteria2")
    db.set_web_payment_status(rec["id"], "CONFIRMED")
    log.info("Оплата подтверждена: %s — web_user %s, план %s (+%s дн.)",
             rec["id"], rec["web_user_id"], rec["plan"], days)


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
        _confirm_web_payment(rec)
    return {"ok": True}


@pay.route("/pay/platega/callback", methods=["POST"])
def platega_callback():
    """Приём статуса от Platega. Подлинность — X-MerchantId/X-Secret."""
    if not platega.is_configured():
        abort(404)
    if not platega.check_callback_auth(request.headers):
        log.warning("Platega callback: неверные X-MerchantId/X-Secret")
        abort(403)

    data = request.get_json(silent=True) or {}
    tx_id = str(data.get("id") or "")
    status = data.get("status")
    rec = db.get_web_payment(tx_id) if tx_id else None
    if not rec:
        # 200 — чтобы Platega не ретраила чужой/тестовый платёж
        log.warning("Platega callback: неизвестная транзакция %s (%s)", tx_id, status)
        return {"ok": True}

    if rec["status"] == "PENDING":
        if status == platega.CONFIRMED:
            _confirm_web_payment(rec)
        elif status in (platega.CANCELED, platega.CHARGEBACKED):
            db.set_web_payment_status(tx_id, status)
            log.info("Platega: платёж %s завершён со статусом %s", tx_id, status)
    return {"ok": True}
