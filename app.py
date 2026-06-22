import os

from flask import Flask, render_template

app = Flask(__name__)
# Секретный ключ берём из переменной окружения (для будущей кассы/сессий).
app.secret_key = os.environ.get('SECRET_KEY', 'change-me-to-a-random-secret')


@app.route('/')
def home():
    return render_template('index.html')


@app.route('/tariffs')
def tariffs():
    # Страница выбора тарифа и оплаты (касса будет добавлена позже)
    return render_template('tariffs.html')


@app.route('/bot')
def bot():
    # Страница со ссылкой на Telegram-бота (добавим позже)
    return render_template('bot.html')


@app.route('/app')
def webapp():
    # Мини-приложение Telegram с инструкцией подключения (Happ)
    return render_template('webapp.html')


if __name__ == '__main__':
    # Порт можно задать переменной окружения PORT (по умолчанию 5000)
    port = int(os.environ.get('PORT', 5000))
    app.run(debug=True, port=port)
