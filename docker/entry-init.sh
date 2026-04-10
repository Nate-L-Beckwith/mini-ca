#!/usr/bin/env sh
set -eu

echo "[minica‑init] ensuring perms on /data"
chown -R 1001:1001 /data || true

# only allow the supported --force flag; reject anything else
FORCE_ARG=""
for arg in "$@"; do
  case "$arg" in
    --force)
      FORCE_ARG=" --force"
      ;;
    *)
      echo "[minica-init] unsupported argument: $arg" >&2
      exit 1
      ;;
  esac
done

su -s /bin/sh -c "touch /data/DOMAINS && mini_ca.py init$FORCE_ARG" myca
