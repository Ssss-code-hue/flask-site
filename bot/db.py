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
            CREATE TABLE IF NOT EXISTS promo_codes (
                code       TEXT PRIMARY KEY,        -- код в ВЕРХНЕМ регистре
                bonus_days INTEGER NOT NULL,        -- сколько дней даёт
                max_uses   INTEGER DEFAULT 0,       -- лимит активаций (0 = без лимита)
                used_count INTEGER DEFAULT 0,       -- сколько раз уже активировали
                active     INTEGER DEFAULT 1,       -- 1 = действует
                created_at INTEGER
            );
            CREATE TABLE IF NOT EXISTS promo_uses (
                code    TEXT,
                user_id INTEGER,
                used_at INTEGER,
                PRIMARY KEY (code, user_id)         -- один код — один раз на человека
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
        # Флаг: слали ли напоминание «триал заканчивается» (чтобы не спамить).
        # Сбрасывается при новой активации триала/подписки.
        if "trial_reminded" not in ucols:
            c.execute("ALTER TABLE users ADD COLUMN trial_reminded INTEGER DEFAULT 0")
        # Заблокировал ли пользователь бота. Telegram сообщает об этом только
        # при попытке отправки, поэтому отмечаем во время рассылок и дальше
        # таких не трогаем — иначе каждая рассылка снова стучится в мёртвые
        # чаты и портит статистику. Сбрасывается, когда человек вернётся.
        if "blocked" not in ucols:
            c.execute("ALTER TABLE users ADD COLUMN blocked INTEGER DEFAULT 0")
        # Выдана ли награда пригласившему ЗА ЭТОГО пользователя. Бонус даётся
        # за первую оплату приглашённого, а платить он может много раз —
        # без отметки пригласивший получал бы дни за каждое продление.
        if "ref_rewarded" not in ucols:
            c.execute("ALTER TABLE users ADD COLUMN ref_rewarded INTEGER DEFAULT 0")
        # Напомнили ли, что заканчивается ТЕКУЩАЯ подписка. Сбрасывается в
        # add_days: продлился — значит про новый срок надо напомнить заново.
        if "expiry_reminded" not in ucols:
            c.execute("ALTER TABLE users ADD COLUMN expiry_reminded INTEGER DEFAULT 0")
        # Просили ли порекомендовать сервис друзьям (одна просьба на человека).
        if "ref_asked" not in ucols:
            c.execute("ALTER TABLE users ADD COLUMN ref_asked INTEGER DEFAULT 0")
        # Откуда пришёл человек: метка из ссылки t.me/бот?start=<метка>.
        # Без неё каналы продвижения сравнивать нечем — видно только общее
        # число пользователей, а какой ролик его дал, приходится угадывать.
        if "source" not in ucols:
            c.execute("ALTER TABLE users ADD COLUMN source TEXT")
        # Воронка подключения. Между «выдали ключ» и «человек в сети» у нас не
        # было ни одного замера: кнопка «Подключиться сейчас» — обычная
        # URL-кнопка, о нажатии Telegram боту не сообщает. Поэтому на вопрос
        # «почему половина не подключилась» ответить было нечем.
        # Все три шага ниже происходят на нашем же сервере, надо лишь их
        # записать. Токен подписки храним, чтобы сопоставить запрос с человеком:
        # обратно из токена в user_id иначе не перейти.
        if "sub_token" not in ucols:
            c.execute("ALTER TABLE users ADD COLUMN sub_token TEXT")
        if "page_at" not in ucols:      # открыл страницу подключения (/app)
            c.execute("ALTER TABLE users ADD COLUMN page_at INTEGER")
        if "import_at" not in ucols:    # нажал «Добавить подписку» (/v2raytun)
            c.execute("ALTER TABLE users ADD COLUMN import_at INTEGER")
        if "fetch_at" not in ucols:     # приложение забрало подписку (/sub)
            c.execute("ALTER TABLE users ADD COLUMN fetch_at INTEGER")
        if "fetch_ua" not in ucols:     # чем забрало — видно платформу
            c.execute("ALTER TABLE users ADD COLUMN fetch_ua TEXT")
        # Когда человеку впервые показали ключ. Нужно, чтобы напомнить о
        # подключении спустя несколько часов, а не сразу и не всем подряд.
        if "key_at" not in ucols:
            c.execute("ALTER TABLE users ADD COLUMN key_at INTEGER")
        # Подтолкнули ли подключиться (одно напоминание на человека —
        # второе читается как навязчивость и ведёт в блок)
        if "connect_reminded" not in ucols:
            c.execute("ALTER TABLE users ADD COLUMN connect_reminded "
                      "INTEGER DEFAULT 0")
        # Когда человек подтвердил участие в розыгрыше. Время, а не флаг:
        # по нему строится порядок участников, а он должен быть неизменным —
        # список публикуется до розыгрыша, и номера обязаны совпасть.
        if "giveaway_at" not in ucols:
            c.execute("ALTER TABLE users ADD COLUMN giveaway_at INTEGER")
        # Поиск идёт по токену на каждый запрос подписки — без индекса это
        # полный обход таблицы на каждое открытие приложения
        c.execute("CREATE INDEX IF NOT EXISTS idx_users_sub_token "
                  "ON users(sub_token)")
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
        # Связка аккаунта сайта с Telegram-ботом: у связанного веб-аккаунта
        # тут лежит user_id из таблицы users. Тогда подписка и ключ берутся
        # из бот-идентичности (один пользователь Marzban на человека), а не
        # заводится отдельная веб-подписка. NULL = обычный email-аккаунт.
        if "telegram_id" not in cols:
            c.execute("ALTER TABLE web_users ADD COLUMN telegram_id INTEGER")
            c.execute("CREATE INDEX IF NOT EXISTS idx_web_users_tg "
                      "ON web_users(telegram_id)")


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


def set_source(uid, source):
    """Запоминает, откуда пришёл человек. Только если метки ещё нет.

    Первое касание важнее последнего: если человек пришёл с YouTube, а потом
    открыл бота по ссылке из канала, засчитать надо YouTube — иначе все
    источники со временем перепишутся на самый частый.
    """
    with _conn() as c:
        c.execute("UPDATE users SET source=? WHERE user_id=? "
                  "AND (source IS NULL OR source='')", (source, uid))


def remember_sub_token(uid, token):
    """Запоминает токен подписки — по нему запрос с сайта находит человека.

    Заодно отмечает момент первой выдачи ключа: отсюда считается пауза
    перед напоминанием «ключ есть, а VPN не включён».
    """
    if not token:
        return
    with _conn() as c:
        c.execute("UPDATE users SET sub_token=?, "
                  "key_at=COALESCE(key_at, ?) WHERE user_id=?",
                  (token, int(time.time()), uid))


def connect_reminder_candidates(now, after_seconds):
    """Кому напомнить, что ключ есть, а VPN так и не включён.

    Только те, кому ключ уже показывали (key_at) и с тех пор прошло
    достаточно времени: напоминание через минуту после выдачи выглядит
    так, будто за человеком следят.

    Тех, кто получил ключ до появления замеров, здесь нет намеренно —
    иначе при первом же запуске им всем разом улетела бы рассылка.
    Для них есть ручная команда /broadcast_nc.
    """
    with _conn() as c:
        rows = c.execute(
            "SELECT user_id FROM users "
            "WHERE COALESCE(blocked,0)=0 AND COALESCE(connect_reminded,0)=0 "
            "AND sub_until>? AND key_at IS NOT NULL AND key_at<?",
            (now, now - after_seconds),
        ).fetchall()
        return [r["user_id"] for r in rows]


def mark_connect_reminded(uid):
    with _conn() as c:
        c.execute("UPDATE users SET connect_reminded=1 WHERE user_id=?", (uid,))


# Шаг воронки → колонка с временем ПЕРВОГО прохождения. Первое, а не
# последнее: интересно, дошёл ли человек вообще, а не сколько раз повторил.
_FUNNEL_STEPS = {"page": "page_at", "import": "import_at", "fetch": "fetch_at"}


def touch_funnel(token, step, ua=None):
    """Отмечает шаг воронки по токену подписки. Чужой токен молча игнорируем
    (у пользователей сайта своя таблица, их здесь нет)."""
    col = _FUNNEL_STEPS.get(step)
    if not (token and col):
        return
    now = int(time.time())
    with _conn() as c:
        c.execute(f"UPDATE users SET {col}=COALESCE({col}, ?) "
                  "WHERE sub_token=?", (now, token))
        # UA нужен только на шаге скачивания и только первый — по нему видно
        # платформу, на которой человек застрял
        if step == "fetch" and ua:
            c.execute("UPDATE users SET fetch_ua=? WHERE sub_token=? "
                      "AND (fetch_ua IS NULL OR fetch_ua='')", (ua[:120], token))


def mark_giveaway_entry(uid):
    """Отмечает участие в розыгрыше.

    COALESCE: место в списке определяет ПЕРВОЕ нажатие. Иначе человек,
    зашедший перепроверить статус, уезжал бы в конец очереди — а список
    к тому моменту уже опубликован.
    """
    with _conn() as c:
        c.execute("UPDATE users SET giveaway_at=COALESCE(giveaway_at, ?) "
                  "WHERE user_id=?", (int(time.time()), uid))


def is_giveaway_entry(uid):
    u = get_user(uid)
    return bool(u and u["giveaway_at"])


def giveaway_entries():
    """Подтвердившие участие — в порядке подтверждения.

    Подписку на канал здесь не проверяем: её знает только Telegram, это
    делает бот перед розыгрышем. Заблокировавших бота не берём — приз им
    не вручить. Ключ обязателен: это второе условие акции.
    """
    with _conn() as c:
        rows = c.execute(
            "SELECT user_id, username, source FROM users "
            "WHERE giveaway_at IS NOT NULL AND sub_until>0 "
            "AND COALESCE(blocked,0)=0 "
            "ORDER BY giveaway_at, user_id"
        ).fetchall()
        return [(r["user_id"], r["username"], r["source"]) for r in rows]


def giveaway_count():
    """Сколько мест уже занято. Отдельным запросом, а не len(entries):
    проверка потолка идёт на каждое нажатие кнопки."""
    with _conn() as c:
        return c.execute(
            "SELECT COUNT(*) FROM users WHERE giveaway_at IS NOT NULL"
        ).fetchone()[0] or 0


def giveaway_suspicious(window_hours=1):
    """Признаки накрутки среди участников.

    `no_touch` — участники, которые ни разу не открывали страницу
    подключения. Это и есть рабочий признак: накрутчику нужна отметка
    об участии, а настраивать VPN на полусотне аккаунтов он не станет.
    Живой человек, пришедший за подарком, чаще всего ключ хотя бы
    попробует — ради него он и заходил.

    Первым признаком было «участвовал в течение часа после запуска бота»,
    но при обязательной паузе перед участием туда попадает и честный
    человек, пришедший по рекламе, — тревога горела бы всегда.

    `recent` — пришло за последний час, чтобы видеть всплески.
    """
    with _conn() as c:
        no_touch = c.execute(
            "SELECT COUNT(*) FROM users WHERE giveaway_at IS NOT NULL "
            "AND page_at IS NULL").fetchone()[0] or 0
        recent = c.execute(
            "SELECT COUNT(*) FROM users WHERE giveaway_at > ?",
            (int(time.time()) - window_hours * 3600,)).fetchone()[0] or 0
        return {"no_touch": no_touch, "recent": recent}


def giveaway_reset():
    """Очищает список участников — перед новой акцией.

    Без этого во второй розыгрыш автоматически попадут все участники
    первого, и победителем окажется человек, который про новую акцию
    даже не слышал.
    """
    with _conn() as c:
        c.execute("UPDATE users SET giveaway_at=NULL")


def funnel_rows():
    """Все, кому выдавали ключ, с отметками шагов. Для отчёта /funnel."""
    with _conn() as c:
        rows = c.execute(
            "SELECT user_id, username, sub_until, trial_used, source, sub_token, "
            "       page_at, import_at, fetch_at, fetch_ua "
            "FROM users WHERE sub_until>0 ORDER BY sub_until DESC"
        ).fetchall()
        return [dict(r) for r in rows]


def source_stats():
    """Сводка по источникам: сколько пришло, скольким выдали ключ, сколько
    заплатили. Оплатой считаем и звёзды (payments), и карту (bot_invoices)."""
    with _conn() as c:
        rows = c.execute(
            "SELECT COALESCE(NULLIF(u.source,''),'—') AS src, "
            "COUNT(*) AS total, "
            "SUM(CASE WHEN u.sub_until>0 THEN 1 ELSE 0 END) AS activated, "
            "SUM(CASE WHEN EXISTS(SELECT 1 FROM payments p WHERE p.user_id=u.user_id) "
            "      OR EXISTS(SELECT 1 FROM bot_invoices b WHERE b.user_id=u.user_id "
            "                AND b.status='paid') "
            "    THEN 1 ELSE 0 END) AS paid "
            "FROM users u GROUP BY src ORDER BY total DESC"
        ).fetchall()
        return [(r["src"], r["total"], r["activated"] or 0, r["paid"] or 0)
                for r in rows]


def update_username(uid, username):
    """Обновляет юзернейм. Раз человек написал боту — он его не блокирует,
    поэтому заодно снимаем флаг blocked, если тот стоял."""
    with _conn() as c:
        c.execute("UPDATE users SET username=?, blocked=0 WHERE user_id=?",
                  (username, uid))


# ===== Промокоды =====

def create_promo(code, bonus_days, max_uses=0):
    """Создаёт/обновляет промокод, сохраняя уже накопленные активации."""
    code = code.strip().upper()
    with _conn() as c:
        used = c.execute("SELECT used_count FROM promo_codes WHERE code=?",
                         (code,)).fetchone()
        c.execute(
            "INSERT OR REPLACE INTO promo_codes"
            "(code, bonus_days, max_uses, used_count, active, created_at) "
            "VALUES(?,?,?,?,1,?)",
            (code, bonus_days, max_uses, used["used_count"] if used else 0,
             int(time.time())),
        )


def redeem_promo(code, uid):
    """Пробует активировать промокод. Возвращает (bonus_days, None) при успехе
    или (None, причина): not_found / exhausted / already."""
    code = (code or "").strip().upper()
    now = int(time.time())
    with _conn() as c:
        p = c.execute(
            "SELECT bonus_days, max_uses, used_count FROM promo_codes "
            "WHERE code=? AND active=1", (code,)).fetchone()
        if not p:
            return (None, "not_found")
        if p["max_uses"] and p["used_count"] >= p["max_uses"]:
            return (None, "exhausted")
        if c.execute("SELECT 1 FROM promo_uses WHERE code=? AND user_id=?",
                     (code, uid)).fetchone():
            return (None, "already")
        c.execute("INSERT INTO promo_uses(code, user_id, used_at) VALUES(?,?,?)",
                  (code, uid, now))
        c.execute("UPDATE promo_codes SET used_count=used_count+1 WHERE code=?",
                  (code,))
        return (p["bonus_days"], None)


def add_days(uid, days):
    """Продлевает подписку на N дней (от текущего конца или от сейчас).

    Сбрасывает флаг напоминания об окончании: срок стал другой, значит про
    него надо будет напомнить заново.
    """
    now = int(time.time())
    u = get_user(uid)
    base = max(now, u["sub_until"]) if (u and u["sub_until"]) else now
    new_until = base + days * 86400
    with _conn() as c:
        c.execute("UPDATE users SET sub_until=?, expiry_reminded=0 WHERE user_id=?",
                  (new_until, uid))
    return new_until


def is_active(uid):
    u = get_user(uid)
    return bool(u and u["sub_until"] and u["sub_until"] > int(time.time()))


def mark_trial_used(uid):
    with _conn() as c:
        # новый триал — сбрасываем флаг напоминания, чтобы напомнить и о нём
        c.execute("UPDATE users SET trial_used=1, trial_reminded=0 WHERE user_id=?", (uid,))


def active_users(now):
    """Все пользователи с активной подпиской — для разовой рассылки."""
    with _conn() as c:
        rows = c.execute(
            "SELECT user_id, sub_until FROM users WHERE sub_until>?", (now,)
        ).fetchall()
        return [(r["user_id"], r["sub_until"]) for r in rows]


def all_user_ids():
    """Все, кто запускал бота и не блокировал его — для общей рассылки."""
    with _conn() as c:
        return [r["user_id"] for r in
                c.execute("SELECT user_id FROM users WHERE COALESCE(blocked,0)=0")]


def mark_blocked(uid, blocked=True):
    """Отмечает, что пользователь заблокировал бота (или вернулся)."""
    with _conn() as c:
        c.execute("UPDATE users SET blocked=? WHERE user_id=?",
                  (1 if blocked else 0, uid))


def blocked_count():
    with _conn() as c:
        return c.execute(
            "SELECT COUNT(*) AS n FROM users WHERE blocked=1").fetchone()["n"]


def trial_reminder_candidates(now, within_seconds):
    """Пользователи с активным триалом, у кого до конца осталось не больше
    within_seconds, и кому ещё не напоминали. Для авторассылки «триал
    заканчивается»."""
    with _conn() as c:
        rows = c.execute(
            "SELECT user_id, sub_until FROM users "
            "WHERE trial_used=1 AND trial_reminded=0 "
            "AND sub_until>? AND sub_until<=?",
            (now, now + within_seconds),
        ).fetchall()
        return [(r["user_id"], r["sub_until"]) for r in rows]


def mark_trial_reminded(uid):
    with _conn() as c:
        c.execute("UPDATE users SET trial_reminded=1 WHERE user_id=?", (uid,))


def set_referred_by(uid, ref_id):
    """Записывает пригласившего — только если ещё не записан."""
    with _conn() as c:
        c.execute(
            "UPDATE users SET referred_by=? WHERE user_id=? AND referred_by IS NULL",
            (ref_id, uid),
        )


def pending_referrer(uid):
    """Кому положен бонус за этого пользователя, если он ещё не выдан.

    Возвращает id пригласившего или None. Пригласивший должен существовать —
    иначе начислять некому.
    """
    with _conn() as c:
        r = c.execute(
            "SELECT u.referred_by AS ref FROM users u "
            "JOIN users inv ON inv.user_id = u.referred_by "
            "WHERE u.user_id=? AND COALESCE(u.ref_rewarded,0)=0",
            (uid,)).fetchone()
        return r["ref"] if r else None


def mark_ref_rewarded(uid):
    with _conn() as c:
        c.execute("UPDATE users SET ref_rewarded=1 WHERE user_id=?", (uid,))


def expiry_reminder_candidates(now, within_seconds):
    """Кому пора напомнить, что подписка заканчивается.

    Берём всех с активной подпиской, которая кончится в ближайшие
    within_seconds, кому про этот срок ещё не напоминали и кто не блокировал
    бота. Тех, кому сейчас положено напоминание о триале, исключаем — иначе
    человек получит два сообщения об одном сроке. После того как триальное
    напоминание отправлено (trial_reminded=1), продления попадают уже сюда.
    """
    with _conn() as c:
        rows = c.execute(
            "SELECT user_id, sub_until FROM users "
            "WHERE COALESCE(expiry_reminded,0)=0 AND COALESCE(blocked,0)=0 "
            "AND NOT (COALESCE(trial_used,0)=1 AND COALESCE(trial_reminded,0)=0) "
            "AND sub_until>? AND sub_until<=?",
            (now, now + within_seconds),
        ).fetchall()
        return [(r["user_id"], r["sub_until"]) for r in rows]


def mark_expiry_reminded(uid):
    with _conn() as c:
        c.execute("UPDATE users SET expiry_reminded=1 WHERE user_id=?", (uid,))


def lapsed_users(now, max_age_days=180):
    """У кого подписка была и закончилась — для рассылки «вернись».

    Слишком старых не берём: человек, пропавший полгода назад, скорее всего
    уже не вернётся, а рассылка ему — лишний повод заблокировать бота.
    """
    with _conn() as c:
        rows = c.execute(
            "SELECT user_id, sub_until FROM users "
            "WHERE COALESCE(blocked,0)=0 AND sub_until>0 AND sub_until<? "
            "AND sub_until>? ORDER BY sub_until DESC",
            (now, now - max_age_days * 86400),
        ).fetchall()
        return [(r["user_id"], r["sub_until"]) for r in rows]


def advocacy_candidates(now, min_days_active=10):
    """Кого попросить порекомендовать сервис.

    Условие: подписка активна и человек с нами уже min_days_active дней —
    значит успел попользоваться и может судить. Просим один раз.
    """
    with _conn() as c:
        rows = c.execute(
            "SELECT user_id FROM users "
            "WHERE COALESCE(ref_asked,0)=0 AND COALESCE(blocked,0)=0 "
            "AND sub_until>? AND created_at IS NOT NULL AND created_at<=?",
            (now, now - min_days_active * 86400),
        ).fetchall()
        return [r["user_id"] for r in rows]


def mark_ref_asked(uid):
    with _conn() as c:
        c.execute("UPDATE users SET ref_asked=1 WHERE user_id=?", (uid,))


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


def get_web_user_by_tg(tg_id):
    """Веб-аккаунт, связанный с этим Telegram-пользователем, или None."""
    with _conn() as c:
        return c.execute(
            "SELECT * FROM web_users WHERE telegram_id=?", (tg_id,)
        ).fetchone()


def get_or_create_web_user_by_tg(tg_id):
    """Возвращает id веб-аккаунта для входа через Telegram, создавая при
    необходимости. Email — синтетический плейсхолдер (вход только через
    Telegram), пароль пустой. Подписка у такого аккаунта берётся из
    бот-идентичности по telegram_id."""
    row = get_web_user_by_tg(tg_id)
    if row:
        return row["id"]
    with _conn() as c:
        cur = c.execute(
            "INSERT INTO web_users(email, password_hash, verified, created_at, "
            "telegram_id) VALUES(?,?,1,?,?)",
            (f"tg{tg_id}@telegram.user", "", int(time.time()), tg_id),
        )
        return cur.lastrowid


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


def find_by_hy_token(token):
    """Ищет владельца старого Hysteria2-токена из ранее выданных ссылок.

    Возвращает (kind, id, sub_until): kind — "web" или "bot", либо None.
    Нужно, чтобы ссылки вида /vpnsub/<токен>, уже разосланные клиентам,
    продолжали работать после перехода на подписки Marzban.
    """
    if not token:
        return None
    with _conn() as c:
        r = c.execute("SELECT id, sub_until FROM web_users WHERE hy_token=?",
                      (token,)).fetchone()
        if r:
            return ("web", r["id"], r["sub_until"])
        r = c.execute("SELECT user_id, sub_until FROM users WHERE hy_token=?",
                      (token,)).fetchone()
        if r:
            return ("bot", r["user_id"], r["sub_until"])
    return None


def web_set_sub_url(uid, sub_url):
    """Обновляет только ссылку-подписку, не трогая срок.

    Нужно, чтобы «вылечить» пользователей, у которых в sub_url осталось
    наследие Hysteria2 вместо адреса подписки Marzban.
    """
    with _conn() as c:
        c.execute("UPDATE web_users SET sub_url=? WHERE id=?", (sub_url, uid))


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


def owner_stats():
    """Сводка для команды /stats.

    Отдельно от stats(): та отдаётся наружу через API и сознательно
    короткая. Здесь же то, по чему принимаются решения, — с разбивкой
    по срокам, чтобы был виден рост, а не одно число.

    Про «пользователей в месяц» в профиле бота: Telegram считает тех, кто
    ЗА 30 ДНЕЙ что-то нажимал. Человек, который взял ключ и молча им
    пользуется, оттуда выпадает, оставаясь клиентом. Поэтому здесь
    считаем и приход, и оплаты — их Telegram не видит вовсе.
    """
    now = int(time.time())
    d7, d30 = now - 7 * 86400, now - 30 * 86400
    with _conn() as c:
        def one(sql, *a):
            return c.execute(sql, a).fetchone()[0] or 0

        paid_ids = ("SELECT user_id FROM payments "
                    "UNION SELECT user_id FROM bot_invoices WHERE status='paid'")
        return {
            "total":       one("SELECT COUNT(*) FROM users"),
            "new_7":       one("SELECT COUNT(*) FROM users WHERE created_at>?", d7),
            "new_30":      one("SELECT COUNT(*) FROM users WHERE created_at>?", d30),
            "with_key":    one("SELECT COUNT(*) FROM users WHERE sub_until>0"),
            "active":      one("SELECT COUNT(*) FROM users WHERE sub_until>?", now),
            "paying":      one(f"SELECT COUNT(*) FROM ({paid_ids})"),
            "blocked":     one("SELECT COUNT(*) FROM users "
                               "WHERE COALESCE(blocked,0)=1"),
            "giveaway":    one("SELECT COUNT(*) FROM users "
                               "WHERE giveaway_at IS NOT NULL"),
        }


def stats():
    now = int(time.time())
    with _conn() as c:
        users = c.execute("SELECT COUNT(*) AS n FROM users").fetchone()["n"]
        active = c.execute("SELECT COUNT(*) AS n FROM users WHERE sub_until > ?", (now,)).fetchone()["n"]
        payments = c.execute("SELECT COUNT(*) AS n FROM payments").fetchone()["n"]
        stars = c.execute("SELECT COALESCE(SUM(stars), 0) AS s FROM payments").fetchone()["s"]
    return {"users": users, "active_subscriptions": active, "payments": payments, "stars_total": stars}
