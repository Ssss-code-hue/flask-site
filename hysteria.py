"""Hysteria2 — рабочий транспорт для РФ (обходит DPI, работает в Happ).

Ключ у каждого пользователя — свой токен в поле auth ссылки. Сервер
Hysteria2 на каждое подключение спрашивает разрешение у сайта
(HTTP-auth → /api/hy2/auth), а сайт проверяет, активна ли подписка
у этого токена. Так просроченные ключи автоматически перестают работать.

Настройка через переменные окружения:
  HY_HOST          — адрес/домен сервера (совпадает с сертификатом)
  HY_PORT          — UDP-порт (по умолчанию 443)
  HY_PORTS         — диапазон портов для перескока, напр. "20000-50000"
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
# Перескок портов (port hopping) — против глушения UDP-потока DPI: провайдер
# пропускает первые пакеты и глушит поток, а клиент, меняя порт каждые
# ~30 секунд, каждый раз начинает «свежий» поток. Диапазон передаём
# параметром mport (в адресе остаётся надёжный 443: клиент без поддержки
# mport просто продолжит работать по 443, ссылка не ломается);
# на сервере диапазон завёрнут iptables-редиректом на реальный порт 443.
HY_PORTS = os.environ.get("HY_PORTS", "").strip()


def is_configured():
    return bool(HY_HOST and HY_OBFS_PASSWORD)


def link_for(token):
    """Ссылка-ключ Hysteria2 для Happ. token уходит в поле auth."""
    pin = f"&pinSHA256={quote(HY_PIN, safe='')}" if HY_PIN else ""
    hop = f"&mport={quote(HY_PORTS, safe='')}&mportHopInt=30" if HY_PORTS else ""
    return (
        f"hysteria2://{quote(token, safe='')}@{HY_HOST}:{HY_PORT}/"
        f"?obfs=salamander&obfs-password={quote(HY_OBFS_PASSWORD, safe='')}"
        f"&sni={quote(HY_SNI, safe='')}{pin}{hop}#IKK%20VPN"
    )


def happ_link(config):
    """Deep link «Открыть в Happ»: сам ключ кодируем в base64, иначе
    символы ?/#/& обрезают конфиг и Happ пишет «не удалось разобрать»."""
    return "happ://add/" + base64.b64encode(config.encode()).decode()
