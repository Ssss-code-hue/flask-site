import os

from flask import Flask, render_template
from flasgger import Swagger

from api import api as api_blueprint
from bot import db

app = Flask(__name__)
# Секретный ключ берём из переменной окружения (для будущей кассы/сессий).
app.secret_key = os.environ.get('SECRET_KEY', 'change-me-to-a-random-secret')

# REST API + Swagger UI (документация на /apidocs)
db.init_db()                       # на всякий случай создаём таблицы
app.register_blueprint(api_blueprint)
Swagger(app, template={
    "info": {
        "title": "IKK VPN API",
        "description": "API сервиса IKK VPN: тарифы, подписки, рефералы, статистика.",
        "version": "1.0.0",
    },
})


@app.route('/')
def home():
    return render_template('index.html')


@app.route('/tariffs')
def tariffs():
    # Страница выбора тарифа и оплаты (касса будет добавлена позже)
    return render_template('tariffs.html')


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


if __name__ == '__main__':
    # Порт можно задать переменной окружения PORT (по умолчанию 5000)
    port = int(os.environ.get('PORT', 5000))
    app.run(debug=True, port=port)
