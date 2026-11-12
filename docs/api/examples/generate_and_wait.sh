#!/usr/bin/env bash
set -euo pipefail
BASE="${POTIONUI_URL:-http://localhost:26730}"
FORM="${POTIONUI_FORM:-}"
[ -n "$FORM" ] || FORM='{}'

STARTED=$(curl -sS --fail-with-body -X POST "$BASE/api/generations/start" \
  -H "Authorization: Bearer $POTIONUI_KEY" \
  -H "Content-Type: application/json" \
  -d '{"preset_id": "'"$POTIONUI_PRESET"'", "mode": "txt2img", "prompt": "a lighthouse at dusk, oil painting", "form_data": '"$FORM"'}')
ID=$(echo "$STARTED" | jq -r '.data.generation_id')

while true; do
  STATUS=$(curl -sS --fail-with-body "$BASE/api/generations/$ID/status" \
    -H "Authorization: Bearer $POTIONUI_KEY" | jq -r '.data.status')
  case "$STATUS" in completed|failed|cancelled) break ;; esac
  sleep 1
done
[ "$STATUS" = "completed" ] || { echo "generation $STATUS"; exit 1; }

curl -sS --fail-with-body "$BASE/api/generations/history/$ID" \
  -H "Authorization: Bearer $POTIONUI_KEY" \
  | jq -r '.data.files[] | "\(.id) \(.mime_type)"' \
  | while read -r FILE_ID MIME; do
      NAME="$ID-$FILE_ID.${MIME#*/}"
      curl -sS --fail-with-body "$BASE/api/media/files/$FILE_ID" \
        -H "Authorization: Bearer $POTIONUI_KEY" -o "$NAME"
      echo "saved $NAME"
    done
