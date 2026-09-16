"""Сводка состояния боевого сервиса — с любой машины, где есть .env.

Зачем: панель, узлы, база и сайт живут в разных местах, и «всё ли в порядке»
до сих пор собиралось руками из пяти команд на сервере. Здесь всё одним
запуском и без SSH — панель отвечает по HTTPS, сайт тоже.

    venv/bin/python tools/status.py            # взять настройки из .env
    venv/bin/python tools/status.py --env .env.local
    venv/bin/python tools/status.py --top 10   # сколько строк в топе трафика

Секреты не печатаются никогда — только имена переменных и признак «задана».
Скрипт только читает: ни одного запроса, меняющего данные, здесь нет.
"""
import argparse
import os
import sqlite3
import sys
import time
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parent.parent


def load_env(path):
    """Читает .env сам, чтобы секреты не проходили через командную строку."""
    p = Path(path)
    if not p.is_absolute():
        p = ROOT / p
    if not p.exists():
        print(f"[!] Файл настроек {p} не найден")
        return {}
    env = {}
    for line in p.read_text(encoding="utf-8", errors="replace").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        env[k.strip()] = v.strip().strip('"').strip("'")
    os.environ.update(env)
    return env


def head(title):
    print()
    print("=" * 62)
    print(title)
    print("=" * 62)


def fmt_gb(b):
    return f"{b / 1024 ** 3:.1f} ГБ"


def fmt_ts(ts):
    return time.strftime("%d.%m.%Y", time.localtime(ts)) if ts else "—"


class Panel:
    """Только чтение: логин и GET-запросы к API Marzban."""

    def __init__(self, url, user, password, verify=True):
        self.url = url.rstrip("/")
        self.user, self.password, self.verify = user, password, verify
        self.token = None

    def login(self):
        r = requests.post(f"{self.url}/api/admin/token",
                          data={"username": self.user, "password": self.password},
                          timeout=20, verify=self.verify)
        r.raise_for_status()
        self.token = r.json()["access_token"]

    def get(self, path, **params):
        r = requests.get(f"{self.url}{path}", params=params or None,
                         headers={"Authorization": f"Bearer {self.token}"},
                         timeout=30, verify=self.verify)
        r.raise_for_status()
        return r.json()


def check_panel(top):
    url = os.environ.get("PANEL_URL", "").strip()
    user = os.environ.get("PANEL_USERNAME", "").strip()
    password = os.environ.get("PANEL_PASSWORD", "").strip()
    verify = os.environ.get("PANEL_VERIFY_SSL", "1") != "0"

    head("ПАНЕЛЬ MARZBAN")
    if not (url and user and password):
        missing = [n for n, v in (("PANEL_URL", url), ("PANEL_USERNAME", user),
                                  ("PANEL_PASSWORD", password)) if not v]
        print("[!] Не заданы:", ", ".join(missing))
        print("    Без них код уходит в демо-режим и ключи не выдаются.")
        return None

    print(f"Адрес: {url}")
    panel = Panel(url, user, password, verify)
    try:
        panel.login()
    except Exception as e:
        print(f"[ОШИБКА] Авторизация не прошла: {type(e).__name__}: {e}")
        return None
    print("[OK] Авторизация успешна")

    core_ver = None
    try:
        core = panel.get("/api/core")
        core_ver = core.get("version")
        print(f"Ядро мастера: xray {core_ver}, статус {core.get('started') and 'запущено' or 'ОСТАНОВЛЕНО'}")
    except Exception as e:
        print(f"[!] /api/core недоступен: {e}")

    # Узлы: расхождение версии xray с мастером роняет REALITY молча
    try:
        nodes = panel.get("/api/nodes")
        print(f"\nУзлы ({len(nodes)}):")
        for n in nodes:
            ver = n.get("xray_version")
            mark = ""
            if core_ver and ver and ver.split(".")[0:2] != core_ver.split(".")[0:2]:
                mark = f"  <-- ВЕРСИЯ РАСХОДИТСЯ С МАСТЕРОМ ({core_ver})"
            status = n.get("status")
            bad = "" if status == "connected" else "  <-- НЕ ПОДКЛЮЧЁН"
            print(f"  {n.get('name'):<20} {n.get('address'):<16} {status:<12} xray {ver or '?'}{mark}{bad}")
            if n.get("message"):
                print(f"      сообщение: {n['message']}")
    except Exception as e:
        print(f"[!] /api/nodes недоступен: {e}")

    try:
        inb = panel.get("/api/inbounds")
        names = [i.get("tag") for group in inb.values() for i in group]
        print("\nИнбаунды:", ", ".join(names) or "нет")
    except Exception as e:
        print(f"[!] /api/inbounds недоступен: {e}")

    return panel


