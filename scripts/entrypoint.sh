#!/usr/bin/env bash
#
# Hermes Bale Gateway — bootstrap entrypoint.
#
# Validates that BALE_BOT_TOKEN is set, prepares the persistent Hermes home
# directory, generates an API key if missing, and starts:
#   * the Hermes agent server (FastAPI on PORT, default 3000)
#   * the Hermes gateway (long-polls the Bale Bot API via the bundled Bale plugin)
#
set -euo pipefail

export HERMES_HOME="${HERMES_HOME:-/data/.hermes}"
export HOME="${HOME:-/data}"
export MESSAGING_CWD="${MESSAGING_CWD:-/data/workspace}"

INIT_MARKER="${HERMES_HOME}/.initialized"
ENV_FILE="${HERMES_HOME}/.env"
CONFIG_FILE="${HERMES_HOME}/config.yaml"

mkdir -p "${HERMES_HOME}" "${HERMES_HOME}/logs" "${HERMES_HOME}/sessions" "${HERMES_HOME}/cron" "${HERMES_HOME}/pairing" "${MESSAGING_CWD}"
mkdir -p "${HOME}/.claude"

# DEBUG
echo "=== Debugging file structure ==="
ls -R /app | head -n 50
# END DEBUG

# ----------------------------------------------------------------------------
# 1. Provider check
# ----------------------------------------------------------------------------
has_valid_provider_config() {
  if [[ -n "${OPENROUTER_API_KEY:-}" ]]; then return 0; fi
  if [[ -n "${OPENAI_BASE_URL:-}" && -n "${OPENAI_API_KEY:-}" ]]; then return 0; fi
  if [[ -n "${ANTHROPIC_API_KEY:-}" ]]; then return 0; fi
  return 1
}

if ! has_valid_provider_config; then
  echo "[bootstrap] ERROR: Configure a provider: OPENROUTER_API_KEY, or OPENAI_BASE_URL+OPENAI_API_KEY, or ANTHROPIC_API_KEY." >&2
  exit 1
fi

# ----------------------------------------------------------------------------
# 2. Bale platform check (replaces the original Telegram check)
# ----------------------------------------------------------------------------
validate_bale() {
  if [[ -z "${BALE_BOT_TOKEN:-}" ]]; then
    echo "[bootstrap] ERROR: BALE_BOT_TOKEN is not set." >&2
    echo "[bootstrap] Get a token from https://bale.ai/BotFather and set it in Railway Variables." >&2
    exit 1
  fi
}

validate_bale

# ----------------------------------------------------------------------------
# 3. Hermes API key (auto-generate on first boot, persist to /data)
# ----------------------------------------------------------------------------
HERMES_API_KEY_FILE="${HERMES_HOME}/.hermes_api_key"
if [[ -z "${HERMES_API_KEY:-}" && -n "${API_SERVER_KEY:-}" ]]; then
  export HERMES_API_KEY="${API_SERVER_KEY}"
  echo "[bootstrap] Using API_SERVER_KEY as HERMES_API_KEY for the A2A direct bridge."
fi
if [[ -z "${HERMES_API_KEY:-}" && -f "$HERMES_API_KEY_FILE" ]]; then
  export HERMES_API_KEY="$(cat "$HERMES_API_KEY_FILE")"
  echo "[bootstrap] Loaded stored Hermes API key."
fi
if [[ -z "${HERMES_API_KEY:-}" ]]; then
  export HERMES_API_KEY="$(python3 -c 'import secrets; print(secrets.token_hex(32))')"
  echo "$HERMES_API_KEY" > "$HERMES_API_KEY_FILE"
  chmod 600 "$HERMES_API_KEY_FILE"
  echo "[bootstrap] Generated new Hermes API key."
fi

# ----------------------------------------------------------------------------
# 4. Persist a normalized env file so child processes can re-read it
# ----------------------------------------------------------------------------
echo "[bootstrap] Writing runtime env to ${ENV_FILE}"
{
  echo "# Managed by entrypoint.sh"
  echo "HERMES_HOME=${HERMES_HOME}"
  echo "MESSAGING_CWD=${MESSAGING_CWD}"
} > "$ENV_FILE"

append_if_set() {
  local key="$1"
  local val="${!key:-}"
  if [[ -n "$val" ]]; then
    printf '%s=%s\n' "$key" "$val" >> "$ENV_FILE"
  fi
}

