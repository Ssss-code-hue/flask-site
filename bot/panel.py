"""Интеграция с VPN-панелью (Happ работает по subscription-ссылке).

ВАЖНО: рабочие VPN-ключи выдаёт ваш сервер/панель (3x-ui, Marzban, Remnawave и т.п.).
Здесь нужно реализовать создание/продление пользователя в панели и возврат его
subscription-URL, который пользователь добавит в Happ.

Сейчас работает демо-режим:
  - если задан SUB_BASE_URL — возвращаем ссылку вида {SUB_BASE_URL}/{user_id};
  - иначе возвращаем None (бот скажет, что ключ пришлёт администратор).
"""
import os


def get_subscription_url(user_id, sub_until):
    base = os.environ.get("SUB_BASE_URL", "").rstrip("/")
    if base:
        # Демо: предполагаем, что панель отдаёт подписку по такому пути.
        return f"{base}/{user_id}"

    # TODO: подключить реальную панель, например (псевдокод для Marzban):
    #   import requests
    #   token = login_to_panel()
    #   requests.put(f"{PANEL}/api/user/{user_id}", json={"expire": sub_until, ...})
    #   return f"{PANEL}/sub/{subscription_token}"
    return None
