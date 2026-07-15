"""Hysteria2 — рабочий транспорт для РФ (обходит DPI, работает в Happ).

Ключ у каждого пользователя — свой токен в поле auth ссылки. Сервер
Hysteria2 на каждое подключение спрашивает разрешение у сайта
(HTTP-auth → /api/hy2/auth), а сайт проверяет, активна ли подписка
у этого токена. Так просроченные ключи автоматически перестают работать.

Настройка через переменные окружения:
  HY_HOST          — адрес/домен сервера (совпадает с сертификатом)
  HY_PORT          — UDP-порт (по умолчанию 443)
  HY_OBFS_PASSWORD — пароль обфускации Salamander (общий, из конфига сервера)
  HY_SNI           — SNI для TLS (обычно = HY_HOST)
"""
import os
from urllib.parse import quote

HY_HOST = os.environ.get("HY_HOST", "").strip()
HY_PORT = os.environ.get("HY_PORT", "443").strip()
HY_OBFS_PASSWORD = os.environ.get("HY_OBFS_PASSWORD", "").strip()
HY_SNI = os.environ.get("HY_SNI", HY_HOST).strip()


def is_configured():
    return bool(HY_HOST and HY_OBFS_PASSWORD)


def link_for(token):
    """Ссылка-ключ Hysteria2 для Happ. token уходит в поле auth."""
    return (
        f"hysteria2://{quote(token, safe='')}@{HY_HOST}:{HY_PORT}/"
        f"?obfs=salamander&obfs-password={quote(HY_OBFS_PASSWORD, safe='')}"
        f"&sni={quote(HY_SNI, safe='')}#IKK%20VPN"
    )