# Pass-through list — note the BALE_* names instead of TELEGRAM_*.
for key in \
  OPENROUTER_API_KEY OPENAI_API_KEY OPENAI_BASE_URL ANTHROPIC_API_KEY LLM_MODEL HERMES_INFERENCE_PROVIDER HERMES_PORTAL_BASE_URL NOUS_INFERENCE_BASE_URL HERMES_NOUS_MIN_KEY_TTL_SECONDS HERMES_DUMP_REQUESTS \
  BALE_BOT_TOKEN BALE_ALLOWED_USERS BALE_ALLOW_ALL_USERS BALE_HOME_CHANNEL BALE_HOME_CHANNEL_NAME BALE_API_BASE_URL BALE_POLLING_TIMEOUT BALE_POLLING_INTERVAL BALE_WEBHOOK_URL BALE_WEBHOOK_SECRET \
  GATEWAY_ALLOW_ALL_USERS \
  FIRECRAWL_API_KEY NOUS_API_KEY BROWSERBASE_API_KEY BROWSERBASE_PROJECT_ID BROWSERBASE_PROXIES BROWSERBASE_ADVANCED_STEALTH BROWSER_SESSION_TIMEOUT BROWSER_INACTIVITY_TIMEOUT FAL_KEY ELEVENLABS_API_KEY VOICE_TOOLS_OPENAI_KEY \
  TINKER_API_KEY WANDB_API_KEY RL_API_URL GITHUB_TOKEN BYTEROVER_API_KEY BYTEROVER_LOCAL LINEAR_API_KEY LINEAR_TEAM_ID LINEAR_PROJECT_ID \
  TERMINAL_BACKEND TERMINAL_DOCKER_IMAGE TERMINAL_SINGULARITY_IMAGE TERMINAL_MODAL_IMAGE TERMINAL_CWD TERMINAL_TIMEOUT TERMINAL_LIFETIME_SECONDS TERMINAL_CONTAINER_CPU TERMINAL_CONTAINER_MEMORY TERMINAL_CONTAINER_DISK TERMINAL_CONTAINER_PERSISTENT TERMINAL_SANDBOX_DIR TERMINAL_SSH_HOST TERMINAL_SSH_USER TERMINAL_SSH_PORT TERMINAL_SSH_KEY SUDO_PASSWORD \
  WEB_TOOLS_DEBUG VISION_TOOLS_DEBUG MOA_TOOLS_DEBUG IMAGE_TOOLS_DEBUG CONTEXT_COMPRESSION_ENABLED CONTEXT_COMPRESSION_THRESHOLD CONTEXT_COMPRESSION_MODEL HERMES_MAX_ITERATIONS HERMES_TOOL_PROGRESS HERMES_TOOL_PROGRESS_MODE \
  RADIUS_HOME RADIUS_WALLET_ADDRESS RADIUS_NETWORK RADIUS_RPC_URL RADIUS_SBC_ADDRESS RADIUS_CHAIN_ID RADIUS_EXPLORER_URL RADIUS_CLI_BIN RADIUS_AUTO_FUND \
  ERC8004_NETWORK ERC8004_TESTNET_RPC_URL ERC8004_TESTNET_REGISTRY ERC8004_TESTNET_EXPLORER_URL ERC8004_TESTNET_CHAIN_ID ERC8004_MAINNET_RPC_URL ERC8004_MAINNET_REGISTRY ERC8004_MAINNET_EXPLORER_URL ERC8004_MAINNET_CHAIN_ID ERC8004_GAS_LIMIT ERC8004_MAX_AGENT_URI_BYTES \
  AGENT_NAME AGENT_DESCRIPTION AGENT_IMAGE AGENT_ACTIVE AGENT_X402_SUPPORT AGENT_SUPPORTED_TRUST AGENT_A2A_VERSION AGENT_ERC8004_ID AGENT_ERC8004_REGISTRY AGENT_ANS_NAME AGENT_ANS_AGENT_ID AGENT_ANS_HOST AGENT_ANS_STATUS AGENT_WALLET AGENT_EMAIL AGENT_ENS \
  WEBHOOK_PORT WEBHOOK_SECRET DEBUG_SKILLS \
  EXPECTED_VENDORED_SKILLS STRICT_VENDORED_SKILLS VENDORED_SKILLS_SOURCE \
  RADIUS_SKILLS_AUTO_UPDATE RADIUS_SKILLS_REPO RADIUS_SKILLS_BRANCH RADIUS_SKILLS_WEBHOOK_SECRET RADIUS_SKILLS_GITHUB_TOKEN RADIUS_SKILLS_DIR RADIUS_SKILLS_SYNC_TIMEOUT_SECONDS RADIUS_SKILLS_BOOTSTRAP_FROM_IMAGE \
  HERMES_API_KEY HERMES_URL A2A_BRIDGE_MODEL HERMES_TIMEOUT A2A_MODE A2A_PUBLIC_URL A2A_FILE_SERVE_PATHS
do
  append_if_set "$key"
done

# ----------------------------------------------------------------------------
# 5. First-boot Hermes config.yaml (agent runtime settings)
# ----------------------------------------------------------------------------
if [[ ! -f "$CONFIG_FILE" ]]; then
  echo "[bootstrap] Creating ${CONFIG_FILE}"
  cat > "$CONFIG_FILE" <<EOF
model: ${LLM_MODEL:-openai/gpt-5.4-nano}
terminal:
  backend: ${TERMINAL_BACKEND:-local}
  cwd: ${TERMINAL_CWD:-/data/workspace}
  timeout: ${TERMINAL_TIMEOUT:-180}
