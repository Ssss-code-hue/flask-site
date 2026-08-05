"""Резервная копия базы SQLite — с ротацией и отправкой в Telegram.

Зачем отдельный скрипт, а не `cp`: на работающей базе обычное
копирование может застать её в середине транзакции, и копия окажется
битой ровно тогда, когда понадобится. Родной механизм SQLite
(Connection.backup) снимает согласованный слепок на живой базе.

Копия уезжает в Telegram владельцу: файл, лежащий на том же сервере,
что и оригинал, спасает от повреждения базы, но не от потери сервера.

Запуск:
    python tools/backup_db.py                      # взять DB_PATH из окружения
    python tools/backup_db.py --db /путь/база.db --keep 14 --send
"""
import argparse
import os
import sqlite3
import sys
import time
from contextlib import closing
from datetime import datetime, timedelta
from pathlib import Path

import requests


def make_backup(src, out_dir):
    """Согласованный слепок базы. Возвращает путь к файлу копии."""
    src = Path(src)
    if not src.exists():
        raise SystemExit(f"База не найдена: {src}")
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    # Имя обязано быть уникальным: две копии, снятые подряд, не должны
    # затирать друг друга — иначе неудачная вторая уничтожит хорошую первую.
    # Одного времени мало, при быстром повторе секунда та же.
    base = f"{src.stem}_{datetime.now():%Y-%m-%d_%H%M%S}"
    dst = out_dir / f"{base}.db"
    n = 1
    while dst.exists():
        dst = out_dir / f"{base}-{n}.db"
        n += 1

    # closing обязателен: `with sqlite3.connect(...)` управляет транзакцией,
    # а соединение оставляет открытым. Дескрипторы копились бы при каждом
    # запуске, а файл оставался занятым и не удалялся при ротации.
    with closing(sqlite3.connect(f"file:{src}?mode=ro", uri=True)) as s, \
            closing(sqlite3.connect(dst)) as d:
        s.backup(d)                       # атомарно, не мешает работе бота

    # Сразу проверяем копию: бэкап, который никто не открывал, — это
    # не бэкап, а надежда. Дешевле узнать о проблеме сейчас.
    with closing(sqlite3.connect(f"file:{dst}?mode=ro", uri=True)) as c:
        if c.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
            dst.unlink(missing_ok=True)
            raise SystemExit("Копия не прошла проверку целостности, удалена")
        users = c.execute("SELECT COUNT(*) FROM users").fetchone()[0]
    return dst, users


def rotate(out_dir, keep_days):
    """Удаляет копии старше keep_days. Свежую не трогает никогда."""
    if keep_days <= 0:
        return 0
    edge = time.time() - keep_days * 86400
    removed = 0
    files = sorted(Path(out_dir).glob("*.db"), key=lambda f: f.stat().st_mtime)
    for f in files[:-1]:                  # последнюю оставляем при любом раскладе
        if f.stat().st_mtime < edge:
            f.unlink()
            removed += 1
    return removed


def send_to_telegram(path, token, chat_id, caption):
    """Отправляет файл владельцу. Лимит Telegram на документ — 50 МБ."""
    size_mb = path.stat().st_size / 1024 / 1024
    if size_mb > 49:
        print(f"[!] Копия {size_mb:.1f} МБ — больше лимита Telegram, не отправляю")
        return False
    with open(path, "rb") as f:
        r = requests.post(
            f"https://api.telegram.org/bot{token}/sendDocument",
            data={"chat_id": chat_id, "caption": caption},
            files={"document": (path.name, f)}, timeout=120)
    if not r.ok:
        # Текст ответа Telegram может содержать токен из URL — не печатаем
        print(f"[!] Telegram отклонил отправку: код {r.status_code}")
        return False
    return True


def main():
    ap = argparse.ArgumentParser(description="Резервная копия базы IKK VPN")
    ap.add_argument("--db", default=os.environ.get("DB_PATH", "ikk_bot.db"))
    ap.add_argument("--out", default=os.environ.get("BACKUP_DIR", "/root/backups"))
    ap.add_argument("--keep", type=int, default=14, help="сколько дней хранить")
    ap.add_argument("--send", action="store_true", help="отправить копию в Telegram")
    a = ap.parse_args()

    dst, users = make_backup(a.db, a.out)
    size_kb = dst.stat().st_size / 1024
    print(f"Копия: {dst} ({size_kb:.0f} КБ, пользователей: {users})")

    removed = rotate(a.out, a.keep)
    if removed:
        print(f"Удалено старых копий: {removed}")

    if a.send:
        token = os.environ.get("BOT_TOKEN")
        owner = os.environ.get("OWNER_ID")
        if not (token and owner):
            print("[!] Для отправки нужны BOT_TOKEN и OWNER_ID")
            return 1
        caption = (f"💾 Копия базы {datetime.now():%d.%m.%Y %H:%M}\n"
                   f"Пользователей: {users}")
        if send_to_telegram(dst, token, owner, caption):
            print("Отправлено в Telegram")
        else:
            return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
