FROM python:3.12-alpine

# Pin CLI tool version for reproducible builds
ARG BW_CLI_VERSION=2026.5.0

RUN apk add --no-cache \
    nodejs \
    npm \
    tzdata \
    ca-certificates \
    gcc \
    musl-dev \
    libffi-dev \
    && npm install -g @bitwarden/cli@${BW_CLI_VERSION} \
    && npm cache clean --force

WORKDIR /srv

# Use pinned lock file for reproducible Python dependency installs
COPY requirements.txt /srv/requirements.txt
RUN pip install --no-cache-dir -r /srv/requirements.txt

COPY app/ /srv/app/
COPY entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

ENV PYTHONPATH=/srv

EXPOSE 8080

ENTRYPOINT ["/entrypoint.sh"]
CMD ["python", "/srv/app/main.py"]
