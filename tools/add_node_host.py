"""Добавляет в подписку хосты, указывающие на узел (ноду) Marzban.

Зачем: Marzban не выводит ноды в подписку сам. Ссылки строятся только из
хостов (Host Settings), а нода — это лишь второй Xray с тем же конфигом.
Так и вышло с Нидерландами: узел подключён и работает (connected, xray
25.3.6), но оба хоста смотрели на {SERVER_IP}, то есть на финский сервер
с панелью, и в подписке нидерландского сервера не было вовсе.

Новый хост копируется с существующего хоста ТОГО ЖЕ инбаунда: sni, path,
host и прочие транспортные поля обязаны остаться прежними — нода отдаёт
тот же REALITY, и чужой SNI сломал бы рукопожатие. Меняются только адрес
и подпись.

Apple-клиенты это не затрагивает: отбор в sub.py идёт по параметру
type=xhttp, а не по названию, поэтому iPhone автоматически получит только
TCP-хосты (финский и нидерландский), а Android — все.

    docker compose exec -T web python tools/add_node_host.py 78.17.59.224           # план
    docker compose exec -T web python tools/add_node_host.py 78.17.59.224 --apply   # применить

ВНИМАНИЕ: PUT /api/hosts перезаписывает карту хостов целиком. Поэтому
скрипт никогда не трогает существующие записи, а перед записью печатает
текущую карту — это ваш откат.
"""
import argparse
import copy
import json
import socket
import sys

import requests

sys.path.insert(0, "/app")

from bot.panel import (PANEL_URL, TIMEOUT, VERIFY, _configured,  # noqa: E402
                       _headers, _login)


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


def inbound_ports(token):
    """{tag: port} — порт хоста берётся из инбаунда, когда в хосте он пуст."""
    r = requests.get(f"{PANEL_URL}/api/inbounds", headers=_headers(token),
                     timeout=TIMEOUT, verify=VERIFY)
    r.raise_for_status()
    return {i["tag"]: i.get("port")
            for items in r.json().values() for i in items}


def kind(tag):
    """Короткое имя транспорта для подписи: VLESS XHTTP REALITY → XHTTP."""
    for word in ("XHTTP", "TCP", "GRPC", "WS"):
        if word in tag.upper():
            return word
    return tag


def reachable(address, port):
    """Принимает ли сервер соединение на этот порт (3 секунды на попытку)."""
    if not port:
        return None
    try:
        with socket.create_connection((address, int(port)), timeout=3):
            return True
    except OSError:
        return False


def main():
    ap = argparse.ArgumentParser(description="Добавить хосты, указывающие на ноду")
    ap.add_argument("address", help="адрес узла, напр. 78.17.59.224")
    ap.add_argument("--name", default="Netherlands", help="название в подписке")
    ap.add_argument("--flag", default="\U0001F1F3\U0001F1F1", help="флаг в подписке")
    ap.add_argument("--apply", action="store_true", help="записать изменения")
    args = ap.parse_args()

    if not _configured():
        sys.exit("Панель не настроена: нет PANEL_URL / PANEL_USERNAME / PANEL_PASSWORD.")

    token = _login()
    hosts = get_hosts(token)
    ports = inbound_ports(token)

    print("=== ТЕКУЩАЯ КАРТА ХОСТОВ (сохраните — это откат) ===")
    print(json.dumps(hosts, ensure_ascii=False, indent=2))

    print(f"\n=== ДОСТУПНОСТЬ {args.address} ===")
    dead = []
    for tag in hosts:
        port = ports.get(tag)
        ok = reachable(args.address, port)
        mark = {True: "отвечает", False: "НЕ ОТВЕЧАЕТ", None: "порт неизвестен"}[ok]
        print(f"  {tag}: порт {port} — {mark}")
        if ok is False:
            dead.append(tag)

    print("\n=== ПЛАН ===")
    updated = copy.deepcopy(hosts)
    added = 0
    for tag, items in hosts.items():
        if not items:
            print(f"  ! {tag}: нет ни одного хоста — копировать не с чего, пропускаю")
            continue
        if any((h.get("address") or "") == args.address for h in items):
            print(f"  = {tag}: хост на {args.address} уже есть, пропускаю")
            continue
        h = copy.deepcopy(items[0])
        h.pop("id", None)
        h["address"] = args.address
        h["remark"] = f"{args.flag} IKK VPN — {args.name} ({kind(tag)})"
        updated[tag] = items + [h]
        added += 1
        print(f"  + {tag}: remark={h['remark']!r} address={h['address']!r} "
              f"sni={h.get('sni')!r} port={h.get('port')} (пусто = из инбаунда)")

    if not added:
        print("\nДобавлять нечего.")
        return

    if dead:
        print(f"\n[ВНИМАНИЕ] не отвечают порты: {', '.join(dead)}. "
              "Хост добавить можно, но у клиентов он работать не будет — "
              "сначала откройте порт на сервере узла.")

    if not args.apply:
        print("\nЭто предпросмотр, НИЧЕГО НЕ ИЗМЕНЕНО.")
        print(f"Применить: docker compose exec -T web python tools/add_node_host.py "
              f"{args.address} --apply")
        return

    print("\nЗаписываю…")
    put_hosts(token, updated)

    print("\n=== РЕЗУЛЬТАТ ===")
    for tag, items in get_hosts(token).items():
        print(f"  {tag}: хостов {len(items)}")
        for h in items:
            print(f"     · {h.get('remark')} → {h.get('address')}")
    print("\nГотово. Клиентам достаточно обновить подписку в приложении.")


if __name__ == "__main__":
    main()
