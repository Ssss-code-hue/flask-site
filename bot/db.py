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
            CREATE TABLE IF NOT EXISTS web_users (
                id            INTEGER PRIMARY KEY AUTOINCREMENT,
                email         TEXT UNIQUE NOT NULL,
                password_hash TEXT NOT NULL,
                verified      INTEGER DEFAULT 0,   -- 1 = почта подтверждена кодом
                created_at    INTEGER
            );
            CREATE TABLE IF NOT EXISTS web_payments (
                id          TEXT PRIMARY KEY,      -- transactionId от Platega
                web_user_id INTEGER,
                plan        TEXT,
                amount_rub  INTEGER,
                status      TEXT DEFAULT 'PENDING',-- PENDING/CONFIRMED/CANCELED/CHARGEBACKED
                created_at  INTEGER
            );
            CREATE TABLE IF NOT EXISTS bot_invoices (
                payment_id TEXT PRIMARY KEY,      -- наш uuid (по нему ищем счёт в Lolz)
                invoice_id TEXT,                  -- id счёта на стороне Lolz
                user_id    INTEGER,               -- Telegram-пользователь
                plan       TEXT,
                amount_rub INTEGER,
                status     TEXT DEFAULT 'pending',-- pending/paid/expired
                created_at INTEGER
            );
            CREATE TABLE IF NOT EXISTS email_codes (
                email      TEXT PRIMARY KEY,
                code_hash  TEXT,                   -- sha256 от кода (сам код не храним)
                expires_at INTEGER,                -- unix-время, до которого код действует
                attempts   INTEGER DEFAULT 0,      -- сколько раз вводили неверный код
                sent_at    INTEGER                 -- когда отправлен (для повторной отправки)
            );
            """
        )
        # Миграция: провайдер счёта (lolz/platega) у счетов бота
        icols = {r["name"] for r in c.execute("PRAGMA table_info(bot_invoices)")}
        if "provider" not in icols:
            c.execute("ALTER TABLE bot_invoices ADD COLUMN provider TEXT DEFAULT 'lolz'")
        # Миграция старых баз: пробный период у Telegram-пользователей
        ucols = {r["name"] for r in c.execute("PRAGMA table_info(users)")}
        if "trial_used" not in ucols:
            c.execute("ALTER TABLE users ADD COLUMN trial_used INTEGER DEFAULT 0")
        # Наследие Hysteria2: колонка больше не используется, но остаётся —
        # SQLite не умеет удалять колонки без пересоздания таблицы.
        if "hy_token" not in ucols:
            c.execute("ALTER TABLE users ADD COLUMN hy_token TEXT")
        wcols0 = {r["name"] for r in c.execute("PRAGMA table_info(web_users)")}
        if "hy_token" not in wcols0:
            c.execute("ALTER TABLE web_users ADD COLUMN hy_token TEXT")
        # Провайдер веб-платежа (platega/lolz) — нужен, чтобы сайт мог сам
        # сверить незачисленную оплату у нужной кассы (если callback не дошёл)
        pcols = {r["name"] for r in c.execute("PRAGMA table_info(web_payments)")}
        if "provider" not in pcols:
            c.execute("ALTER TABLE web_payments ADD COLUMN provider TEXT DEFAULT 'platega'")
        # Миграция старых баз: колонки VPN-подписки у пользователей сайта
        cols = {r["name"] for r in c.execute("PRAGMA table_info(web_users)")}
        if "sub_until" not in cols:
            c.execute("ALTER TABLE web_users ADD COLUMN sub_until INTEGER DEFAULT 0")
        if "trial_used" not in cols:
            c.execute("ALTER TABLE web_users ADD COLUMN trial_used INTEGER DEFAULT 0")
        if "sub_url" not in cols:
            c.execute("ALTER TABLE web_users ADD COLUMN sub_url TEXT")


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


def mark_trial_used(uid):
    with _conn() as c:
        c.execute("UPDATE users SET trial_used=1 WHERE user_id=?", (uid,))


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


def get_payments(uid):
    """История платежей пользователя (свежие сверху)."""
    with _conn() as c:
        rows = c.execute(
            "SELECT plan, stars, created_at FROM payments "
            "WHERE user_id=? ORDER BY created_at DESC",
            (uid,),
        ).fetchall()
        return [dict(r) for r in rows]


def web_stats():
    """Статистика аккаунтов сайта (без почтовых адресов — приватность)."""
    with _conn() as c:
        total = c.execute("SELECT COUNT(*) AS n FROM web_users").fetchone()["n"]
        verified = c.execute(
            "SELECT COUNT(*) AS n FROM web_users WHERE verified=1"
        ).fetchone()["n"]
    return {"web_users": total, "verified": verified}


def record_payment(uid, plan, stars, charge_id):
    with _conn() as c:
        c.execute(
            "INSERT INTO payments(user_id, plan, stars, charge_id, created_at) VALUES(?,?,?,?,?)",
            (uid, plan, stars, charge_id, int(time.time())),
        )


# ===== Админ-панель: список и управление подписками =====

def list_web_users(limit=1000):
    with _conn() as c:
        rows = c.execute(
            "SELECT id, email, sub_until, trial_used, created_at FROM web_users "
            "ORDER BY created_at DESC LIMIT ?", (limit,)).fetchall()
        return [dict(r) for r in rows]


def list_bot_users(limit=1000):
    with _conn() as c:
        rows = c.execute(
            "SELECT user_id, username, sub_until, trial_used, created_at FROM users "
            "ORDER BY created_at DESC LIMIT ?", (limit,)).fetchall()
        return [dict(r) for r in rows]


def admin_set_web_until(web_id, ts):
    with _conn() as c:
        c.execute("UPDATE web_users SET sub_until=? WHERE id=?", (ts, web_id))


def admin_set_bot_until(uid, ts):
    with _conn() as c:
        c.execute("UPDATE users SET sub_until=? WHERE user_id=?", (ts, uid))


def admin_delete_web_user(web_id):
    with _conn() as c:
        c.execute("DELETE FROM web_users WHERE id=?", (web_id,))


def admin_delete_bot_user(uid):
    with _conn() as c:
        c.execute("DELETE FROM users WHERE user_id=?", (uid,))


# ===== Счета Lolz в боте (оплата картой/СБП) =====

def create_bot_invoice(payment_id, invoice_id, user_id, plan, amount_rub, provider="lolz"):
    with _conn() as c:
        c.execute(
            "INSERT OR IGNORE INTO bot_invoices"
            "(payment_id, invoice_id, user_id, plan, amount_rub, status, created_at, provider) "
            "VALUES(?,?,?,?,?,'pending',?,?)",
            (payment_id, invoice_id, user_id, plan, amount_rub, int(time.time()), provider),
        )


def get_bot_invoice(payment_id):
    with _conn() as c:
        return c.execute(
            "SELECT * FROM bot_invoices WHERE payment_id=?", (payment_id,)
        ).fetchone()


def pending_bot_invoices():
    """Неоплаченные счета — их фоново опрашивает бот."""
    with _conn() as c:
        rows = c.execute(
            "SELECT * FROM bot_invoices WHERE status='pending' ORDER BY created_at"
        ).fetchall()
        return [dict(r) for r in rows]


def settle_bot_invoice(payment_id, status):
    """Переводит счёт из pending в конечный статус.

    Возвращает True, только если статус сменили именно сейчас — это
    защита от двойного зачисления (кнопка «Я оплатил» + фоновый опрос).
    """
    with _conn() as c:
        cur = c.execute(
            "UPDATE bot_invoices SET status=? WHERE payment_id=? AND status='pending'",
            (status, payment_id),
        )
        return cur.rowcount > 0


# ===== Пользователи сайта (регистрация по почте) =====

def get_web_user_by_email(email):
    with _conn() as c:
        return c.execute("SELECT * FROM web_users WHERE email=?", (email,)).fetchone()


def get_web_user(uid):
    with _conn() as c:
        return c.execute("SELECT * FROM web_users WHERE id=?", (uid,)).fetchone()


def create_web_user(email, password_hash):
    """Создаёт неподтверждённого пользователя сайта."""
    with _conn() as c:
        c.execute(
            "INSERT INTO web_users(email, password_hash, verified, created_at) VALUES(?,?,0,?)",
            (email, password_hash, int(time.time())),
        )


def set_web_user_password(email, password_hash):
    with _conn() as c:
        c.execute("UPDATE web_users SET password_hash=? WHERE email=?", (password_hash, email))


def mark_web_user_verified(email):
    with _conn() as c:
        c.execute("UPDATE web_users SET verified=1 WHERE email=?", (email,))


def web_add_days(uid, days):
    """Продлевает подписку пользователя сайта на N дней (от текущего конца или от сейчас)."""
    now = int(time.time())
    u = get_web_user(uid)
    base = max(now, u["sub_until"]) if (u and u["sub_until"]) else now
    new_until = base + days * 86400
    with _conn() as c:
        c.execute("UPDATE web_users SET sub_until=? WHERE id=?", (new_until, uid))
    return new_until


def create_web_payment(tx_id, web_user_id, plan, amount_rub, provider="platega"):
    """Запоминает созданный платёж (до подтверждения — PENDING)."""
    with _conn() as c:
        c.execute(
            "INSERT OR IGNORE INTO web_payments"
            "(id, web_user_id, plan, amount_rub, status, created_at, provider) "
            "VALUES(?,?,?,?, 'PENDING', ?, ?)",
            (tx_id, web_user_id, plan, amount_rub, int(time.time()), provider),
        )


def pending_web_payments(web_user_id, max_age=3 * 3600):
    """Незачисленные (PENDING) платежи пользователя за последние часы —
    их сайт сам сверяет у кассы, если callback не дошёл."""
    now = int(time.time())
    with _conn() as c:
        rows = c.execute(
            "SELECT * FROM web_payments WHERE web_user_id=? AND status='PENDING' "
            "AND created_at > ?", (web_user_id, now - max_age)).fetchall()
        return [dict(r) for r in rows]


def get_web_payment(tx_id):
    with _conn() as c:
        return c.execute("SELECT * FROM web_payments WHERE id=?", (tx_id,)).fetchone()


def set_web_payment_status(tx_id, status):
    with _conn() as c:
        c.execute("UPDATE web_payments SET status=? WHERE id=?", (status, tx_id))


def web_activate_sub(uid, sub_until, sub_url, trial=False):
    """Сохраняет подписку пользователя сайта после успешной выдачи ключа панелью.

    Вызывается ТОЛЬКО когда панель уже вернула ссылку — чтобы пробный период
    не «сгорал» из-за временной ошибки панели.
    """
    with _conn() as c:
        if trial:
            c.execute(
                "UPDATE web_users SET sub_until=?, sub_url=?, trial_used=1 WHERE id=?",
                (sub_until, sub_url, uid),
            )
        else:
            c.execute(
                "UPDATE web_users SET sub_until=?, sub_url=? WHERE id=?",
                (sub_until, sub_url, uid),
            )


# ===== Коды подтверждения почты =====

def save_email_code(email, code_hash, ttl_seconds):
    """Сохраняет код для почты (старый код затирается)."""
    now = int(time.time())
    with _conn() as c:
        c.execute(
            "INSERT OR REPLACE INTO email_codes(email, code_hash, expires_at, attempts, sent_at) "
            "VALUES(?,?,?,0,?)",
            (email, code_hash, now + ttl_seconds, now),
        )


def get_email_code(email):
    with _conn() as c:
        return c.execute("SELECT * FROM email_codes WHERE email=?", (email,)).fetchone()


def bump_code_attempts(email):
    with _conn() as c:
        c.execute("UPDATE email_codes SET attempts = attempts + 1 WHERE email=?", (email,))


def delete_email_code(email):
    with _conn() as c:
        c.execute("DELETE FROM email_codes WHERE email=?", (email,))


def stats():
    now = int(time.time())
    with _conn() as c:
        users = c.execute("SELECT COUNT(*) AS n FROM users").fetchone()["n"]
        active = c.execute("SELECT COUNT(*) AS n FROM users WHERE sub_until > ?", (now,)).fetchone()["n"]
        payments = c.execute("SELECT COUNT(*) AS n FROM payments").fetchone()["n"]
        stars = c.execute("SELECT COALESCE(SUM(stars), 0) AS s FROM payments").fetchone()["s"]
    return {"users": users, "active_subscriptions": active, "payments": payments, "stars_total": stars}
