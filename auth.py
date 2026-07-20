"""Регистрация и вход на сайте по электронной почте.

Схема: email + пароль, при регистрации на почту приходит 6-значный код.
Пока код не введён — аккаунт считается неподтверждённым.
"""
import hashlib
import os
import random
import re
import secrets
import time
from functools import wraps

from flask import (Blueprint, flash, redirect, render_template, request,
                   session, url_for)
from werkzeug.security import check_password_hash, generate_password_hash

import mailer
from bot import db
from bot.panel import WEB_PREFIX, get_subscription_url, site_sub_url, sub_token

auth = Blueprint("auth", __name__)

CODE_TTL = 15 * 60        # код действует 15 минут
CODE_RESEND_COOLDOWN = 60  # повторная отправка не чаще раза в минуту
CODE_MAX_ATTEMPTS = 5      # попыток ввода кода
TRIAL_DAYS = int(os.environ.get("WEB_TRIAL_DAYS", "5"))  # пробный VPN-ключ с сайта
SITE_URL = os.environ.get("SITE_URL", "https://ikkvpn.com").rstrip("/")
EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def _hash_code(code):
    return hashlib.sha256(code.encode()).hexdigest()


def _send_new_code(email):
    """Генерирует 6-значный код, сохраняет и отправляет на почту."""
    code = f"{secrets.randbelow(1000000):06d}"
    db.save_email_code(email, _hash_code(code), CODE_TTL)
    return mailer.send_code(email, code)


def _new_captcha():
    """Простая капча от ботов: вопрос-пример, ответ храним в сессии.

    Внешние сервисы (reCAPTCHA и т.п.) не используем — им нужны ключи,
    и в РФ они часто недоступны. Для мелкого сайта примера достаточно.
    """
    a, b = random.randint(2, 9), random.randint(2, 9)
    session["captcha_answer"] = a + b
    return f"Сколько будет {a} + {b}?"


def login_required(view):
    """Декоратор: пускает на страницу только вошедших пользователей."""
    @wraps(view)
    def wrapped(*args, **kwargs):
        if not session.get("uid"):
            return redirect(url_for("auth.login"))
        return view(*args, **kwargs)
    return wrapped


@auth.route("/register", methods=["GET", "POST"])
def register():
    if session.get("uid"):
        return redirect(url_for("auth.account"))

    if request.method == "POST":
        email = (request.form.get("email") or "").strip().lower()
        password = request.form.get("password") or ""
        password2 = request.form.get("password2") or ""
        captcha = (request.form.get("captcha") or "").strip()
        honeypot = request.form.get("website") or ""  # скрытое поле-ловушка

        expected = session.pop("captcha_answer", None)
        if honeypot:
            # Поле видят только боты — молча возвращаем на форму.
            return redirect(url_for("auth.register"))
        if not request.form.get("accept_terms") or not request.form.get("accept_privacy"):
            flash("Подтвердите согласие с пользовательским соглашением "
                  "и политикой конфиденциальности.")
        elif expected is None or captcha != str(expected):
            flash("Неверный ответ на пример. Попробуйте ещё раз.")
        elif not EMAIL_RE.match(email):
            flash("Введите корректный адрес почты.")
        elif len(password) < 8:
            flash("Пароль должен быть не короче 8 символов.")
        elif password != password2:
            flash("Пароли не совпадают.")
        else:
            existing = db.get_web_user_by_email(email)
            if existing and existing["verified"]:
                flash("Эта почта уже зарегистрирована — попробуйте войти.")
                return redirect(url_for("auth.login"))
            if existing:
                # Регистрацию начинали, но почту не подтвердили — обновляем пароль.
                db.set_web_user_password(email, generate_password_hash(password))
            else:
                db.create_web_user(email, generate_password_hash(password))

            if _send_new_code(email):
                session["pending_email"] = email
                flash("Код отправлен на почту. Проверьте входящие (и папку «Спам»).")
                return redirect(url_for("auth.verify"))
            flash("Не получилось отправить письмо. Попробуйте ещё раз чуть позже.")

    return render_template("register.html", captcha_question=_new_captcha())


@auth.route("/verify", methods=["GET", "POST"])
def verify():
    email = session.get("pending_email")
    if not email:
        return redirect(url_for("auth.register"))

    if request.method == "POST":
        code = (request.form.get("code") or "").strip()
        rec = db.get_email_code(email)

        if not rec or rec["expires_at"] < int(time.time()):
            flash("Код устарел. Отправьте новый.")
        elif rec["attempts"] >= CODE_MAX_ATTEMPTS:
            flash("Слишком много попыток. Отправьте новый код.")
        elif _hash_code(code) != rec["code_hash"]:
            db.bump_code_attempts(email)
            flash("Неверный код. Проверьте письмо и попробуйте ещё раз.")
        else:
            # Код верный: подтверждаем почту и сразу входим.
            db.mark_web_user_verified(email)
            db.delete_email_code(email)
            user = db.get_web_user_by_email(email)
            session.pop("pending_email", None)
            session.permanent = True
            session["uid"] = user["id"]
            flash("Почта подтверждена — добро пожаловать!")
            return redirect(url_for("auth.account"))

    return render_template("verify.html", email=email)


