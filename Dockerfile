FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        build-essential \
        gcc \
        libpq-dev \
        libpq5 \
        ca-certificates \
        curl \
        libjpeg62-turbo \
        libjpeg-dev \
        zlib1g \
        zlib1g-dev \
        libstdc++6 \
        fonts-noto-cjk \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt /app/requirements.txt
RUN pip install --upgrade pip \
    && pip install -r /app/requirements.txt

COPY . /app

EXPOSE 8000

CMD ["sh", "-c", "gunicorn codemaster_system.wsgi:application --bind 0.0.0.0:8000 --workers 2 --threads 2 --timeout ${GUNICORN_TIMEOUT:-120}"]
