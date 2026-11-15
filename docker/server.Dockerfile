# The whole server on one machine: the brain and the parts that can live
# without a microphone or a screen — the keeper, the native hands, the
# sources. The page is the brain's, so the interface ships in the same image.
#
#   docker build -f docker/server.Dockerfile -t heare-server .
#   docker run -p 127.0.0.1:8095:9790 -v heare:/home/heare/.heare heare-server
#
# The image is the artifact: Coolify pulls it, and a new release is a new
# tag, not a script run inside a running container.

FROM python:3.12-slim

RUN useradd --create-home --uid 1000 heare
# Coolify's own healthcheck asks with curl (or wget); the slim image has
# neither, and a container it cannot check reads as unhealthy forever.
RUN apt-get update \
    && apt-get install -y --no-install-recommends curl \
    && rm -rf /var/lib/apt/lists/*
RUN pip install --no-cache-dir supervisor

WORKDIR /src
COPY packages/heare-core packages/heare-core
COPY packages/heare-reach packages/heare-reach
COPY packages/heare-memory packages/heare-memory
COPY packages/heare-brain packages/heare-brain
COPY packages/heare-keeper packages/heare-keeper
COPY packages/heare-native-hands packages/heare-native-hands
COPY packages/heare-sources packages/heare-sources

RUN pip install --no-cache-dir \
    ./packages/heare-core \
    ./packages/heare-reach \
    ./packages/heare-memory \
    ./packages/heare-brain \
    ./packages/heare-keeper \
    ./packages/heare-native-hands \
    ./packages/heare-sources

COPY docker/supervisord.conf /etc/supervisord.conf
COPY docker/entrypoint.sh /entrypoint.sh
RUN chmod 755 /entrypoint.sh && chmod 644 /etc/supervisord.conf

USER heare
WORKDIR /home/heare
# The keyring is a desktop's; a server has none, and asking for one hangs.
ENV PYTHON_KEYRING_BACKEND=keyring.backends.null.Keyring \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    HEARE_NO_BROWSER=1 \
    HEARE_NO_SELF_UPDATE=1
RUN mkdir -p /home/heare/.heare
VOLUME /home/heare/.heare
EXPOSE 9790

# The brain answers /health without a key: a container that cannot is not up.
HEALTHCHECK --interval=15s --timeout=5s --start-period=20s --retries=5 \
    CMD python -c "import sys, urllib.request; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:9790/health', timeout=3).status == 200 else 1)"

ENTRYPOINT ["/entrypoint.sh"]
