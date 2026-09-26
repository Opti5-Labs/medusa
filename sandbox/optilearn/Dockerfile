# Medusa sandbox image for the OptiLearn demo.
# Dependencies are baked in at build time; containers run with the network disabled.
# The code under test is mounted read-only at /code by backend/app/sandbox/runner.py.
FROM python:3.13-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    HF_HUB_OFFLINE=1 \
    PIP_NO_CACHE_DIR=1

RUN pip install \
        "pydantic-settings==2.15.0" \
        "loguru==0.7.3" \
        "aiosqlite==0.22.1" \
        "bcrypt==5.0.0" \
        "huggingface_hub==2.0.0" \
        "pytest==9.1.1" \
        "pytest-asyncio==1.4.0" \
    && useradd --uid 10001 --no-create-home --shell /usr/sbin/nologin sandbox

COPY harness/ /opt/medusa/

USER 10001
WORKDIR /tmp
ENTRYPOINT ["python", "/opt/medusa/run_checks.py"]
