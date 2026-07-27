"""Создаёт хост для инбаундов, у которых его нет.

Зачем: в Marzban ссылки в подписке строятся не из инбаундов, а из хостов
(Host Settings). Если у инбаунда нет ни одного хоста, он пользователям
не достаётся, даже когда назначен всем. Так и вышло с VLESS TCP REALITY:
инбаунд есть, выдан всем 17 пользователям, а в подписке его нет — из-за
чего на iPhone было не к чему подключаться (XHTTP их клиент не тянет).

Новый хост копируется с уже работающего: тот же address (у нас это
шаблон {SERVER_IP}), а транспортные поля — sni, host, path — очищаются,
чтобы подтянулись из самого инбаунда. Для REALITY это важно: serverNames
у разных инбаундов свои, и копировать чужой SNI нельзя.

    docker compose exec -T web python tools/add_host.py           # показать план
    docker compose exec -T web python tools/add_host.py --apply   # применить

ВНИМАНИЕ: PUT /api/hosts в Marzban перезаписывает карту хостов целиком.
Поэтому скрипт только добавляет записи, никогда не трогая существующие,
и перед записью печатает текущую карту целиком — это ваш откат.
"""
import copy
import json
import sys

import requests

sys.path.insert(0, "/app")

from bot.panel import (PANEL_URL, TIMEOUT, VERIFY, _configured,  # noqa: E402
                       _headers, _login)

# поля, которые нельзя переносить с чужого транспорта
TRANSPORT_FIELDS = ("sni", "host", "path")


def get_hosts(token):
    r = requests.get(f"{PANEL_URL}/api/hosts", headers=_headers(token),
                     timeout=TIMEOUT, verify=VERIFY)
    r.raise_for_status()
    return r.json()


def put_hosts(token, hosts):
    r = requests.put(f"{PANEL_URL}/api/hosts", headers=_headers(token),
                     json=hosts, timeout=TIMEOUT, verify=VERIFY)
    r.raise_for_status()
    return r.json()


def donor(hosts):
    """Любой существующий хост — как образец структуры записи."""
    for items in hosts.values():
        if items:
            return items[0]
    return None


def make_host(sample, tag):
    """Хост для tag по образцу sample: адрес тот же, транспортное — пустое."""
    h = copy.deepcopy(sample)
    h["remark"] = f"🇫🇮 IKK VPN — {tag.replace('VLESS ', '').replace(' REALITY', '')}"
    h["port"] = None                      # взять из инбаунда
    for f in TRANSPORT_FIELDS:
        if f in h:
            h[f] = ""
    return h


def main():
    apply = "--apply" in sys.argv

    if not _configured():
        sys.exit("Панель не настроена: нет PANEL_URL / PANEL_USERNAME / PANEL_PASSWORD.")

    token = _login()
    hosts = get_hosts(token)

    print("=== ТЕКУЩАЯ КАРТА ХОСТОВ (сохраните — это откат) ===")
    print(json.dumps(hosts, ensure_ascii=False, indent=2))

    empty = [tag for tag, items in hosts.items() if not items]
    print("\n=== ПЛАН ===")
    if not empty:
        print("У всех инбаундов хосты есть — менять нечего.")
        return

    sample = donor(hosts)
    if sample is None:
        sys.exit("Не с чего копировать: ни у одного инбаунда нет хоста. "
                 "Заведите первый хост через панель.")

    updated = copy.deepcopy(hosts)
    for tag in empty:
        h = make_host(sample, tag)
        updated[tag] = [h]
        print(f"  + {tag}: remark={h['remark']!r} address={h.get('address')!r} "
              f"port=из инбаунда sni=из инбаунда")

    if not apply:
        print("\nЭто предпросмотр, ничего не изменено.")
        print("Применить: docker compose exec -T web python tools/add_host.py --apply")
        return

    print("\nЗаписываю…")
    put_hosts(token, updated)

    after = get_hosts(token)
    print("\n=== РЕЗУЛЬТАТ ===")
    for tag, items in after.items():
        print(f"  {tag}: хостов {len(items)}")
    if any(not items for items in after.values()):
        print("\n[ВНИМАНИЕ] где-то всё ещё пусто — проверьте панель.")
    else:
        print("\nГотово. Клиентам достаточно обновить подписку.")


if __name__ == "__main__":
    main()
