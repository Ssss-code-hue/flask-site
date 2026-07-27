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

# --- Apple: не XHTTP, а другой инбаунд -------------------------------
#
# Ядро v2RayTun в Apple-сборке не тянет XHTTP: с нашим xmux в extra оно
# молча не поднимает outbound (туннель встаёт, трафика нет), а без него
# соединение живёт пару минут и рвётся. Проверено 27.07.2026 на iPhone.
#
# Подбирать XHTTP-параметры под это ядро — тупик: значения пришлось бы
# угадывать, а одна попытка уже вышла боком (scMaxConcurrentPosts=1 плюс
# scMinPostsIntervalMs=30 упирают сессию в ~33 запроса/с, выглядит как
# «не подключается»).
#
# На сервере есть второй инбаунд — VLESS TCP REALITY. В нём нет ни XHTTP,
# ни extra, ни xmux, то есть нечему и ломаться. Поэтому Apple-устройствам
# просто не отдаём XHTTP-серверы, оставляя им TCP+REALITY.
#
# Android и десктоп это не затрагивает: они получают подписку как раньше,
# со всеми серверами и с xmux (см. XHTTP_EXTRA).

# chrome помечен ТСПУ как подозрительный отпечаток, firefox — нет.
FINGERPRINT = "firefox"


def _is_xhttp_link(link):
    """XHTTP ли этот vless://-URI (по параметру type, а не по названию)."""
    try:
        head = link.partition("#")[0]
        q = dict(urllib.parse.parse_qsl(urllib.parse.urlsplit(head).query))
        return q.get("type") == "xhttp"
    except Exception:
        return False


def _fix_vless_link(link, apple=False):
    """Правит один vless://-URI. У не-XHTTP ссылок меняет только fp."""
    try:
        head, _, frag = link.partition("#")
        parts = urllib.parse.urlsplit(head)
        q = dict(urllib.parse.parse_qsl(parts.query, keep_blank_values=True))

        if q.get("fp"):
            q["fp"] = FINGERPRINT

        # у Apple XHTTP-ссылок не остаётся, так что extra правим только им
        if not apple and q.get("type") == "xhttp":
            q["extra"] = json.dumps(XHTTP_EXTRA, separators=(",", ":"))

        new = urllib.parse.urlunsplit((
            parts.scheme, parts.netloc, parts.path,
            urllib.parse.urlencode(q), "",
        ))
        return "{}#{}".format(new, frag) if frag else new
    except Exception:
        log.exception("Подписка: не смог разобрать ссылку, отдаю как есть")
        return link


def _fix_plain_text(text, apple=False):
    """Правит список ссылок (по одной на строку).

    apple=True — XHTTP-серверы выбрасываем совсем. Если после этого не
    осталось ни одного сервера, отдаём полный список: подписка без единого
    сервера бесполезна, пусть уж будет XHTTP.
    """
    out, seen, kept = [], False, False
    for line in text.splitlines():
        s = line.strip()
        if not s.startswith("vless://"):
            out.append(line)
            continue
        seen = True
        if apple and _is_xhttp_link(s):
            continue
        out.append(_fix_vless_link(s, apple))
        kept = True

    if apple and seen and not kept:
        log.warning("Подписка: для Apple не осталось серверов, отдаю все")
        return _fix_plain_text(text, apple=False)
    return "\n".join(out)


def _cfg_is_xhttp(cfg):
    """Есть ли в конфиге XHTTP-outbound (Marzban даёт по конфигу на сервер)."""
    for ob in cfg.get("outbounds") or []:
        if ((ob or {}).get("streamSettings") or {}).get("network") == "xhttp":
            return True
    return False