def check_users(panel, top):
    if not panel:
        return
    head("ПОЛЬЗОВАТЕЛИ В ПАНЕЛИ")
    try:
        data = panel.get("/api/users", limit=1000)
    except Exception as e:
        print(f"[!] /api/users недоступен: {e}")
        return
    users = data.get("users", data if isinstance(data, list) else [])
    by_status = {}
    never = []
    web = bot = 0
    prefix = os.environ.get("WEB_USERNAME_PREFIX", "web_")
    for u in users:
        by_status[u.get("status")] = by_status.get(u.get("status"), 0) + 1
        if not u.get("online_at") and not u.get("used_traffic"):
            never.append(u)
        if str(u.get("username", "")).startswith(prefix):
            web += 1
        else:
            bot += 1
    print(f"Всего: {len(users)}  (сайт «{prefix}»: {web}, бот: {bot})")
    print("По статусу: " + ", ".join(f"{k}: {v}" for k, v in sorted(by_status.items())))

    active = [u for u in users if u.get("status") == "active"]
    never_active = [u for u in never if u.get("status") == "active"]
    print(f"\nНи разу не подключались: {len(never)} из {len(users)}"
          f"  (из них с активной подпиской: {len(never_active)})")
    if never_active:
        print("  Это и есть дыра воронки — ключ выдан, человек им не воспользовался:")
        for u in never_active[:top]:
            print(f"    {u.get('username'):<28} до {fmt_ts(u.get('expire'))}")
        if len(never_active) > top:
            print(f"    … и ещё {len(never_active) - top}")

    used = sorted((u for u in users if u.get("used_traffic")),
                  key=lambda u: -u["used_traffic"])
    if used:
        print(f"\nТоп по трафику (активных: {len(active)}):")
        for u in used[:top]:
            limit = f" из {fmt_gb(u['data_limit'])}" if u.get("data_limit") else ""
            print(f"    {u.get('username'):<28} {fmt_gb(u['used_traffic']):>10}{limit}")

    soon = [u for u in active if u.get("expire")
            and 0 < u["expire"] - time.time() < 3 * 86400]
    print(f"\nЗакончится в ближайшие 3 дня: {len(soon)}")


def check_site(site):
    head(f"САЙТ {site}")
    for path in ("/", "/api/health", "/tariffs", "/guide"):
        try:
            t0 = time.time()
            r = requests.get(site + path, timeout=15,
                             headers={"User-Agent": "ikk-status/1.0"})
            print(f"  {path:<14} {r.status_code}  {(time.time() - t0) * 1000:.0f} мс")
        except Exception as e:
            print(f"  {path:<14} НЕ ОТВЕЧАЕТ ({type(e).__name__})")


def check_payments():
    head("КАССЫ")
    provider = os.environ.get("PAY_PROVIDER", "").strip().lower()
    platega = bool(os.environ.get("PLATEGA_MERCHANT_ID") and os.environ.get("PLATEGA_SECRET"))
    lolz = bool(os.environ.get("LOLZ_TOKEN") and os.environ.get("LOLZ_MERCHANT_ID"))
    print(f"PAY_PROVIDER: {provider or '(не задан)'}")
    print(f"Ключи заданы — Platega: {'да' if platega else 'нет'}, Lolz: {'да' if lolz else 'нет'}")
    if not provider and platega and lolz:
        print("[!] При пустом PAY_PROVIDER (pay.py:48) клиентам показываются ОБЕ кассы —")
        print("    на сайте видна кнопка «Lolzteam». Лечится PAY_PROVIDER=platega.")
    print("(проверяются переменные ЭТОГО файла настроек; на сервере .env свой)")


def check_db(path):
    head("БАЗА")
    p = Path(path)
    if not p.is_absolute():
        p = ROOT / p
    if not p.exists():
        print(f"[!] {p} не найдена")
        return
    print(f"Файл: {p}")
    now = int(time.time())
    with sqlite3.connect(f"file:{p}?mode=ro", uri=True) as c:
        q = lambda sql, *a: c.execute(sql, a).fetchone()[0]
        # Возраст берём по последней записи, а не по дате файла: копирование
        # обновляет mtime и снимок начинает выглядеть свежим.
        last = q("select max(created_at) from users") or 0
        age = (now - last) / 86400
        print(f"Последний пользователь: {fmt_ts(last)} ({age:.0f} дн. назад)")
        if age > 1:
            print("[!] Это снимок, а не боевая база — цифры ниже устарели на этот срок.")
        print(f"Пользователей бота: {q('select count(*) from users')}"
              f"  | с активной подпиской: {q('select count(*) from users where sub_until>?', now)}"
              f"  | заблокировали бота: {q('select count(*) from users where blocked=1')}")
        print(f"Аккаунтов сайта: {q('select count(*) from web_users')}")
        print(f"Оплат Stars: {q('select count(*) from payments')}"
              f"  | счетов картой: {q('select count(*) from bot_invoices')}"
              f"  (оплачено: {q('select count(*) from bot_invoices where status=?', 'paid')})")
        rows = c.execute("select source, count(*) from users group by source "
                         "order by count(*) desc").fetchall()
        if rows:
            print("Источники: " + ", ".join(f"{s or 'без метки'}: {n}" for s, n in rows))


def main():
    ap = argparse.ArgumentParser(description="Состояние боевого IKK VPN")
    ap.add_argument("--env", default=".env", help="файл настроек (по умолчанию .env)")
    ap.add_argument("--db", default=None, help="база для сводки (по умолчанию DB_PATH)")
    ap.add_argument("--top", type=int, default=8, help="сколько строк в списках")
    ap.add_argument("--site", default="https://ikkvpn.com",
                    help="какой сайт проверять (по умолчанию боевой)")
    ap.add_argument("--no-panel", action="store_true", help="не ходить в панель")
    args = ap.parse_args()

    env = load_env(args.env)
    print(f"Настройки: {args.env} ({len(env)} переменных, значения не печатаются)")

    panel = None if args.no_panel else check_panel(args.top)
    check_users(panel, args.top)
    check_site(args.site.rstrip("/"))
    check_payments()
    check_db(args.db or os.environ.get("DB_PATH", "ikk_bot.db"))
    print()


if __name__ == "__main__":
    sys.exit(main())
