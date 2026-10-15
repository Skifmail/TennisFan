FROM python:3.12.15-slim-trixie

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
        cron \
        libpango-1.0-0 \
        libpangoft2-1.0-0 \
        libpangocairo-1.0-0 \
        libharfbuzz0b \
        libharfbuzz-subset0 \
        fonts-dejavu-core; \
    rm -rf /var/lib/apt/lists/*

COPY requirements.txt requirements.lock ./
RUN pip install --no-cache-dir --root-user-action=ignore --require-hashes -r requirements.lock

# Удаляем gcc после установки зависимостей
RUN apt-get purge -y gcc && apt-get autoremove -y

COPY . .

COPY entrypoint.sh /entrypoint.sh
COPY cron-entrypoint.sh /cron-entrypoint.sh
COPY cron-run-wrapper.py /app/cron-run-wrapper.py
RUN chmod +x /entrypoint.sh /cron-entrypoint.sh /app/cron-run-wrapper.py

# Статика создаётся один раз на образ, без боевых ключей и подключения к БД.
RUN python scripts/build_css_bundle.py \
    && SECRET_KEY=build-only-not-used-at-runtime DEBUG=False USE_POSTGRES=False \
       USE_S3=False USE_REDIS=False TELEGRAM_ENABLED=False \
       python manage.py collectstatic --noinput \
    && useradd --uid 10001 --create-home app \
    && chown -R app:app /app/logs /app/staticfiles

EXPOSE 8000

ENTRYPOINT ["/entrypoint.sh"]
