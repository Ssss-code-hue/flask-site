"""Выдаёт всем пользователям панели ВСЕ доступные инбаунды.

Зачем: пользователи, заведённые до появления второго инбаунда (или при
заданном PANEL_INBOUNDS), подключены только к части серверов. На практике
это вылезло так — в подписке был только XHTTP, а v2RayTun на iPhone его
не тянет, и подключиться было просто некуда.

Запуск (изнутри контейнера сайта, пароль берётся из его окружения):

    docker compose exec -T web python tools/fix_inbounds.py           # показать план
    docker compose exec -T web python tools/fix_inbounds.py --apply   # применить

Без --apply ничего не меняет, только печатает, кого и куда добавит.

Токен подписки при этом НЕ меняется: клиентам ничего переустанавливать не
нужно, новые серверы приедут при обычном обновлении подписки.
"""
import sys

import requests

sys.path.insert(0, "/app")

from bot.panel import (PANEL_URL, PROXIES, TIMEOUT, VERIFY,  # noqa: E402
                       _configured, _headers, _login)

PAGE = 100


def all_inbounds(token):
    """{протокол: [теги]} — все инбаунды панели по нашим протоколам."""
    r = requests.get(f"{PANEL_URL}/api/inbounds", headers=_headers(token),
                     timeout=TIMEOUT, verify=VERIFY)
    r.raise_for_status()
    return {proto: sorted(i["tag"] for i in items)
            for proto, items in r.json().items()
            if proto in PROXIES and items}


def all_users(token):
    """Все пользователи панели (страницами, чтобы не упереться в лимит)."""
    out, offset = [], 0
    while True:
        r = requests.get(f"{PANEL_URL}/api/users", headers=_headers(token),
                         params={"limit": PAGE, "offset": offset},
                         timeout=TIMEOUT, verify=VERIFY)
        r.raise_for_status()
        chunk = r.json().get("users") or []
        out.extend(chunk)
        offset += PAGE
        if len(chunk) < PAGE:
            return out


def missing(user, full):
    """Каких инбаундов пользователю не хватает: {протокол: [теги]}."""
    have = user.get("inbounds") or {}
    gaps = {}
    for proto, tags in full.items():
        lack = sorted(set(tags) - set(have.get(proto) or []))
        if lack:
            gaps[proto] = lack
    return gaps


def main():
    apply = "--apply" in sys.argv

    if not _configured():
        sys.exit("Панель не настроена: нет PANEL_URL / PANEL_USERNAME / PANEL_PASSWORD.")

    token = _login()
    full = all_inbounds(token)
    if not full:
        sys.exit("Панель не вернула ни одного инбаунда по нашим протоколам.")

    print("Инбаунды панели:")
    for proto, tags in full.items():
        print(f"  {proto}: {', '.join(tags)}")

    users = all_users(token)
    todo = [(u, gaps) for u in users if (gaps := missing(u, full))]

    print(f"\nПользователей всего: {len(users)}")
    print(f"Не хватает инбаундов: {len(todo)}")
    if not todo:
        print("\nВсё уже на месте — менять нечего.")
        return

    for u, gaps in todo:
        what = "; ".join(f"{p}: {', '.join(t)}" for p, t in gaps.items())
        print(f"  {u['username']:<24} + {what}")

    if not apply:
        print("\nЭто предпросмотр, ничего не изменено.")
        print("Применить: docker compose exec -T web python tools/fix_inbounds.py --apply")
        return

    print("\nПрименяю…")
    done = failed = 0
    for u, _ in todo:
        try:
            r = requests.put(f"{PANEL_URL}/api/user/{u['username']}",
                             headers=_headers(token), json={"inbounds": full},
                             timeout=TIMEOUT, verify=VERIFY)
            r.raise_for_status()
            done += 1
        except Exception as e:
            failed += 1
            print(f"  [ошибка] {u['username']}: {e}")

    print(f"\nОбновлено: {done}\nОшибок: {failed}")
    print("Токены подписок не менялись — клиентам достаточно обновить подписку.")


if __name__ == "__main__":
    main()
