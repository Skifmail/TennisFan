FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

WORKDIR /app

# Системные зависимости.
# На части VPS (в т.ч. Dokploy) deb.debian.org/CDN недоступен из build-контейнера,
# поэтому ставим зеркало. Переопределение: --build-arg DEBIAN_MIRROR=...
ARG DEBIAN_MIRROR=https://mirror.yandex.ru/debian
ARG DEBIAN_SECURITY_MIRROR=https://mirror.yandex.ru/debian-security
RUN set -eux; \
    sed -i \
      -e "s|https\\?://deb.debian.org/debian-security|${DEBIAN_SECURITY_MIRROR}|g" \
      -e "s|https\\?://deb.debian.org/debian|${DEBIAN_MIRROR}|g" \
      /etc/apt/sources.list.d/debian.sources; \
    apt-get update; \
    apt-get install -y --no-install-recommends \
        libpq-dev \
        gcc \
        cron; \
    rm -rf /var/lib/apt/lists/*

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