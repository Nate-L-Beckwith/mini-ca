#!/usr/bin/env sh
# One-shot bootstrap, meant to run as root: fix the ownership of $MINICA_DATA
# (needed for volumes created before 1.1 or bind mounts), then create the root
# CA as the unprivileged service user.
#
#   entry-init.sh [--force]      --force rotates an existing CA (old files are kept as .bak-*)
set -eu

DATA="${MINICA_DATA:-/data}"
RUN_USER="myca"
RUN_UID="1001"

INIT_ARGS=""
for arg in "$@"; do
  case "$arg" in
    --force) INIT_ARGS="$INIT_ARGS --force" ;;
    *)
      echo "[minica-init] unsupported argument: $arg (only --force is accepted)" >&2
      exit 2
      ;;
  esac
done

mkdir -p "$DATA"

if [ "$(id -u)" = "0" ]; then
  echo "[minica-init] setting owner of $DATA to uid $RUN_UID"
  chown -R "$RUN_UID:$RUN_UID" "$DATA"
  # shellcheck disable=SC2086  # INIT_ARGS is intentionally word-split (empty or " --force")
  exec su -s /bin/sh -c "touch '$DATA/DOMAINS' && exec mini_ca.py init$INIT_ARGS" "$RUN_USER"
fi

echo "[minica-init] not running as root — skipping chown of $DATA"
touch "$DATA/DOMAINS"
# shellcheck disable=SC2086
exec mini_ca.py init $INIT_ARGS
