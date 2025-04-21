#!/usr/bin/env sh
set -eu
chown -R 1001:1001 /data || true

echo "[minica] watching /data/DOMAINS …"
exec mini_ca.py watch --file /data/DOMAINS
