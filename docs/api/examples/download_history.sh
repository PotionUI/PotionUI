#!/usr/bin/env bash
set -euo pipefail
BASE="${POTIONUI_URL:-http://localhost:26730}"
mkdir -p history

curl -sS --fail-with-body "$BASE/api/generations/history?status=completed&limit=20&offset=0" \
  -H "Authorization: Bearer $POTIONUI_KEY" \
  | jq -r '.data.generations[] | .id as $generation | .files[] | "\($generation) \(.id) \(.mime_type)"' \
  | while read -r GENERATION FILE_ID MIME; do
      NAME="history/$GENERATION-$FILE_ID.${MIME#*/}"
      [ -e "$NAME" ] && continue
      curl -sS --fail-with-body "$BASE/api/media/files/$FILE_ID" \
        -H "Authorization: Bearer $POTIONUI_KEY" -o "$NAME"
      echo "saved $NAME"
    done
