"""Отправка писем с кодом подтверждения.

Два способа (проверяются по порядку):

1. Resend (https://resend.com) — HTTP-API через порт 443, работает даже когда
   хостер блокирует SMTP-порты. Настройка:
       RESEND_API_KEY — API-ключ из панели Resend (re_...)
       MAIL_FROM      — адрес отправителя на подтверждённом домене,
                        например noreply@ikkvpn.com

2. SMTP (по умолчанию — Gmail). Настройка:
       SMTP_USER     — адрес, с которого шлём
       SMTP_PASSWORD — «пароль приложения» (не обычный пароль от почты!)
       SMTP_HOST     — по умолчанию smtp.gmail.com (для Mail.ru: smtp.mail.ru)
       SMTP_PORT     — 465 (SSL) или 587 (STARTTLS)
       MAIL_FROM     — адрес отправителя (по умолчанию = SMTP_USER)

Если ничего не настроено — режим разработки:
письмо не отправляется, код печатается в консоль сервера.
"""
import json
import os
import smtplib
import ssl
import urllib.request
from email.message import EmailMessage

RESEND_API_KEY = (os.environ.get("RESEND_API_KEY") or "").strip()
SMTP_HOST = os.environ.get("SMTP_HOST", "smtp.gmail.com").strip()
SMTP_PORT = int(os.environ.get("SMTP_PORT", "465"))
SMTP_USER = (os.environ.get("SMTP_USER") or "").strip()
SMTP_PASSWORD = (os.environ.get("SMTP_PASSWORD") or "").strip()
MAIL_FROM = (os.environ.get("MAIL_FROM") or SMTP_USER).strip()


def is_configured():
    """True, если почта настроена и письма реально отправляются."""
    return bool(RESEND_API_KEY or (SMTP_USER and SMTP_PASSWORD))


def _code_email_html(code):
    """HTML-версия письма с кодом — в винтажном стиле сайта IKK.

    Все стили инлайновые: почтовые клиенты (Gmail, Mail.ru) вырезают
    внешние CSS-файлы и большинство <style>-блоков.
    """
    return f"""\
<!DOCTYPE html>
<html lang="ru">
<body style="margin:0; padding:0;">
  <table role="presentation" width="100%" cellpadding="0" cellspacing="0"
         style="padding:40px 12px;">
    <tr><td align="center">
      <table role="presentation" cellpadding="0" cellspacing="0"
             style="max-width:520px; width:100%; background-color:#111112;
                    border:1px solid #2e2c28;">

        <!-- Шапка: логотип -->
        <tr><td style="padding:38px 40px 26px; text-align:center;
                       border-bottom:1px solid rgba(236,233,225,0.10);">
          <div style="font-family:Georgia,'Times New Roman',serif; font-size:36px;
                      letter-spacing:8px; color:#ece9e1;">IKK</div>
          <div style="font-family:Georgia,serif; font-size:11px; letter-spacing:4px;
                      color:#8d8a82; text-transform:uppercase; margin-top:8px;">
            VPN-сервис &middot; MMXXVI</div>
        </td></tr>

        <!-- Тело: код -->
        <tr><td style="padding:40px 40px 34px; text-align:center;">
          <div style="font-family:Georgia,serif; font-size:12px; letter-spacing:4px;
                      color:#8d8a82; text-transform:uppercase; margin-bottom:20px;">
            Подтверждение почты</div>
          <p style="font-family:Georgia,serif; font-size:16px; line-height:1.6;
                    color:#ece9e1; margin:0 0 28px;">
            Ваш код для завершения регистрации на&nbsp;сайте:</p>
          <div style="display:inline-block; border:1px solid rgba(236,233,225,0.25);
                      padding:18px 34px 18px 46px; font-family:Georgia,serif;
                      font-size:34px; letter-spacing:12px; color:#ece9e1;">{code}</div>
          <p style="font-family:Georgia,serif; font-size:13px; line-height:1.6;
                    color:#8d8a82; margin:28px 0 0;">
            Код действует 15 минут.<br>
            Если вы не регистрировались на IKK VPN — просто удалите это письмо.</p>
        </td></tr>

        <!-- Подвал -->
        <tr><td style="padding:22px 40px 26px; text-align:center;
                       border-top:1px solid rgba(236,233,225,0.10);">
          <p style="font-family:Georgia,serif; font-size:11px; letter-spacing:3px;
                    text-transform:uppercase; color:#6d6a63; margin:0;">
            &copy; MMXXVI &middot; IKK VPN &middot; приватность прежде всего</p>
        </td></tr>

      </table>
    </td></tr>
  </table>
</body>
</html>"""


