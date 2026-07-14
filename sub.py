"""Прокси подписок VPN через основной сайт (чистый 443 за Cloudflare).

Зачем: панель Marzban раздаёт подписки на порту 8000, который у части
провайдеров (напр. Ростелеком) ненадёжен/режется — Happ не может
скачать конфиг. Здесь сайт (ikkvpn.com, уже на 443) сам забирает
подписку с панели сервер-серверу и отдаёт клиенту по 443.

Клиент видит только https://ikkvpn.com/sub/<токен>. Сам VPN-туннель
идёт напрямую к серверу (адрес зашит внутри конфига) — не меняется.

Чтобы ссылки в панели стали вида https://ikkvpn.com/... — задайте
в /opt/marzban/.env: XRAY_SUBSCRIPTION_URL_PREFIX=https://ikkvpn.com
"""
import logging
import os

import requests
from flask import Blueprint, Response, abort, request

log = logging.getLogger(__name__)
sub = Blueprint("sub", __name__)

# База панели, откуда берём подписки (валидный серт, grey-cloud → прямо на сервер)
SUB_UPSTREAM = os.environ.get("SUB_UPSTREAM", "https://panel.ikkvpn.com:8000").rstrip("/")
TIMEOUT = 20

# заголовки ответа панели, которые НЕ пробрасываем клиенту
_HOP = {"content-encoding", "transfer-encoding", "connection",
        "content-length", "keep-alive"}


@sub.route("/sub/<path:subpath>")
def proxy(subpath):
    """Отдаёт клиенту подписку, забирая её с панели Marzban.

    User-Agent пробрасываем — от него зависит формат (Happ/v2ray/clash).
    """
    upstream = f"{SUB_UPSTREAM}/sub/{subpath}"
    try:
        r = requests.get(
            upstream,
            params=request.args,
            headers={"User-Agent": request.headers.get("User-Agent", "Happ")},
            timeout=TIMEOUT,
        )
    except Exception:
        log.exception("Подписка: панель недоступна (%s)", upstream)
        abort(502)

    if r.status_code == 404:
        abort(404)

    headers = [(k, v) for k, v in r.headers.items() if k.lower() not in _HOP]
    return Response(r.content, status=r.status_code, headers=headers)
