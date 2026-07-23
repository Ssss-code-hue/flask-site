"""Интеграция с VPN-панелью **Marzban**.

Бот вызывает get_subscription_url(user_id, sub_until):
  - создаёт пользователя в Marzban (или продлевает существующего) на срок expire=sub_until;
  - возвращает его subscription-URL, который пользователь добавляет в v2RayTun.

Настройка через переменные окружения:
  PANEL_URL        — адрес панели, напр. https://panel.example.com
  PANEL_USERNAME   — логин администратора Marzban (sudo-admin)
  PANEL_PASSWORD   — пароль администратора
  PANEL_PROXIES    — JSON протоколов, по умолчанию {"vless": {"flow": "xtls-rprx-vision"}}
  PANEL_INBOUNDS   — JSON инбаундов, напр. {"vless": ["VLESS TCP REALITY"]}.
                     Если не задано — берём ВСЕ инбаунды панели по нашим протоколам
                     (без инбаундов Marzban создаёт пользователя с пустой подпиской).
  PANEL_VERIFY_SSL — "0" чтобы НЕ проверять SSL (по умолчанию проверяем)
  USERNAME_PREFIX  — префикс имени пользователя в панели (по умолчанию ikk_)

Проверить связь с панелью:  python -m bot.panel
"""
import json
import logging
import os

import requests

log = logging.getLogger(__name__)

PANEL_URL = os.environ.get("PANEL_URL", "").rstrip("/")
PANEL_USERNAME = os.environ.get("PANEL_USERNAME", "")
PANEL_PASSWORD = os.environ.get("PANEL_PASSWORD", "")
VERIFY = os.environ.get("PANEL_VERIFY_SSL", "1") != "0"
USERNAME_PREFIX = os.environ.get("USERNAME_PREFIX", "ikk_")
TIMEOUT = 15

# Месячный лимит трафика на подписку (ГБ). Щедрый для одного человека, но
# компания из нескольких устройств упрётся — так сдерживаем шаринг ключа.
# 0 = безлимит. Сброс раз в месяц (data_limit_reset_strategy=month).
DATA_LIMIT_GB = int(os.environ.get("DATA_LIMIT_GB", "200"))
DATA_LIMIT_BYTES = DATA_LIMIT_GB * 1024 ** 3
DATA_LIMIT_RESET = "month" if DATA_LIMIT_GB else "no_reset"

_DEFAULT_PROXIES = '{"vless": {"flow": "xtls-rprx-vision"}}'
try:
    PROXIES = json.loads(os.environ.get("PANEL_PROXIES", _DEFAULT_PROXIES))
except json.JSONDecodeError:
    PROXIES = json.loads(_DEFAULT_PROXIES)

try:
    _inb = os.environ.get("PANEL_INBOUNDS", "")
    INBOUNDS = json.loads(_inb) if _inb else None
except json.JSONDecodeError:
    INBOUNDS = None


def _configured():
    return bool(PANEL_URL and PANEL_USERNAME and PANEL_PASSWORD)


# Пользователи сайта и Telegram-бота нумеруются независимо, поэтому у них
# пересекаются id. Без разных префиксов они затирали бы подписки друг друга
# в панели, оказавшись одним и тем же пользователем Marzban.
WEB_PREFIX = os.environ.get("WEB_USERNAME_PREFIX", "web_")


def _username(user_id, prefix=None):
    return f"{prefix or USERNAME_PREFIX}{user_id}"


def sub_token(url):
    """Токен из subscription-URL панели: .../sub/<токен> → <токен>."""
    if not url:
        return None
    marker = "/sub/"
    i = url.find(marker)
    return url[i + len(marker):].strip("/") if i != -1 else None


def site_sub_url(url, site_url):
    """Переписывает ссылку с панели (порт 8000) на наш домен.

    Сайт отдаёт подписку по 443 и попутно правит XHTTP-параметры
    (см. sub.py) — клиенту нужна именно эта версия, а не панельная.
    """
    token = sub_token(url)
    return f"{site_url.rstrip('/')}/sub/{token}" if token else None


def _login():
    r = requests.post(
        f"{PANEL_URL}/api/admin/token",
        data={"username": PANEL_USERNAME, "password": PANEL_PASSWORD},
        timeout=TIMEOUT, verify=VERIFY,
    )
    r.raise_for_status()
    return r.json()["access_token"]


def _headers(token):
    return {"Authorization": f"Bearer {token}"}


def _full_sub(url):
    if not url:
        return None
    return PANEL_URL + url if url.startswith("/") else url


_auto_inbounds = None


