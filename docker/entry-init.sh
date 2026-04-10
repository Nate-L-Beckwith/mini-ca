#!/usr/bin/env sh
set -eu

echo "[minica‑init] ensuring perms on /data"
chown -R 1001:1001 /data || true

# build the command string safely — expected arg is only --force
INIT_CMD="touch /data/DOMAINS && mini_ca.py init"
for arg in "$@"; do
  INIT_CMD="$INIT_CMD $arg"
done

su -s /bin/sh -c "$INIT_CMD" myca
