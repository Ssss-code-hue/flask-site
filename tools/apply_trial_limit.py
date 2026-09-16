"""Ставит бесплатным пользователям урезанный лимит трафика.

Трогает только тех, кто ещё НЕ израсходовал новый лимит: у кого расход
больше, лимит остаётся прежним — иначе человек отключится в ту же минуту,
хотя ничего не нарушал.

    python tools/apply_trial_limit.py --db /root/flask-site/data/ikk_bot.db
    python tools/apply_trial_limit.py --db ... --apply     # сделать

Без --apply только показывает, что изменится. Платящих не трогает никогда.
"""
import argparse
import os
import sqlite3
import sys
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).resolve().parent))
from status import load_env                      # noqa: E402  (тот же разбор .env)

GB = 1024 ** 3


def payers(db_path):
    """id всех, кто платил хоть раз: им лимит не режем."""
    c = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    rows = c.execute("SELECT user_id FROM payments "
                     "UNION SELECT user_id FROM bot_invoices WHERE status='paid'")
    return {r[0] for r in rows}


def main():
    ap = argparse.ArgumentParser(description="Урезанный лимит трафика бесплатным")
    ap.add_argument("--env", default=".env")
    ap.add_argument("--db", default="/root/flask-site/data/ikk_bot.db")
    ap.add_argument("--gb", type=int, default=int(os.environ.get("TRIAL_DATA_LIMIT_GB", "20")))
    ap.add_argument("--apply", action="store_true", help="выполнить, а не показать")
    args = ap.parse_args()

    load_env(args.env)
    url = os.environ.get("PANEL_URL", "").rstrip("/")
    user, password = os.environ.get("PANEL_USERNAME"), os.environ.get("PANEL_PASSWORD")
    if not (url and user and password):
        print("[!] Не заданы PANEL_URL / PANEL_USERNAME / PANEL_PASSWORD")
        return 1
    verify = os.environ.get("PANEL_VERIFY_SSL", "1") != "0"

    r = requests.post(f"{url}/api/admin/token",
                      data={"username": user, "password": password},
                      timeout=20, verify=verify)
    r.raise_for_status()
    head = {"Authorization": "Bearer " + r.json()["access_token"]}

    paid = payers(args.db)
    limit = args.gb * GB
    users = requests.get(f"{url}/api/users", params={"limit": 1000},
                         headers=head, timeout=30, verify=verify).json().get("users", [])

    plan, skip_paid, skip_over, skip_same = [], 0, 0, 0
    for u in users:
        name = u.get("username", "")
        uid = int(name.split("_")[1]) if name.startswith("ikk_") and name.split("_")[1].isdigit() else None
        if uid in paid:
            skip_paid += 1
            continue
        used = u.get("used_traffic") or 0
        if used >= limit:
            skip_over += 1                 # уже за новым лимитом — не трогаем
            continue
        if (u.get("data_limit") or 0) == limit:
            skip_same += 1
            continue
        plan.append((name, used))

    print(f"Новый лимит: {args.gb} ГБ. Пользователей в панели: {len(users)}")
    print(f"  поставим лимит:           {len(plan)}")
    print(f"  пропустим — платили:      {skip_paid}")
    print(f"  пропустим — уже больше:   {skip_over}  (у них лимит остаётся прежним)")
    print(f"  пропустим — лимит уже тот:{skip_same}")
    for name, used in plan[:10]:
        print(f"    {name:<24} израсходовано {used / GB:.1f} ГБ")
    if len(plan) > 10:
        print(f"    … и ещё {len(plan) - 10}")

    if not args.apply:
        print("\nЭто предпросмотр. Чтобы применить, добавьте --apply")
        return 0

    done = failed = 0
    for name, _ in plan:
        try:
            rr = requests.put(f"{url}/api/user/{name}", headers=head,
                              json={"data_limit": limit,
                                    "data_limit_reset_strategy": "month"},
                              timeout=20, verify=verify)
            rr.raise_for_status()
            done += 1
        except Exception as e:
            failed += 1
            print(f"    [!] {name}: {type(e).__name__}")
    print(f"\nГотово. Обновлено: {done}, ошибок: {failed}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
