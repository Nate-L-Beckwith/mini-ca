#!/usr/bin/env sh
# Image entrypoint.
#
#   (no args) / watch [...]   make sure a root CA exists, then follow $MINICA_DATA/DOMAINS
#   init|issue|renew|list|info|verify|add|npm-sync [...]
#                             run that mini_ca.py command   (docker run --rm -v minica-data:/data IMAGE list)
#   --help | --version        forwarded to mini_ca.py
#   anything else             executed as-is                (docker run --rm -it IMAGE sh)
set -eu

DATA="${MINICA_DATA:-/data}"

[ "$#" -eq 0 ] && set -- watch
case "$1" in -h) set -- --help ;; -V) set -- --version ;; esac

case "$1" in
  --help|--version)
    exec mini_ca.py "$@"
    ;;
  watch)
    if [ ! -d "$DATA" ] || [ ! -w "$DATA" ]; then
      echo "[minica] ERROR: $DATA is not writable by uid $(id -u)." >&2
      echo "[minica]        Mount a volume at $DATA; for a volume created before 1.1 run 'make init' once" >&2
      echo "[minica]        (docker compose --profile setup run --rm init) to fix its ownership." >&2
      exit 1
    fi
    if [ ! -f "$DATA/rootCA/rootCA.key" ] || [ ! -f "$DATA/rootCA/rootCA.crt" ]; then
      if [ "${MINICA_AUTO_INIT:-1}" = "1" ]; then
        echo "[minica] no root CA in $DATA/rootCA — creating one (MINICA_AUTO_INIT=0 disables this)"
        mini_ca.py init
      else
        echo "[minica] ERROR: no root CA in $DATA/rootCA — run 'make init' first." >&2
        exit 1
      fi
    fi
    echo "[minica] mini-ca ${MINICA_VERSION:-dev} — following $DATA/DOMAINS"
    exec mini_ca.py "$@"
    ;;
  init|issue|renew|list|info|verify|add|npm-sync)
    exec mini_ca.py "$@"
    ;;
  *)
    exec "$@"
    ;;
esac