def _fix_json_configs(data, apple=False):
    """Правит формат v2ray-json: список готовых конфигов Xray."""
    is_list = isinstance(data, list)
    cfgs = data if is_list else [data]

    # выбрасывать серверы умеем только когда их список — иначе просто правим
    if apple and is_list:
        kept = [c for c in cfgs
                if not (isinstance(c, dict) and _cfg_is_xhttp(c))]
        if not kept:
            log.warning("Подписка: для Apple не осталось конфигов, отдаю все")
        else:
            cfgs = kept

    for cfg in cfgs:
        if not isinstance(cfg, dict):
            continue
        for ob in cfg.get("outbounds") or []:
            ss = (ob or {}).get("streamSettings") or {}
            if not apple and ss.get("network") == "xhttp":
                xs = ss.get("xhttpSettings") or {}
                xs.pop("scMaxConcurrentPosts", None)
                xs.update(XHTTP_EXTRA)
                ss["xhttpSettings"] = xs
            rs = ss.get("realitySettings") or ss.get("tlsSettings")
            if isinstance(rs, dict) and rs.get("fingerprint"):
                rs["fingerprint"] = FINGERPRINT
    return cfgs if is_list else data


def _patch_body(body, apple=False):
    """Определяет формат подписки и правит его.

    Marzban отдаёт три варианта: JSON-конфиги, base64 от списка ссылок
    или тот же список открытым текстом. Порядок проверок именно такой.
    """
    text = body.decode("utf-8")

    if text.lstrip()[:1] in ("[", "{"):
        try:
            fixed = _fix_json_configs(json.loads(text), apple)
        except (ValueError, TypeError):
            log.warning("Подписка: похоже на JSON, но разобрать не вышло")
            return body
        return json.dumps(fixed, ensure_ascii=False).encode()

    if "vless://" in text:
        return _fix_plain_text(text, apple).encode()

    try:
        raw = base64.b64decode(text + "=" * (-len(text) % 4)).decode("utf-8")
    except (binascii.Error, UnicodeDecodeError, ValueError):
        return body

    if "vless://" not in raw:
        return body
    return base64.b64encode(_fix_plain_text(raw, apple).encode())


@sub.route("/sub/<path:subpath>")
def proxy(subpath):
    """Отдаёт клиенту подписку, забирая её с панели Marzban.

    User-Agent пробрасываем — от него зависит формат (Happ/v2ray/clash).

    ?legacy=1 — вариант для Apple: без XHTTP-серверов, только TCP+REALITY
    (их ядро XHTTP не тянет). Эту ссылку выдаёт сайт, когда видит
    iPhone/iPad/Mac — см. _is_apple в app.py.

    Диагностика: ?nopatch=1 отдаёт конфиг панели БЕЗ наших правок XHTTP.
    Нужно, чтобы отличить «клиент не понимает наши xmux/extra» от
    «проблема на стороне сервера». Наружу не рекламируем.
    """
    # свои параметры наверх не пробрасываем — панель их не ждёт
    args = request.args.to_dict(flat=False)
    nopatch = args.pop("nopatch", None)
    legacy = args.pop("legacy", None)

    upstream = f"{SUB_UPSTREAM}/sub/{subpath}"
    try:
        r = requests.get(
            upstream,
            params=args,
            headers={"User-Agent": request.headers.get("User-Agent", "Happ")},
            timeout=TIMEOUT,
        )
    except Exception:
        log.exception("Подписка: панель недоступна (%s)", upstream)
        abort(502)

    if r.status_code == 404:
        abort(404)

    body = r.content
    if r.status_code == 200 and not nopatch:
        try:
            body = _patch_body(body, apple=bool(legacy))
        except Exception:
            log.exception("Подписка: правка не удалась, отдаю оригинал")
            body = r.content

    # Панель подставляет свой адрес с портом 8000, а он у части провайдеров
    # режется — клиент не смог бы обновить подписку и остался бы без
    # серверов. Заменяем на наш домен, по которому он её и получил.
    site = request.url_root.rstrip("/")
    headers = []
    for k, v in r.headers.items():
        if k.lower() in _HOP:
            continue
        if k.lower() == "profile-web-page-url":
            v = f"{site}/sub/{subpath}"
        headers.append((k, v))

    return Response(body, status=r.status_code, headers=headers)
