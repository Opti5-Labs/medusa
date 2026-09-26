# Medusa runner image for general Python repositories (ARBITRARY_EXECUTION).
# Runs under gVisor (runsc). The repo is mounted read-only at /code, its
# dependencies are installed into a capped RAM area and persisted to a volume
# at /deps for the test phase, and all other writes go to tmpfs.
# See backend/app/sandbox/pyexec.py for how the two phases are launched.
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

RUN apt-get update \
    && apt-get install -y --no-install-recommends git \
    && rm -rf /var/lib/apt/lists/* \
    && pip install "pip==26.2.1" "pytest==9.1.1" "pytest-timeout==2.4.0" \
    && useradd --uid 10001 --no-create-home --shell /usr/sbin/nologin runner \
    && mkdir /deps && chown 10001:10001 /deps

COPY harness/ /opt/medusa/

USER 10001
WORKDIR /work
ENTRYPOINT ["python", "/opt/medusa/run.py"]
