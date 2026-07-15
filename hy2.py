"""HTTP-авторизация Hysteria2.

Сервер Hysteria2 на каждое подключение шлёт сюда POST с полем auth
(токен ключа). Отвечаем {ok, id}: ok=true, если у токена активная
подписка — тогда пользователь подключается; иначе доступа нет.

Hysteria2 ходит на этот эндпоинт напрямую по IP origin (в обход
Cloudflare — иначе антибот заблокирует server-to-server запрос).
"""
import logging

from flask import Blueprint, jsonify, request

from bot import db

log = logging.getLogger(__name__)
hy2 = Blueprint("hy2", __name__)


@hy2.route("/api/hy2/auth", methods=["POST"])
def auth():
    data = request.get_json(silent=True) or {}
    token = str(data.get("auth") or "")
    uid = db.hy_authorize(token)
    if uid:
        return jsonify(ok=True, id=uid)
    log.info("Hysteria2 auth отклонён (addr=%s)", data.get("addr"))
    return jsonify(ok=False, id="")
