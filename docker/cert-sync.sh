#!/usr/bin/env sh
set -euo pipefail

apk add --no-cache curl jq >/dev/null 2>&1

: "${DOMAIN:?need DOMAIN}"
: "${INITIAL_ADMIN_EMAIL:?missing}"
: "${NPM_INITIAL_PASSWORD:?missing}"

SHORT=${DOMAIN%%.*}
DST=/data/custom_ssl/$(echo "$DOMAIN" | tr . _)
mkdir -p "$DST"
cp "/certs/certificates/${SHORT}/${DOMAIN}.crt" "$DST/fullchain.pem"
cp "/certs/certificates/${SHORT}/${DOMAIN}.key" "$DST/privkey.pem"
echo "✅  copied cert to $DST"

BASE=http://127.0.0.1:81
TOKEN=$(curl -s --fail -X POST "$BASE/api/tokens" \
          -H "Content-Type: application/json" \
          --data '{"identity":"'"$INITIAL_ADMIN_EMAIL"'","secret":"'"$NPM_INITIAL_PASSWORD"'"}' |
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
