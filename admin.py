"""Админ-панель управления пользователями и подписками (owner-only).

Доступ по паролю ADMIN_PASSWORD. Так как доступ к VPN гейтится полем
sub_until (Hysteria2 спрашивает у сайта), управление = правка sub_until:
продлить / отключить подписку у веб- и Telegram-пользователей.
"""
import functools
import hmac
import os
import time

from flask import (Blueprint, flash, redirect, render_template, request,
                   session, url_for)

from bot import db

admin = Blueprint("admin", __name__)
ADMIN_PASSWORD = os.environ.get("ADMIN_PASSWORD", "").strip()


def admin_required(view):
    @functools.wraps(view)
    def wrapped(*args, **kwargs):
        if not session.get("admin"):
            return redirect(url_for("admin.login"))
        return view(*args, **kwargs)
    return wrapped


@admin.route("/admin/login", methods=["GET", "POST"])
def login():
    if session.get("admin"):
        return redirect(url_for("admin.dashboard"))
    if request.method == "POST":
        pw = request.form.get("password", "")
        if ADMIN_PASSWORD and hmac.compare_digest(pw, ADMIN_PASSWORD):
            session["admin"] = True
            session.permanent = True
            return redirect(url_for("admin.dashboard"))
        flash("Неверный пароль." if ADMIN_PASSWORD
              else "Админ-панель не настроена: задайте ADMIN_PASSWORD.")
    return render_template("admin_login.html")


@admin.route("/admin/logout")
def logout():
    session.pop("admin", None)
    return redirect(url_for("admin.login"))


@admin.route("/admin")
@admin_required
def dashboard():
    now = int(time.time())
    web = db.list_web_users()
    bot = db.list_bot_users()

    def active(rows, key):
        return sum(1 for r in rows if r["sub_until"] and r["sub_until"] > now)

    stats = {
        "web_total": len(web), "web_active": active(web, "id"),
        "bot_total": len(bot), "bot_active": active(bot, "user_id"),
    }
    return render_template("admin.html", web=web, bot=bot, now=now, stats=stats)


@admin.route("/admin/action", methods=["POST"])
@admin_required
def action():
    kind = request.form.get("kind")            # web | bot
    op = request.form.get("op")                # add7 | add30 | add365 | revoke
    try:
        uid = int(request.form.get("uid"))
    except (TypeError, ValueError):
        flash("Некорректный пользователь.")
        return redirect(url_for("admin.dashboard"))

    now = int(time.time())
    getter = db.get_web_user if kind == "web" else db.get_user
    setter = db.admin_set_web_until if kind == "web" else db.admin_set_bot_until
    deleter = db.admin_delete_web_user if kind == "web" else db.admin_delete_bot_user
    u = getter(uid)
    if not u:
        flash("Пользователь не найден.")
        return redirect(url_for("admin.dashboard"))

    if op == "delete":
        deleter(uid)
        flash("Пользователь удалён.")
    else:
        days = {"add7": 7, "add30": 30, "add365": 365}.get(op)
        if not days:
            flash("Неизвестное действие.")
            return redirect(url_for("admin.dashboard"))
        base = max(now, u["sub_until"]) if u["sub_until"] else now
        setter(uid, base + days * 86400)
        flash(f"Добавлено {days} дн.")
    return redirect(url_for("admin.dashboard"))
