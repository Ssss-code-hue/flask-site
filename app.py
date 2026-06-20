import os

from flask import Flask, render_template, request, redirect, url_for, flash

app = Flask(__name__)
# Секретный ключ нужен, чтобы работали flash-сообщения ("спасибо за отзыв").
# Для реального проекта замени на свой случайный набор символов.
app.secret_key = 'change-me-to-a-random-secret'


@app.route('/')
def home():
    return render_template('index.html')


@app.route('/contact', methods=['POST'])
def contact():
    # Забираем данные из формы
    name = request.form.get('name')
    email = request.form.get('email')
    message = request.form.get('message')

    # Пока просто выводим в консоль (потом можно сохранять в файл или БД)
    print(f'Новое сообщение от {name} ({email}): {message}')

    # Показываем пользователю сообщение об успехе и возвращаем на главную
    flash(f'Спасибо, {name}! Твоё сообщение получено. ✅')
    return redirect(url_for('home') + '#contact')


if __name__ == '__main__':
    # Порт можно задать переменной окружения PORT (по умолчанию 5000)
    port = int(os.environ.get('PORT', 5000))
    app.run(debug=True, port=port)