def _inbounds(token):
    """Инбаунды для пользователя: из env или все доступные в панели (кэш на процесс)."""
    global _auto_inbounds
    if INBOUNDS:
        return INBOUNDS
    if _auto_inbounds is None:
        r = requests.get(
            f"{PANEL_URL}/api/inbounds",
            headers=_headers(token), timeout=TIMEOUT, verify=VERIFY,
        )
        r.raise_for_status()
        _auto_inbounds = {
            proto: [i["tag"] for i in items]
            for proto, items in r.json().items()
            if proto in PROXIES and items
        }
    return _auto_inbounds


def get_subscription_url(user_id, sub_until, prefix=None):
    """Создаёт/продлевает пользователя в Marzban и возвращает subscription-URL.

    prefix разделяет пользователей сайта и бота (см. WEB_PREFIX).
    """
    # Демо-режим, если панель не настроена (бот не упадёт)
    if not _configured():
        base = os.environ.get("SUB_BASE_URL", "").rstrip("/")
        return f"{base}/{user_id}" if base else None

    try:
        token = _login()
        username = _username(user_id, prefix)

        # есть ли уже такой пользователь?
        r = requests.get(
            f"{PANEL_URL}/api/user/{username}",
            headers=_headers(token), timeout=TIMEOUT, verify=VERIFY,
        )

        if r.status_code == 404:
            # создаём нового (inbounds обязательны: без них подписка будет пустой)
            body = {
                "username": username,
                "proxies": PROXIES,
                "inbounds": _inbounds(token),
                "expire": int(sub_until),
                "data_limit": DATA_LIMIT_BYTES,
                "data_limit_reset_strategy": DATA_LIMIT_RESET,
                "status": "active",
            }
            r = requests.post(
                f"{PANEL_URL}/api/user",
                headers=_headers(token), json=body, timeout=TIMEOUT, verify=VERIFY,
            )
        else:
            r.raise_for_status()
            # продлеваем существующего; inbounds и лимит шлём тоже —
            # это чинит пользователей, созданных ранее без инбаундов/лимита
            r = requests.put(
                f"{PANEL_URL}/api/user/{username}",
                headers=_headers(token),
                json={
                    "expire": int(sub_until),
                    "status": "active",
                    "inbounds": _inbounds(token),
                    "data_limit": DATA_LIMIT_BYTES,
                    "data_limit_reset_strategy": DATA_LIMIT_RESET,
                },
                timeout=TIMEOUT, verify=VERIFY,
            )

        r.raise_for_status()
        return _full_sub(r.json().get("subscription_url"))
    except Exception:
        log.exception("Ошибка обращения к панели Marzban")
        return None


def user_connected(user_id, prefix=None):
    """Подключался ли пользователь к VPN хоть раз.

    True  — был онлайн или прошёл трафик;
    False — ни разу не подключался;
    None  — не удалось проверить (панель недоступна) — вызывающий пропустит.
    """
    if not _configured():
        return None
    try:
        r = requests.get(
            f"{PANEL_URL}/api/user/{_username(user_id, prefix)}",
            headers=_headers(_login()), timeout=TIMEOUT, verify=VERIFY,
        )
        if r.status_code == 404:
            return False
        r.raise_for_status()
        u = r.json()
        return bool(u.get("online_at")) or (u.get("used_traffic") or 0) > 0
    except Exception:
        log.exception("panel: не удалось проверить подключение пользователя")
        return None


def delete_panel_user(user_id, prefix=None):
    """Удаляет пользователя из Marzban — иначе его ключ работает и после
    удаления аккаунта. Возвращает True, если пользователя больше нет."""
    if not _configured():
        return False
    try:
        r = requests.delete(
            f"{PANEL_URL}/api/user/{_username(user_id, prefix)}",
            headers=_headers(_login()), timeout=TIMEOUT, verify=VERIFY,
        )
        return r.ok or r.status_code == 404   # 404 — его и так нет
    except Exception:
        log.exception("Не удалось удалить пользователя в панели Marzban")
        return False


def check():
    """Диагностика подключения к панели. Запуск: python -m bot.panel"""
    if not _configured():
        print("[ВНИМАНИЕ] Панель НЕ настроена. Задайте PANEL_URL, PANEL_USERNAME, PANEL_PASSWORD.")
        print("           Сейчас бот работает в демо-режиме (ключи не выдаются автоматически).")
        return
    print(f"Панель: {PANEL_URL}")
    try:
        token = _login()
        print("[OK] Авторизация успешна — API доступен.")
        r = requests.get(f"{PANEL_URL}/api/inbounds", headers=_headers(token), timeout=TIMEOUT, verify=VERIFY)
        if r.ok:
            tags = [i.get("tag") for proto in r.json().values() for i in proto]
            print("Доступные инбаунды (можно указать в PANEL_INBOUNDS):", tags)
    except Exception as e:
        print("[ОШИБКА] Не удалось подключиться к панели:", e)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    check()
