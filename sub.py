"""Прокси подписок VPN через основной сайт (чистый 443 за Cloudflare).

Зачем: панель Marzban раздаёт подписки на порту 8000, который у части
провайдеров (напр. Ростелеком) ненадёжен/режется — клиент не может
скачать конфиг. Здесь сайт (ikkvpn.com, уже на 443) сам забирает
подписку с панели сервер-серверу и отдаёт клиенту по 443.

Клиент видит только https://ikkvpn.com/sub/<токен>. Сам VPN-туннель
идёт напрямую к серверу (адрес зашит внутри конфига) — не меняется.

Чтобы ссылки в панели стали вида https://ikkvpn.com/... — задайте
в /opt/marzban/.env: XRAY_SUBSCRIPTION_URL_PREFIX=https://ikkvpn.com

--------------------------------------------------------------------
Правка XHTTP-параметров на лету
--------------------------------------------------------------------
Marzban 0.8.4 жёстко зашивает в XHTTP-ссылки свои дефолты:

    scMaxConcurrentPosts: 100   и никакого xmux

С такими параметрами клиент открывает пачку параллельных TLS-соединений
к одному SNI. ТСПУ с июня 2026 детектит именно это поведение (больше 3
параллельных хендшейков к одному SNI) и «замораживает» адрес примерно на
120 секунд: TCP проходит, ClientHello уходит, а дальше пакеты молча
дропаются — без RST. Наружу выглядит как «подключился, пару секунд
поработало и умерло».

Лечится ограничением до ОДНОГО соединения (xmux.maxConnections = 1):
все потоки идут внутри одного канала, ТСПУ видит один хендшейк.
Проверено 20.07.2026: залп из 6 параллельных запросов проходит
полностью, 10 МБ качается без обрывов.

Пропатчить сам Marzban нельзя — на сервере закрыт SSH (доступна только
панель на 8000). Поэтому правим ответ здесь, при проксировании:
клиенту уходит уже исправленный конфиг, ничего вручную вставлять не надо.
"""
import base64
import binascii
import json
import logging
import os
import urllib.parse

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

# --- параметры, которыми подменяем дефолты Marzban -------------------

# Одно соединение на всё. Ключевая защита от «заморозки» ТСПУ.
XMUX = {
    "maxConnections": 1,
    "cMaxReuseTimes": "64-128",
    "cMaxLifetimeMs": 0,
    "hMaxRequestTimes": "800-900",
    "hKeepAlivePeriod": 0,
}

# scMaxConcurrentPosts намеренно отсутствует — именно он плодил потоки.
XHTTP_EXTRA = {
    "xmux": XMUX,
    "scMaxEachPostBytes": 1000000,
    "scMinPostsIntervalMs": 30,
    "xPaddingBytes": "100-1000",
}

# chrome помечен ТСПУ как подозрительный отпечаток, firefox — нет.
FINGERPRINT = "firefox"


def _fix_vless_link(link):
    """Правит один vless://-URI. У не-XHTTP ссылок меняет только fp."""
    try:
        head, _, frag = link.partition("#")
        parts = urllib.parse.urlsplit(head)
        q = dict(urllib.parse.parse_qsl(parts.query, keep_blank_values=True))

        if q.get("fp"):
            q["fp"] = FINGERPRINT

        if q.get("type") == "xhttp":
            q["extra"] = json.dumps(XHTTP_EXTRA, separators=(",", ":"))

        new = urllib.parse.urlunsplit((
            parts.scheme, parts.netloc, parts.path,
            urllib.parse.urlencode(q), "",
        ))
        return "{}#{}".format(new, frag) if frag else new
    except Exception:
        log.exception("Подписка: не смог разобрать ссылку, отдаю как есть")
        return link


def _fix_plain_text(text):
    """Правит список ссылок (по одной на строку)."""
    out = []
    for line in text.splitlines():
        s = line.strip()
        out.append(_fix_vless_link(s) if s.startswith("vless://") else line)
    return "\n".join(out)


def _fix_json_configs(data):
    """Правит формат v2ray-json: список готовых конфигов Xray."""
    for cfg in data if isinstance(data, list) else [data]:
        if not isinstance(cfg, dict):
            continue
        for ob in cfg.get("outbounds") or []:
            ss = (ob or {}).get("streamSettings") or {}
            if ss.get("network") == "xhttp":
                xs = ss.get("xhttpSettings") or {}
                xs.pop("scMaxConcurrentPosts", None)
                xs.update(XHTTP_EXTRA)
                ss["xhttpSettings"] = xs
            rs = ss.get("realitySettings") or ss.get("tlsSettings")
            if isinstance(rs, dict) and rs.get("fingerprint"):
                rs["fingerprint"] = FINGERPRINT
    return data


def _patch_body(body):
    """Определяет формат подписки и правит его.

    Marzban отдаёт три варианта: JSON-конфиги, base64 от списка ссылок
    или тот же список открытым текстом. Порядок проверок именно такой.
    """
    text = body.decode("utf-8")

    if text.lstrip()[:1] in ("[", "{"):
        try:
            fixed = _fix_json_configs(json.loads(text))
        except (ValueError, TypeError):
            log.warning("Подписка: похоже на JSON, но разобрать не вышло")
            return body
        return json.dumps(fixed, ensure_ascii=False).encode()

    if "vless://" in text:
        return _fix_plain_text(text).encode()

    try:
        raw = base64.b64decode(text + "=" * (-len(text) % 4)).decode("utf-8")
    except (binascii.Error, UnicodeDecodeError, ValueError):
        return body

    if "vless://" not in raw:
        return body
    return base64.b64encode(_fix_plain_text(raw).encode())


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

    body = r.content
    if r.status_code == 200:
        try:
            body = _patch_body(body)
        except Exception:
            log.exception("Подписка: правка не удалась, отдаю оригинал")
            body = r.content

    headers = [(k, v) for k, v in r.headers.items() if k.lower() not in _HOP]
    return Response(body, status=r.status_code, headers=headers)
