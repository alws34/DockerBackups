# python:3.12-alpine (multi-arch index digest). Dependabot keeps this pin current.
FROM python:3.12-alpine@sha256:1b668429b3511ab407d8e00648891631b0b1a4d7e15e3ca70f38ab5b91ad4ab4

# Bitwarden CLI, version pinned in bw/package.json and every transitive dependency locked
# (with integrity hashes) in bw/package-lock.json. nodejs runs it; npm is only needed to
# install it and is removed afterwards.
COPY bw/package.json bw/package-lock.json /opt/bw/
RUN apk add --no-cache nodejs tzdata ca-certificates \
    && apk add --no-cache --virtual .build-deps npm \
    && npm ci --prefix /opt/bw --omit=dev --ignore-scripts --no-audit --no-fund \
    && ln -s /opt/bw/node_modules/.bin/bw /usr/local/bin/bw \
    && npm cache clean --force \
    && apk del .build-deps \
    && rm -rf /root/.npm

WORKDIR /srv

# Hash-checked install from the pinned lock file. Every dependency ships a musllinux
# wheel, so no compiler is needed.
COPY requirements.txt /srv/requirements.txt
RUN pip install --no-cache-dir --only-binary :all: --require-hashes -r /srv/requirements.txt

COPY app/ /srv/app/
# COPY keeps the build machine's permissions; a strict umask (or a synced folder) would
# leave the code unreadable for the unprivileged user below.
RUN chmod -R a+rX,go-w /srv/app
COPY --chmod=755 entrypoint.sh /entrypoint.sh

# Unprivileged runtime user. The code stays root-owned, so the app cannot modify itself.
# HOME must be writable: the Bitwarden CLI keeps its config under ~/.config.
RUN addgroup -g 1000 takeout \
    && adduser -D -u 1000 -G takeout -h /home/takeout takeout

ENV PYTHONPATH=/srv \
    PYTHONDONTWRITEBYTECODE=1 \
    HOME=/home/takeout

USER 1000:1000

EXPOSE 8080

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD ["python", "-c", "import os, urllib.request; urllib.request.urlopen(f\"http://127.0.0.1:{os.environ.get('WEB_PORT', '8080')}/\", timeout=4)"]

ENTRYPOINT ["/entrypoint.sh"]
CMD ["python", "/srv/app/main.py"]