def _plain_text(code):
    """Текстовая версия письма — для клиентов без HTML."""
    return (
        f"Ваш код подтверждения: {code}\n\n"
        f"Введите его на сайте, чтобы завершить регистрацию.\n"
        f"Код действует 15 минут.\n\n"
        f"Если вы не регистрировались на IKK VPN — просто удалите это письмо.\n\n"
        f"— IKK VPN · приватность прежде всего"
    )


def _send_via_resend(to_email, code):
    """Отправка через HTTP-API Resend (порт 443 — SMTP-блокировки не мешают)."""
    payload = {
        "from": f"IKK VPN <{MAIL_FROM}>",
        "to": [to_email],
        "subject": f"IKK VPN — код подтверждения: {code}",
        "text": _plain_text(code),
        "html": _code_email_html(code),
    }
    req = urllib.request.Request(
        "https://api.resend.com/emails",
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {RESEND_API_KEY}",
            "Content-Type": "application/json",
            # Без User-Agent запрос отбивает антибот Cloudflare (ошибка 1010)
            "User-Agent": "ikk-vpn-site/1.0 (+https://ikkvpn.com)",
        },
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=15) as resp:
        resp.read()
    return True


def send_code(to_email, code):
    """Отправляет письмо с кодом подтверждения.

    Возвращает True, если письмо ушло (или напечатано в консоли в dev-режиме),
    False — если отправка не удалась.
    """
    if not is_configured():
        # Режим разработки: почта не настроена, показываем код в консоли.
        # Пишем латиницей: консоль Windows может не показывать кириллицу.
        print(f"[MAILER] Mail not configured (RESEND_API_KEY or SMTP_USER/"
              f"SMTP_PASSWORD). Code for {to_email}: {code}", flush=True)
        return True

    if RESEND_API_KEY:
        try:
            return _send_via_resend(to_email, code)
        except Exception as e:
            # Показываем и тело ответа API — там причина (нет домена и т.п.)
            detail = ""
            if hasattr(e, "read"):
                try:
                    detail = " " + e.read().decode("utf-8", "replace")[:300]
                except Exception:
                    pass
            print(f"[MAILER] Resend failed for {to_email}: {e}{detail}", flush=True)
            return False

    msg = EmailMessage()
    msg["Subject"] = f"IKK VPN — код подтверждения: {code}"
    msg["From"] = MAIL_FROM
    msg["To"] = to_email
    # Простой текст — запасной вариант для клиентов без HTML.
    msg.set_content(_plain_text(code))
    msg.add_alternative(_code_email_html(code), subtype="html")

    try:
        context = ssl.create_default_context()
        if SMTP_PORT == 587:
            # Порт 587: сначала обычное соединение, потом шифрование (STARTTLS).
            with smtplib.SMTP(SMTP_HOST, SMTP_PORT, timeout=15) as server:
                server.starttls(context=context)
                server.login(SMTP_USER, SMTP_PASSWORD)
                server.send_message(msg)
        else:
            # Порт 465: шифрование сразу (SSL).
            with smtplib.SMTP_SSL(SMTP_HOST, SMTP_PORT, context=context, timeout=15) as server:
                server.login(SMTP_USER, SMTP_PASSWORD)
                server.send_message(msg)
        return True
    except Exception as e:
        print(f"[MAILER] Failed to send email to {to_email}: {e}", flush=True)
        return False
