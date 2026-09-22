# syntax=docker/dockerfile:1
# ── stage 1: build wheels ───────────────────────────────────────────────────
ARG PYTHON_VERSION=3.12
FROM python:${PYTHON_VERSION}-slim AS builder
ENV PIP_NO_CACHE_DIR=1 PIP_DISABLE_PIP_VERSION_CHECK=1 PIP_ROOT_USER_ACTION=ignore
WORKDIR /src
COPY requirements.txt .
RUN pip wheel --wheel-dir /wheels -r requirements.txt

# ── stage 2: slim runtime ───────────────────────────────────────────────────
FROM python:${PYTHON_VERSION}-slim
ARG VERSION=dev
ARG VCS_REF=unknown
LABEL org.opencontainers.image.title="mini-ca" \
      org.opencontainers.image.description="Tiny private certificate authority with a domain-list watcher" \
      org.opencontainers.image.source="https://github.com/Nate-L-Beckwith/mini-ca" \
      org.opencontainers.image.licenses="MIT" \
      org.opencontainers.image.version="${VERSION}" \
      org.opencontainers.image.revision="${VCS_REF}"

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_ROOT_USER_ACTION=ignore \
    MINICA_DATA=/data \
    MINICA_VERSION=${VERSION}

# Unprivileged service user. /data is pre-created and owned by it so a fresh
# named volume mounted there inherits the ownership and needs no root init step.
RUN useradd --uid 1001 --user-group --create-home --shell /bin/sh myca \
 && mkdir -p /data && chown myca:myca /data

WORKDIR /opt/myca

# dependencies first (best layer re-use)
COPY --from=builder /wheels /wheels
RUN pip install /wheels/*.whl && rm -rf /wheels

# application code (owned by root, read-only for the service user)
COPY run/*.py ./
COPY docker/entry.sh docker/entry-init.sh /usr/local/bin/
# Normalise line endings (a Windows checkout without .gitattributes yields CRLF) and set modes.
RUN sed -i 's/\r$//' /usr/local/bin/entry.sh /usr/local/bin/entry-init.sh ./*.py \
 && chmod 0755 /usr/local/bin/entry.sh /usr/local/bin/entry-init.sh ./mini_ca.py \
 && ln -s /opt/myca/mini_ca.py /usr/local/bin/mini_ca.py \
 && mini_ca.py --version

USER myca
HEALTHCHECK --interval=60s --timeout=5s --start-period=15s --retries=3 \
    CMD ["sh", "-c", "test -w \"$MINICA_DATA\" && test -f \"$MINICA_DATA/rootCA/rootCA.crt\""]
# entry.sh dispatches: no args / "watch" runs the watcher, a mini_ca.py subcommand runs that command.
ENTRYPOINT ["entry.sh"]
CMD ["watch"]
