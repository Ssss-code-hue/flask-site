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
import base64
import os
from urllib.parse import quote

HY_HOST = os.environ.get("HY_HOST", "").strip()
HY_PORT = os.environ.get("HY_PORT", "443").strip()
HY_OBFS_PASSWORD = os.environ.get("HY_OBFS_PASSWORD", "").strip()
HY_SNI = os.environ.get("HY_SNI", HY_HOST).strip()
# Отпечаток (SHA-256) самоподписанного сертификата сервера. Если задан —
# клиент доверяет серверу по отпечатку (pinSHA256), а не по цепочке CA:
# убирает «ошибку TLS рукопожатия» и не зависит от продления Let's Encrypt.
HY_PIN = os.environ.get("HY_PIN", "").strip()


def is_configured():
    return bool(HY_HOST and HY_OBFS_PASSWORD)


def link_for(token):
    """Ссылка-ключ Hysteria2 для Happ. token уходит в поле auth."""
    pin = f"&pinSHA256={quote(HY_PIN, safe='')}" if HY_PIN else ""
    return (
        f"hysteria2://{quote(token, safe='')}@{HY_HOST}:{HY_PORT}/"
        f"?obfs=salamander&obfs-password={quote(HY_OBFS_PASSWORD, safe='')}"
        f"&sni={quote(HY_SNI, safe='')}{pin}#IKK%20VPN"
    )


def happ_link(config):
    """Deep link «Открыть в Happ»: сам ключ кодируем в base64, иначе
    символы ?/#/& обрезают конфиг и Happ пишет «не удалось разобрать»."""
    return "happ://add/" + base64.b64encode(config.encode()).decode()
