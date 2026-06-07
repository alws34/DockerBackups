FROM python:3.12-alpine

RUN apk add --no-cache \
    nodejs \
    npm \
    tzdata \
    ca-certificates \
    gcc \
    musl-dev \
    libffi-dev \
    && npm install -g @bitwarden/cli \
    && npm cache clean --force

WORKDIR /srv

COPY requirements.txt /srv/requirements.txt
RUN pip install --no-cache-dir -r /srv/requirements.txt

COPY app/ /srv/app/

ENV PYTHONPATH=/srv

EXPOSE 8080

CMD ["python", "/srv/app/main.py"]
