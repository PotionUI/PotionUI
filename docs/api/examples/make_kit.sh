#!/usr/bin/env bash
set -euo pipefail
BASE="${POTIONUI_URL:-http://localhost:26730}"

KIT=$(curl -sS --fail-with-body -X POST "$BASE/api/kits" \
  -H "Authorization: Bearer $POTIONUI_KEY" \
  -H "Content-Type: application/json" \
  -d '{"name": "'"${POTIONUI_KIT:-Mara}"'", "label": "Actor"}')
KIT_ID=$(echo "$KIT" | jq -r '.data.kit.id')
KIT_PATH=$(echo "$KIT" | jq -r '.data.kit.path')

curl -sS --fail-with-body -X POST "$BASE/api/kits/$KIT_ID/items" \
  -H "Authorization: Bearer $POTIONUI_KEY" \
  -H "Content-Type: application/json" \
  -d '{"name": "bio", "text": "Mara is a sailor with a red scarf."}' > /dev/null

curl -sS --fail-with-body -X POST "$BASE/api/kits/resolve" \
  -H "Authorization: Bearer $POTIONUI_KEY" \
  -H "Content-Type: application/json" \
  -d '{"text": "A portrait of @'"$KIT_PATH"'.bio at sea"}' \
  | jq -r '.data.expanded'
