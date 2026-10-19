# The brain alone, for a server: the parts live on the machines that have a
# microphone and a screen, and knock on this one.
#
#   docker compose -f docker/compose.yaml up -d --build
#   docker compose -f docker/compose.yaml exec brain heare-brain code
#
# The second line prints a one-time code: open http://<server>:9790 and type
# it in. State — the token, memory, settings, kept keys — is the volume.

FROM python:3.12-slim AS wheels
WORKDIR /src
COPY packages/heare-core packages/heare-core
COPY packages/heare-reach packages/heare-reach
COPY packages/heare-brain packages/heare-brain
RUN pip wheel --no-cache-dir --no-deps -w /wheels \
    packages/heare-core packages/heare-reach packages/heare-brain

FROM python:3.12-slim
RUN useradd --create-home --uid 1000 heare
COPY --from=wheels /wheels /tmp/wheels
RUN pip install --no-cache-dir /tmp/wheels/*.whl && rm -rf /tmp/wheels
USER heare
WORKDIR /home/heare
# Made here so the volume mounts over a folder heare owns, not root's.
RUN mkdir -p /home/heare/.heare
VOLUME /home/heare/.heare
EXPOSE 9790
# Inside a container 127.0.0.1 is the container itself: the published port
# could never reach it. 0.0.0.0 opens nothing unguarded — every /api/ route
# still asks for the master token or a key paired with a one-time code.
# Keys come from the environment (DEEPSEEK_API_KEY, GROQ_API_KEY …); a key
# given on the page is kept in the volume, in keys.json, mode 0600.
CMD ["sh", "-c", "python -c 'from heare.config import Settings; Settings().set(\"api_host\", \"0.0.0.0\")' && exec heare-brain"]
