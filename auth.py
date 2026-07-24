"""Регистрация и вход на сайте по электронной почте.

Схема: email + пароль, при регистрации на почту приходит 6-значный код.
Пока код не введён — аккаунт считается неподтверждённым.
"""
import hashlib
import hmac
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
from bot.config import BOT_TOKEN, BOT_USERNAME
from bot.panel import WEB_PREFIX, get_subscription_url, site_sub_url, sub_token

auth = Blueprint("auth", __name__)

CODE_TTL = 15 * 60        # код действует 15 минут
CODE_RESEND_COOLDOWN = 60  # повторная отправка не чаще раза в минуту
CODE_MAX_ATTEMPTS = 5      # попыток ввода кода
TRIAL_DAYS = int(os.environ.get("WEB_TRIAL_DAYS", "15"))  # пробный VPN-ключ с сайта
SITE_URL = os.environ.get("SITE_URL", "https://ikkvpn.com").rstrip("/")
EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
TG_AUTH_TTL = 24 * 3600   # данные Telegram-виджета годны сутки


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

    return render_template("register.html", captcha_question=_new_captcha(),
                           **_tg_widget_ctx())


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

    return render_template("login.html", **_tg_widget_ctx())


def _tg_widget_ctx():
    """Переменные для кнопки Telegram Login Widget в шаблонах.

    Виджет показываем, только если задан токен бота. data-auth-url должен
    быть абсолютным https-адресом того же домена, что привязан к боту в
    @BotFather (/setdomain), иначе Telegram откажется отдавать данные.
    """
    if not (BOT_TOKEN and BOT_USERNAME):
        return {}
    return {"tg_bot": BOT_USERNAME, "tg_auth_url": f"{SITE_URL}/auth/telegram"}


def _verify_telegram_auth(data):
    """Проверяет подпись данных Telegram Login Widget.

    Алгоритм из документации Telegram: секрет = SHA256(bot_token), затем
    HMAC-SHA256 по строке "key=value\\n..." (поля кроме hash, отсортированы).
    Возвращает True, только если подпись верна и данные не старше суток —
    иначе кто угодно мог бы войти под любым Telegram ID.
    """
    if not BOT_TOKEN:
        return False
    received = data.get("hash", "")
    try:
        auth_date = int(data.get("auth_date", 0))
    except (TypeError, ValueError):
        return False
    if not received or abs(time.time() - auth_date) > TG_AUTH_TTL:
        return False
    check = "\n".join(f"{k}={data[k]}" for k in sorted(data) if k != "hash")
    secret = hashlib.sha256(BOT_TOKEN.encode()).digest()
    calc = hmac.new(secret, check.encode(), hashlib.sha256).hexdigest()
    return hmac.compare_digest(calc, received)


