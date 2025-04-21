# multi‑stage build – slim & reproducible
FROM python:3.12-slim AS builder
WORKDIR /src

COPY requirements.txt .
RUN pip install --upgrade pip \
 && pip wheel         --no-cache-dir cryptography        -w /wheels \
 && pip wheel -r      requirements.txt --no-cache-dir    -w /wheels

COPY run/                 ./run
COPY docker/entry*.sh     .
# ────────────────────────────────────────────────────────────────────────────

FROM python:3.12-slim
RUN useradd -u 1001 -m myca
WORKDIR /opt/myca

# wheels first (fast layer‑reuse)
COPY --from=builder /wheels /wheels
RUN pip install --no-cache-dir /wheels/*

# application code
COPY --from=builder /src/run/*.py           ./
COPY --from=builder /src/entry.sh           /usr/local/bin/entry.sh
COPY --from=builder /src/entry-init.sh      /usr/local/bin/entry-init.sh
RUN chmod +x /usr/local/bin/entry*.sh \
 && ln -s /opt/myca/mini_ca.py /usr/local/bin/mini_ca.py \
 && chmod +x /opt/myca/mini_ca.py \
 && chown -R myca:myca /opt/myca
USER 1001
ENTRYPOINT ["entry.sh"]
