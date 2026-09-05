#!/usr/bin/env bash
set -euo pipefail

export HERMES_HOME="${HERMES_HOME:-/data/.hermes}"
export HOME="${HOME:-/data}"
export MESSAGING_CWD="${MESSAGING_CWD:-/data/workspace}"

mkdir -p "${HERMES_HOME}" "${HERMES_HOME}/logs" "${HERMES_HOME}/sessions" "${HERMES_HOME}/cron" "${HERMES_HOME}/pairing" "${MESSAGING_CWD}"
mkdir -p "${HOME}/.claude"

# 1. Platform & Provider validation
if [[ -z "${BALE_BOT_TOKEN:-}" ]]; then
  echo "[bootstrap] ERROR: BALE_BOT_TOKEN missing." >&2
  exit 1
fi

# 2. Config management
if [[ ! -f "${HERMES_HOME}/config.yaml" ]]; then
  cat > "${HERMES_HOME}/config.yaml" <<EOF
model: ${LLM_MODEL:-openai/gpt-4o-mini}
EOF
fi

# 3. Hermes API Key
HERMES_API_KEY_FILE="${HERMES_HOME}/.hermes_api_key"
if [[ -z "${HERMES_API_KEY:-}" && -f "$HERMES_API_KEY_FILE" ]]; then
  export HERMES_API_KEY="$(cat "$HERMES_API_KEY_FILE")"
fi
if [[ -z "${HERMES_API_KEY:-}" ]]; then
  export HERMES_API_KEY="$(python3 -c 'import secrets; print(secrets.token_hex(32))')"
  echo "$HERMES_API_KEY" > "$HERMES_API_KEY_FILE"
fi

# 4. Start Server
echo "[bootstrap] Starting Hermes Bale Gateway..."
python3 -m agent_server.main &
AGENT_PID=$!

echo "[bootstrap] Starting Gateway..."
hermes gateway &
GATEWAY_PID=$!

trap 'kill $AGENT_PID $GATEWAY_PID; exit' EXIT INT TERM
wait $AGENT_PID $GATEWAY_PID