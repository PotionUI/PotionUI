#!/usr/bin/env bash
set -euo pipefail
BASE="${POTIONUI_URL:-http://localhost:26730}"

curl -sS --fail-with-body "$BASE/api/presets" \
  -H "Authorization: Bearer $POTIONUI_KEY" \
  | jq -r '.data[] | "\(.id)  \(.name)  \(.modes | join(","))"'
