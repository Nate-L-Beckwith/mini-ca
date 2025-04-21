#!/usr/bin/env sh
set -eu

echo "[minica‑init] ensuring perms on /data"
chown -R 1001:1001 /data || true

# now drop privileges and create /data/DOMAINS if it’s missing
su -s /bin/sh -c "touch /data/DOMAINS && mini_ca.py init $*" myca
