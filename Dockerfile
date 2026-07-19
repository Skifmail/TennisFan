FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

WORKDIR /app

# Системные зависимости
RUN apt-get update && apt-get install -y --no-install-recommends \
    libpq-dev \
    gcc \
    cron \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir --root-user-action=ignore -r requirements.txt

# Удаляем gcc после установки зависимостей
RUN apt-get purge -y gcc && apt-get autoremove -y

COPY . .

COPY entrypoint.sh /entrypoint.sh
COPY cron-entrypoint.sh /cron-entrypoint.sh
COPY cron-run-wrapper.py /app/cron-run-wrapper.py
RUN chmod +x /entrypoint.sh /cron-entrypoint.sh /app/cron-run-wrapper.py

EXPOSE 8000

ENTRYPOINT ["/entrypoint.sh"]