compression:
  enabled: true
  threshold: 0.85
EOF
fi

if [[ ! -f "$INIT_MARKER" ]]; then
  date -u +"%Y-%m-%dT%H:%M:%SZ" > "$INIT_MARKER"
  echo "[bootstrap] First-time initialization completed."
else
  echo "[bootstrap] Existing Hermes data found. Skipping one-time init."
fi

# ----------------------------------------------------------------------------
# 6. Allowlist warning (default-deny is the safe default)
# ----------------------------------------------------------------------------
if [[ -z "${BALE_ALLOWED_USERS:-}" ]]; then
  if ! is_true "${GATEWAY_ALLOW_ALL_USERS:-}" && ! is_true "${BALE_ALLOW_ALL_USERS:-}"; then
    echo "[bootstrap] WARNING: No BALE_ALLOWED_USERS configured. Defaults to deny-all." >&2
    echo "[bootstrap]          Set BALE_ALLOWED_USERS=<your-id> or BALE_ALLOW_ALL_USERS=true." >&2
  fi
fi

is_true() {
  case "${1:-}" in
    1|true|TRUE|yes|YES|on|ON) return 0 ;;
    *) return 1 ;;
  esac
}

# ----------------------------------------------------------------------------
# 7. Unset empty integer env vars that would crash Hermes with int() errors
# ----------------------------------------------------------------------------
for key in \
  HERMES_MAX_ITERATIONS HERMES_NOUS_MIN_KEY_TTL_SECONDS \
  CONTEXT_COMPRESSION_THRESHOLD \
  TERMINAL_TIMEOUT TERMINAL_LIFETIME_SECONDS \
  TERMINAL_CONTAINER_CPU TERMINAL_CONTAINER_MEMORY TERMINAL_CONTAINER_DISK \
  TERMINAL_SSH_PORT BROWSER_SESSION_TIMEOUT BROWSER_INACTIVITY_TIMEOUT \
  BALE_POLLING_TIMEOUT BALE_POLLING_INTERVAL
do
  val="${!key:-}"
  if [[ -z "${val//[[:space:]]/}" ]]; then
    unset "$key" 2>/dev/null || true
  fi
done

# ----------------------------------------------------------------------------
# 8. Symlink the bundled Bale plugin into HERMES_HOME so the gateway picks it up
# ----------------------------------------------------------------------------
HERMES_PLUGINS_DIR="${HERMES_HOME}/plugins"
mkdir -p "$HERMES_PLUGINS_DIR"
if [[ -d /app/plugins/platforms/bale ]] && [[ ! -e "${HERMES_PLUGINS_DIR}/platforms/bale" ]]; then
  mkdir -p "${HERMES_PLUGINS_DIR}/platforms"
  ln -sfn /app/plugins/platforms/bale "${HERMES_PLUGINS_DIR}/platforms/bale"
  echo "[bootstrap] Linked Bale platform plugin from /app/plugins/platforms/bale."
fi

# ----------------------------------------------------------------------------
# 9. Start agent server (FastAPI on $PORT) and Hermes gateway
# ----------------------------------------------------------------------------
echo "[bootstrap] Starting agent server..."
python3 /app/scripts/agent_server/main.py &
AGENT_PID=$!

_wait_for_agent_server() {
  local port="${PORT:-3000}"
  local max_attempts=15
  local attempt=0

  echo "[bootstrap] Waiting for agent server on port ${port}..."
  while [[ $attempt -lt $max_attempts ]]; do
    attempt=$((attempt + 1))
    if ! kill -0 "$AGENT_PID" 2>/dev/null; then
      echo "[bootstrap] WARNING: Agent server process (PID ${AGENT_PID}) exited unexpectedly." >&2
      return 1
    fi
    if curl -sf -o /dev/null "http://127.0.0.1:${port}/health"; then
      echo "[bootstrap] Agent server ready."
      return 0
    fi
    sleep 2
  done
  echo "[bootstrap] WARNING: Agent server did not become ready in time — continuing anyway." >&2
  return 1
}

_wait_for_agent_server || true

echo "[bootstrap] Starting Hermes gateway (Bale adapter)…"
hermes gateway &
GATEWAY_PID=$!

trap 'cleanup $?' EXIT INT TERM

cleanup() {
  local exit_code="${1:-0}"
  echo "[bootstrap] Shutting down (exit=${exit_code})…"
  kill "$AGENT_PID" "$GATEWAY_PID" 2>/dev/null || true
  wait 2>/dev/null || true
  exit "$exit_code"
}

if ! wait -n "$AGENT_PID" "$GATEWAY_PID"; then
  status=$?
else
  status=0
fi

if ! kill -0 "$AGENT_PID" 2>/dev/null; then
  echo "[bootstrap] ERROR: Agent server exited." >&2
  status=1
fi
if ! kill -0 "$GATEWAY_PID" 2>/dev/null; then
  echo "[bootstrap] ERROR: Hermes gateway exited." >&2
  status=1
fi

cleanup "$status"