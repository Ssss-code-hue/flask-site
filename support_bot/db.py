"""База тикетов поддержки: SQLite, отдельный файл от базы основного бота."""
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
            CREATE TABLE IF NOT EXISTS tickets (
                id         INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id    INTEGER NOT NULL,
                username   TEXT,
                status     TEXT DEFAULT 'open',   -- open/answered/closed
                created_at INTEGER,
                updated_at INTEGER
            );
            CREATE TABLE IF NOT EXISTS ticket_messages (
                id         INTEGER PRIMARY KEY AUTOINCREMENT,
                ticket_id  INTEGER NOT NULL,
                from_owner INTEGER DEFAULT 0,     -- 1 = ответ поддержки
                text       TEXT,
                created_at INTEGER
            );
            """
        )


def create_ticket(user_id, username, text):
    now = int(time.time())
    with _conn() as c:
        cur = c.execute(
            "INSERT INTO tickets(user_id, username, status, created_at, updated_at) "
            "VALUES(?,?,'open',?,?)",
            (user_id, username, now, now),
        )
        tid = cur.lastrowid
        c.execute(
            "INSERT INTO ticket_messages(ticket_id, from_owner, text, created_at) "
            "VALUES(?,0,?,?)",
            (tid, text, now),
        )
        return tid


def add_message(ticket_id, from_owner, text):
    now = int(time.time())
    with _conn() as c:
        c.execute(
            "INSERT INTO ticket_messages(ticket_id, from_owner, text, created_at) "
            "VALUES(?,?,?,?)",
            (ticket_id, 1 if from_owner else 0, text, now),
        )
        # ответ поддержки переводит тикет в answered, реплика юзера — снова в open
        status = "answered" if from_owner else "open"
        c.execute(
            "UPDATE tickets SET status=?, updated_at=? WHERE id=? AND status != 'closed'",
            (status, now, ticket_id),
        )


def get_ticket(tid):
    with _conn() as c:
        return c.execute("SELECT * FROM tickets WHERE id=?", (tid,)).fetchone()


def open_ticket_of(user_id):
    """Последний незакрытый тикет пользователя (для дописывания сообщений)."""
    with _conn() as c:
        return c.execute(
            "SELECT * FROM tickets WHERE user_id=? AND status != 'closed' "
            "ORDER BY updated_at DESC LIMIT 1",
            (user_id,),
        ).fetchone()


def user_tickets(user_id, limit=6):
    with _conn() as c:
        rows = c.execute(
            "SELECT * FROM tickets WHERE user_id=? ORDER BY updated_at DESC LIMIT ?",
            (user_id, limit),
        ).fetchall()
        return [dict(r) for r in rows]


def open_tickets(limit=15):
    with _conn() as c:
        rows = c.execute(
            "SELECT * FROM tickets WHERE status='open' ORDER BY updated_at LIMIT ?",
            (limit,),
        ).fetchall()
        return [dict(r) for r in rows]


def ticket_messages(tid, limit=4):
    """Последние сообщения тикета (в хронологическом порядке)."""
    with _conn() as c:
        rows = c.execute(
            "SELECT * FROM ticket_messages WHERE ticket_id=? "
            "ORDER BY id DESC LIMIT ?",
            (tid, limit),
        ).fetchall()
        return [dict(r) for r in reversed(rows)]


def set_status(tid, status):
    with _conn() as c:
        c.execute(
            "UPDATE tickets SET status=?, updated_at=? WHERE id=?",
            (status, int(time.time()), tid),
        )
