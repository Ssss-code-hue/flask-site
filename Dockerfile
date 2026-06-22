# Боевой образ для VPN-сайта IKK
FROM python:3.13-slim

# .pyc не пишем, логи сразу в stdout (видно в docker logs)
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

# Сначала зависимости — отдельный кэшируемый слой
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Затем код приложения
COPY . .

# Порт приложения
EXPOSE 5000

# Боевой WSGI-сервер gunicorn (НЕ flask dev server).
# app:app — объект Flask с именем app в файле app.py.
# Порт берём из переменной PORT (по умолчанию 5000) — удобно для хостингов.
CMD ["sh", "-c", "gunicorn --bind 0.0.0.0:${PORT:-5000} --workers 2 app:app"]
