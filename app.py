import os
from datetime import timedelta

from flask import Flask, redirect, render_template, session
from flasgger import Swagger

import hysteria

from api import api as api_blueprint
from auth import TRIAL_DAYS, auth as auth_blueprint
from bot import db
from pay import pay as pay_blueprint
from pay import available_providers
from sub import sub as sub_blueprint
from hy2 import hy2 as hy2_blueprint
from admin import admin as admin_blueprint

app = Flask(__name__)
# Секретный ключ берём из переменной окружения (для сессий/кассы).
app.secret_key = os.environ.get('SECRET_KEY', 'change-me-to-a-random-secret')

# Cookie сессии должен переживать возврат с платёжного шлюза (Platega) —
# это переход с чужого домена, при SameSite=Lax браузер cookie не шлёт и
# пользователя «выкидывает» на вход. На боевом HTTPS ставим SameSite=None
# (+ обязателен Secure); локально (HTTP) — Lax, иначе Secure-cookie не
# отправится и вход сломается при разработке.
_https = os.environ.get('SITE_URL', '').startswith('https')
app.config.update(
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SECURE=_https,
    SESSION_COOKIE_SAMESITE='None' if _https else 'Lax',
    PERMANENT_SESSION_LIFETIME=timedelta(days=30),
)

# REST API + Swagger UI (документация на /apidocs)
db.init_db()                       # на всякий случай создаём таблицы
app.register_blueprint(api_blueprint)
app.register_blueprint(auth_blueprint)   # регистрация и вход по почте
app.register_blueprint(pay_blueprint)    # оплата (Platega/Lolz)
app.register_blueprint(sub_blueprint)    # прокси подписок VPN (чистый 443)
app.register_blueprint(hy2_blueprint)    # HTTP-авторизация ключей Hysteria2
app.register_blueprint(admin_blueprint)  # админ-панель управления подписками


@app.template_filter('ts')
def _fmt_ts(value):
    """Unix-время → ДД.ММ.ГГГГ для шаблонов админки."""
    import time as _t
    return _t.strftime('%d.%m.%Y', _t.localtime(value)) if value else '—'
Swagger(app, template={
    "info": {
        "title": "IKK VPN API",
        "description": "API сервиса IKK VPN: тарифы, подписки, рефералы, статистика.",
        "version": "1.0.0",
    },
})


@app.context_processor
def inject_nav_user():
    # Пользователь для шапки (аватар возле «Аккаунт»); None, если не вошёл
    uid = session.get('uid')
    return {'nav_user': db.get_web_user(uid) if uid else None}


@app.route('/happ/<token>')
def happ_open(token):
    """Открыть ключ в Happ одним нажатием. Telegram не пускает схему
    happ:// в ссылках, поэтому даём https-адрес, который редиректит
    в happ://add/<base64> — браузер запускает Happ."""
    return redirect(hysteria.happ_link(hysteria.link_for(token)), code=302)


@app.route('/')
def home():
    return render_template('index.html')


@app.route('/tariffs')
def tariffs():
    # Страница выбора тарифа; providers — список настроенных касс, из которых
    # пользователь выбирает, где платить (Platega / Lolzteam)
    return render_template('tariffs.html', trial_days=TRIAL_DAYS,
                           providers=available_providers())


@app.route('/advantages')
def advantages():
    # Отдельная страница преимуществ
    return render_template('advantages.html')


@app.route('/bot')
def bot():
    # Страница со ссылкой на Telegram-бота (добавим позже)
    return render_template('bot.html')


@app.route('/app')
def webapp():
    # Мини-приложение Telegram с инструкцией подключения (Happ)
    return render_template('webapp.html')


@app.route('/offer')
def offer():
    # Публичная оферта на подписку
    return render_template('offer.html')


@app.route('/offer-app')
def offer_app():
    # Оферта в виде мини-приложения Telegram (для бота)
    return render_template('offer_app.html')


@app.route('/terms')
def terms():
    # Пользовательское соглашение (галочка при регистрации)
    return render_template('terms.html')


@app.route('/privacy')
def privacy():
    # Политика конфиденциальности (галочка при регистрации)
    return render_template('privacy.html')


@app.route('/terms-app')
def terms_app():
    # Соглашение в виде мини-приложения Telegram (для бота)
    return render_template('terms_app.html')


@app.route('/privacy-app')
def privacy_app():
    # Политика конфиденциальности в виде мини-приложения Telegram (для бота)
    return render_template('privacy_app.html')


if __name__ == '__main__':
    # Порт можно задать переменной окружения PORT (по умолчанию 5000)
    port = int(os.environ.get('PORT', 5000))
    app.run(debug=True, port=port)
