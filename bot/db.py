"""Простая база данных на SQLite: пользователи, подписки, платежи, рефералы."""
import sqlite3
import time

from .config import DB_PATH


def _conn():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    with _conn() as c:
        c.executescript(
            """
            CREATE TABLE IF NOT EXISTS users (
                user_id     INTEGER PRIMARY KEY,
                username    TEXT,
                sub_until   INTEGER DEFAULT 0,   -- unix-время окончания подписки
                referred_by INTEGER,             -- кто пригласил
                created_at  INTEGER
            );
            CREATE TABLE IF NOT EXISTS payments (
                id         INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id    INTEGER,
                plan       TEXT,
                stars      INTEGER,
                charge_id  TEXT,
                created_at INTEGER
            );
            """
        )


def get_user(uid):
    with _conn() as c:
        return c.execute("SELECT * FROM users WHERE user_id=?", (uid,)).fetchone()


def create_user(uid, username, referred_by=None):
    """Создаёт пользователя, если его ещё нет."""
    with _conn() as c:
        c.execute(
            "INSERT OR IGNORE INTO users(user_id, username, sub_until, referred_by, created_at) "
            "VALUES(?,?,?,?,?)",
            (uid, username, 0, referred_by, int(time.time())),
        )


def update_username(uid, username):
    with _conn() as c:
        c.execute("UPDATE users SET username=? WHERE user_id=?", (username, uid))


def add_days(uid, days):
    """Продлевает подписку на N дней (от текущего конца или от сейчас)."""
    now = int(time.time())
    u = get_user(uid)
    base = max(now, u["sub_until"]) if (u and u["sub_until"]) else now
    new_until = base + days * 86400
    with _conn() as c:
        c.execute("UPDATE users SET sub_until=? WHERE user_id=?", (new_until, uid))
    return new_until


def is_active(uid):
    u = get_user(uid)
    return bool(u and u["sub_until"] and u["sub_until"] > int(time.time()))


def set_referred_by(uid, ref_id):
    """Записывает пригласившего — только если ещё не записан."""
    with _conn() as c:
        c.execute(
            "UPDATE users SET referred_by=? WHERE user_id=? AND referred_by IS NULL",
            (ref_id, uid),
        )


def count_referrals(uid):
    with _conn() as c:
        return c.execute(
            "SELECT COUNT(*) AS n FROM users WHERE referred_by=?", (uid,)
        ).fetchone()["n"]


def record_payment(uid, plan, stars, charge_id):
    with _conn() as c:
        c.execute(
            "INSERT INTO payments(user_id, plan, stars, charge_id, created_at) VALUES(?,?,?,?,?)",
            (uid, plan, stars, charge_id, int(time.time())),
        )


def stats():
    now = int(time.time())
    with _conn() as c:
        users = c.execute("SELECT COUNT(*) AS n FROM users").fetchone()["n"]
        active = c.execute("SELECT COUNT(*) AS n FROM users WHERE sub_until > ?", (now,)).fetchone()["n"]
        payments = c.execute("SELECT COUNT(*) AS n FROM payments").fetchone()["n"]
        stars = c.execute("SELECT COALESCE(SUM(stars), 0) AS s FROM payments").fetchone()["s"]
    return {"users": users, "active_subscriptions": active, "payments": payments, "stars_total": stars}