@auth.route("/verify/resend")
def resend_code():
    email = session.get("pending_email")
    if not email:
        return redirect(url_for("auth.register"))

    rec = db.get_email_code(email)
    if rec and int(time.time()) - rec["sent_at"] < CODE_RESEND_COOLDOWN:
        flash("Новый код можно запросить раз в минуту. Подождите немного.")
    elif _send_new_code(email):
        flash("Новый код отправлен на почту.")
    else:
        flash("Не получилось отправить письмо. Попробуйте ещё раз чуть позже.")
    return redirect(url_for("auth.verify"))


@auth.route("/login", methods=["GET", "POST"])
def login():
    if session.get("uid"):
        return redirect(url_for("auth.account"))

    if request.method == "POST":
        email = (request.form.get("email") or "").strip().lower()
        password = request.form.get("password") or ""
        user = db.get_web_user_by_email(email)

        if not user or not check_password_hash(user["password_hash"], password):
            flash("Неверная почта или пароль.")
        elif not user["verified"]:
            # Почта так и не подтверждена — отправляем код заново.
            session["pending_email"] = email
            _send_new_code(email)
            flash("Почта ещё не подтверждена. Мы отправили новый код.")
            return redirect(url_for("auth.verify"))
        else:
            session.permanent = True   # держим вход, а не «до закрытия вкладки»
            session["uid"] = user["id"]
            return redirect(url_for("auth.account"))

    return render_template("login.html")


@auth.route("/logout")
def logout():
    session.pop("uid", None)
    return redirect(url_for("home"))


@auth.route("/account")
@login_required
def account():
    user = db.get_web_user(session["uid"])
    if not user:
        session.pop("uid", None)
        return redirect(url_for("auth.login"))
    created = time.strftime("%d.%m.%Y", time.localtime(user["created_at"]))

    # самозачисление: если оплата прошла, но callback не дошёл (режет
    # Cloudflare) — сверяем незачисленные платежи у кассы прямо сейчас
    try:
        import pay
        pay.reconcile_pending(user["id"])
        user = db.get_web_user(session["uid"])   # перечитать после возможного зачисления
    except Exception:
        pass

    # VPN-ключ — подписка Marzban, выданная при оплате/пробном периоде.
    # Клиенту отдаём ссылку на НАШ домен: сайт проксирует её по 443 и
    # попутно правит XHTTP-параметры (sub.py), без которых ТСПУ рвёт связь.
    now = int(time.time())
    vpn_active = bool(user["sub_until"] and user["sub_until"] > now)
    vpn_until = (time.strftime("%d.%m.%Y", time.localtime(user["sub_until"]))
                 if user["sub_until"] else None)
    vpn_key = site_sub_url(user["sub_url"], SITE_URL) if vpn_active else None
    token = sub_token(user["sub_url"]) if vpn_active else None
    app_url = f"/v2raytun/{token}" if token else None
    return render_template(
        "account.html", user=user, created=created,
        vpn_active=vpn_active, vpn_until=vpn_until,
        vpn_key=vpn_key, app_url=app_url, trial_used=bool(user["trial_used"]),
        trial_days=TRIAL_DAYS,
    )


@auth.route("/account/vpn-trial", methods=["POST"])
@login_required
def vpn_trial():
    """Выдаёт пробный VPN-ключ: создаёт пользователя в панели Marzban."""
    user = db.get_web_user(session["uid"])
    if not user:
        session.pop("uid", None)
        return redirect(url_for("auth.login"))

    now = int(time.time())
    if user["trial_used"]:
        flash("Пробный ключ уже был использован на этом аккаунте.")
    elif user["sub_until"] and user["sub_until"] > now:
        flash("Подписка уже активна — ключ ниже.")
    elif not request.form.get("accept_offer"):
        flash("Чтобы активировать пробный период, отметьте согласие с офертой.")
    else:
        new_until = now + TRIAL_DAYS * 86400
        # Пробный период отмечаем использованным ТОЛЬКО если панель реально
        # выдала подписку — иначе временный сбой панели сжёг бы попытку.
        sub_url = get_subscription_url(user["id"], new_until, prefix=WEB_PREFIX)
        if sub_url:
            db.web_activate_sub(user["id"], new_until, sub_url, trial=True)
            flash(f"Готово! Пробный ключ на {TRIAL_DAYS} дн. — ниже, в разделе «VPN-ключ».")
        else:
            flash("Не удалось выдать ключ — попробуйте через минуту "
                  "или напишите в поддержку.")
    return redirect(url_for("auth.account"))


@auth.route("/account/password", methods=["POST"])
@login_required
def change_password():
    user = db.get_web_user(session["uid"])
    if not user:
        session.pop("uid", None)
        return redirect(url_for("auth.login"))

    current = request.form.get("current_password") or ""
    new = request.form.get("new_password") or ""
    new2 = request.form.get("new_password2") or ""

    if not check_password_hash(user["password_hash"], current):
        flash("Текущий пароль указан неверно.")
    elif len(new) < 8:
        flash("Новый пароль должен быть не короче 8 символов.")
    elif new != new2:
        flash("Новые пароли не совпадают.")
    else:
        db.set_web_user_password(user["email"], generate_password_hash(new))
        flash("Пароль обновлён.")
    return redirect(url_for("auth.account"))
