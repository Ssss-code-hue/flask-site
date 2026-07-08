import os

from flask import Flask, render_template, session
from flasgger import Swagger

import platega
from api import api as api_blueprint
from auth import TRIAL_DAYS, auth as auth_blueprint
from bot import db
from pay import pay as pay_blueprint

app = Flask(__name__)
# Секретный ключ берём из переменной окружения (для будущей кассы/сессий).
app.secret_key = os.environ.get('SECRET_KEY', 'change-me-to-a-random-secret')

# REST API + Swagger UI (документация на /apidocs)
db.init_db()                       # на всякий случай создаём таблицы
app.register_blueprint(api_blueprint)
app.register_blueprint(auth_blueprint)   # регистрация и вход по почте
app.register_blueprint(pay_blueprint)    # оплата через Platega (СБП/карта)
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


@app.route('/')
def home():
    return render_template('index.html')


@app.route('/tariffs')
def tariffs():
    # Страница выбора тарифа; кнопки оплаты активны, когда настроена Platega
    return render_template('tariffs.html', trial_days=TRIAL_DAYS,
                           platega_on=platega.is_configured())


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