@auth.route("/auth/telegram")
def telegram_login():
    """Вход через Telegram Login Widget.

    Виджет редиректит сюда с параметрами id/first_name/username/auth_date/hash.
    Проверяем подпись, находим (или создаём) веб-аккаунт, связанный с этим
    Telegram-пользователем, и логиним. Подписка у него — общая с ботом.
    """
    data = {k: v for k, v in request.args.items()}
    if not _verify_telegram_auth(data):
        flash("Не удалось подтвердить вход через Telegram. Попробуйте ещё раз.")
        return redirect(url_for("auth.login"))

    tg_id = int(data["id"])
    # Заводим запись в users, если человека ещё нет в базе бота — тогда его
    # подписка (пока пустая) будет жить там же, где у бот-пользователей.
    if not db.get_user(tg_id):
        db.create_user(tg_id, data.get("username") or data.get("first_name"))
    web_id = db.get_or_create_web_user_by_tg(tg_id)
    session["uid"] = web_id
    return redirect(url_for("auth.account"))


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

    # Для аккаунта, связанного с Telegram, источник правды — бот-идентичность:
    # подписка из users.sub_until и один пользователь Marzban (ikk_<tg_id>).
    # Так у человека единый ключ и срок, а не отдельная веб-подписка.
    now = int(time.time())
    linked = user["telegram_id"]
    if linked:
        bot_user = db.get_user(linked)
        sub_until = bot_user["sub_until"] if bot_user else 0
        panel_id, panel_prefix = linked, None      # ikk_<tg_id>
        stored_sub = None
    else:
        sub_until = user["sub_until"]
        panel_id, panel_prefix = user["id"], WEB_PREFIX  # web_<id>
        stored_sub = user["sub_url"]

    vpn_active = bool(sub_until and sub_until > now)
    vpn_until = (time.strftime("%d.%m.%Y", time.localtime(sub_until))
                 if sub_until else None)

    # Синхронизируем с панелью при каждом заходе. get_subscription_url
    # идемпотентна: нет пользователя — создаёт, есть — продлевает до
    # текущего срока. Так чинятся и пустой sub_url (наследие Hysteria2),
    # и ссылки на пользователя, которого в панели уже нет (удалён/создан
    # под другим префиксом) — их симптом «Подписка не найдена (404)».
    token = None
    if vpn_active:
        fresh = get_subscription_url(panel_id, sub_until, prefix=panel_prefix)
        if fresh:
            if not linked and fresh != stored_sub:
                db.web_set_sub_url(user["id"], fresh)
            token = sub_token(fresh)
        elif stored_sub:
            # панель недоступна — отдаём то, что было, лучше чем ничего
            token = sub_token(stored_sub)

    vpn_key = f"{SITE_URL}/sub/{token}" if token else None
    app_url = f"/v2raytun/{token}" if token else None

    # У Telegram-аккаунта email синтетический, а пароля нет — показываем
    # имя из бота и прячем смену пароля.
    if linked:
        display_name = (bot_user["username"] if bot_user and bot_user["username"]
                        else f"Telegram {linked}")
        trial_used = bool(bot_user["trial_used"]) if bot_user else False
    else:
        display_name = user["email"]
        trial_used = bool(user["trial_used"])

    return render_template(
        "account.html", user=user, created=created,
        vpn_active=vpn_active, vpn_until=vpn_until,
        vpn_key=vpn_key, app_url=app_url, trial_used=trial_used,
        trial_days=TRIAL_DAYS, is_telegram=bool(linked), display_name=display_name,
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
    # Для связанного с Telegram аккаунта триал общий с ботом: и признак
    # использования, и подписка живут в бот-идентичности — иначе человек
    # получил бы пробный период дважды (на сайте и в боте).
    linked = user["telegram_id"]
    bot_user = db.get_user(linked) if linked else None
    trial_used = bot_user["trial_used"] if linked else user["trial_used"]
    sub_until = (bot_user["sub_until"] if bot_user else 0) if linked else user["sub_until"]

    if trial_used:
        flash("Пробный ключ уже был использован на этом аккаунте.")
    elif sub_until and sub_until > now:
        flash("Подписка уже активна — ключ ниже.")
    elif not request.form.get("accept_offer"):
        flash("Чтобы активировать пробный период, отметьте согласие с офертой.")
    else:
        new_until = now + TRIAL_DAYS * 86400
        # Пробный период отмечаем использованным ТОЛЬКО если панель реально
        # выдала подписку — иначе временный сбой панели сжёг бы попытку.
        if linked:
            sub_url = get_subscription_url(linked, new_until)   # ikk_<tg_id>
            if sub_url:
                db.add_days(linked, TRIAL_DAYS)   # пишет users.sub_until от now
                db.mark_trial_used(linked)
        else:
            sub_url = get_subscription_url(user["id"], new_until, prefix=WEB_PREFIX)
            if sub_url:
                db.web_activate_sub(user["id"], new_until, sub_url, trial=True)
        if sub_url:
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
