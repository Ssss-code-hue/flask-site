"""Оповещения владельца об ошибках прямо в Telegram.

Без этого сбои жили только в журнале, и узнать о них можно было либо
случайно, либо от пользователя, который уже ушёл. Особенно дорого это
на оплате и выдаче ключа: там каждый сбой — потерянные деньги.

Главная забота модуля — не превратить личку в помойку. Одна и та же
ошибка на сотне пользователей не должна дать сотню сообщений, иначе
уведомления отключат в первый же день, и смысл потеряется.
"""
import hashlib
import logging
import os
import time
import traceback

# Не чаще одного сообщения про ОДНУ и ту же ошибку за этот срок.
# Повторы копятся и уезжают в следующее сообщение числом.
ALERT_COOLDOWN = int(os.environ.get("ALERT_COOLDOWN", "900"))       # 15 мин
# Потолок на сутки: даже разных ошибок не должно быть больше — если их
# столько, дело не в мелочи, и разбираться надо в журнале, а не в личке.
ALERT_DAILY_LIMIT = int(os.environ.get("ALERT_DAILY_LIMIT", "40"))

_last_sent = {}        # отпечаток ошибки → когда отправляли
_repeats = {}          # отпечаток ошибки → сколько повторов накопилось
_day = [0, 0]          # [начало суток, отправлено за сутки]


def _fingerprint(where, exc):
    """Отпечаток ошибки: место + тип + текст. Номера строк не берём —
    от правки кода отпечаток не должен меняться."""
    raw = f"{where}|{type(exc).__name__}|{exc}"
    return hashlib.md5(raw.encode("utf-8", "replace")).hexdigest()[:12]


def _mask(text):
    """Убирает из текста то, что похоже на секреты.

    Traceback может протащить URL с токеном подписки или строку с
    паролем панели, а уведомление уходит в чат, который могут показать
    с экрана. Дешевле замазать заранее, чем жалеть потом.
    """
    for name in ("BOT_TOKEN", "PANEL_PASSWORD", "PLATEGA_SECRET",
                 "LOLZ_TOKEN", "LOLZ_MERCHANT_SECRET", "SECRET_KEY",
                 "ADMIN_PASSWORD", "HY_OBFS_PASSWORD"):
        val = os.environ.get(name)
        if val and len(val) > 6:
            text = text.replace(val, f"<{name}>")
    return text


def _quota_ok():
    """Не вышли ли за суточный потолок. Счётчик сам сбрасывается в сутки."""
    now = time.time()
    if now - _day[0] > 86400:
        _day[0], _day[1] = now, 0
    if _day[1] >= ALERT_DAILY_LIMIT:
        return False
    _day[1] += 1
    return True


async def report(bot, owner_id, where, exc, uid=None):
    """Прислать владельцу короткий отчёт об ошибке.

    where — где случилось, человеческими словами: «оплата звёздами»,
    «выдача ключа». Это первое, что читают, поэтому не имя функции.
    uid — пользователь, у которого сломалось, если известен.
    """
    if not owner_id:
        return
    fp = _fingerprint(where, exc)
    now = time.time()
    last = _last_sent.get(fp, 0)

    if now - last < ALERT_COOLDOWN:
        _repeats[fp] = _repeats.get(fp, 0) + 1     # копим, отправим позже
        return
    if not _quota_ok():
        _repeats[fp] = _repeats.get(fp, 0) + 1
        return

    repeats = _repeats.pop(fp, 0)
    _last_sent[fp] = now

    tb = "".join(traceback.format_exception_only(type(exc), exc)).strip()
    line = ""
    tb_full = traceback.extract_tb(exc.__traceback__)
    if tb_full:
        f = tb_full[-1]
        line = f"\n<code>{os.path.basename(f.filename)}:{f.lineno}</code>"

    text = (f"⚠️ <b>Сбой: {where}</b>\n"
            f"<code>{_mask(tb)[:300]}</code>{line}")
    if uid:
        text += f"\nу пользователя <code>{uid}</code>"
    if repeats:
        text += f"\n\n<i>+{repeats} таких же за последние "
        text += f"{ALERT_COOLDOWN // 60} мин.</i>"

    try:
        await bot.send_message(owner_id, text)
    except Exception:
        # Молча: падать из-за неудачного уведомления об ошибке — это уже
        # смешно, а в журнале запись всё равно останется.
        logging.exception("Не смог отправить оповещение об ошибке")
