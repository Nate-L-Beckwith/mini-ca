#!/usr/bin/env sh
set -euo pipefail

apk add --no-cache curl jq >/dev/null 2>&1

: "${DOMAIN:?need DOMAIN}"
: "${INITIAL_ADMIN_EMAIL:?missing}"
: "${NPM_INITIAL_PASSWORD:?missing}"

# Derive the folder name exactly as issue_cert.py's _short_label does:
# strip a leading "*." then take the leading DNS label.
STRIPPED=${DOMAIN#\*.}
FOLDER=${STRIPPED%%.*}

# Filesystem-safe domain for on-disk filenames: '*.eos.home' -> '_wildcard.eos.home'.
# Only *file paths* use this; the NPM payload below keeps the real "$DOMAIN".
SAFE_DOMAIN=$(printf '%s' "$DOMAIN" | sed 's/^\*\./_wildcard./')

CERT_SRC="/certs/certificates/${FOLDER}/${SAFE_DOMAIN}.crt"
KEY_SRC="/certs/certificates/${FOLDER}/${SAFE_DOMAIN}.key"

[ -f "$CERT_SRC" ] || { echo "❌ cert not found: $CERT_SRC" >&2; exit 1; }
[ -f "$KEY_SRC" ]  || { echo "❌ key not found: $KEY_SRC"  >&2; exit 1; }

DST="/data/custom_ssl/$(printf '%s' "$SAFE_DOMAIN" | tr . _)"
mkdir -p "$DST"
cp "$CERT_SRC" "$DST/fullchain.pem"
cp "$KEY_SRC"  "$DST/privkey.pem"
echo "✅  copied cert to $DST"

BASE=http://127.0.0.1:81

# wait for NPM API to become available
echo "⏳  waiting for NPM API …"
for i in $(seq 1 30); do
  curl -sf "$BASE/api" >/dev/null 2>&1 && break
  sleep 2
done

PAYLOAD=$(jq -n --arg id "$INITIAL_ADMIN_EMAIL" --arg pw "$NPM_INITIAL_PASSWORD" \
  '{identity: $id, secret: $pw}')
TOKEN=$(curl -s --fail -X POST "$BASE/api/tokens" \
          -H "Content-Type: application/json" \
          --data "$PAYLOAD" |
        jq -r .token)

# 1) Create/lookup record
ID=$(curl -s -X POST "$BASE/api/nginx/certificates" \
        -H "Authorization: Bearer $TOKEN" \
        -F provider=other \
        -F nice_name="$DOMAIN" |
      jq -r .id)

# if it already exists (.id == null) find it by name
if [ "$ID" = "null" ] || [ -z "$ID" ]; then
  ID=$(curl -s -H "Authorization: Bearer $TOKEN" \
          "$BASE/api/nginx/certificates" |
        jq -r '.[] | select(.nice_name=="'"$DOMAIN"'") | .id')
fi

[ -z "$ID" ] && { echo "❌ could not obtain certificate ID"; exit 1; }

# 2) Upload PEMs
curl -s -o /tmp/r -w "%{http_code}" -X POST \
     "$BASE/api/nginx/certificates/$ID/upload" \
     -H "Authorization: Bearer $TOKEN" \
     -F certificate=@"$DST/fullchain.pem" \
     -F certificate_key=@"$DST/privkey.pem" > /tmp/status

STATUS=$(cat /tmp/status)
if [ "$STATUS" = "200" ] || [ "$STATUS" = "204" ]; then
  echo "$DOMAIN ✅  registered/updated in NPM (HTTP $STATUS)"
else
  echo "❌ upload failed (HTTP $STATUS)"; cat /tmp/r; exit 1
fi
