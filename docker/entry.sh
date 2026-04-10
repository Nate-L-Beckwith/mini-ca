#!/usr/bin/env sh
set -eu

test -w /data || { echo "[minica] ERROR: /data not writable — run 'make init' first"; exit 1; }

echo "[minica] watching /data/DOMAINS …"
exec mini_ca.py watch --file /data/DOMAINS
