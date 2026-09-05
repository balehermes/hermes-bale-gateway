#!/usr/bin/env bash
# Hermes Bale Gateway — Railway deployment helper
#
# This script is provided for local convenience; Railway uses the Dockerfile
# directly.  Run it locally to:
#   1. Validate that BALE_BOT_TOKEN is present
#   2. Optionally push the container to a registry of your choice
#
# Usage:
#   ./deploy.sh             # validate only
#   ./deploy.sh --push      # validate, build, and push (requires REGISTRY env)
set -euo pipefail

if [[ -z "${BALE_BOT_TOKEN:-}" ]]; then
  echo "[deploy] ERROR: BALE_BOT_TOKEN is not set." >&2
  echo "[deploy] Get one from https://bale.ai/BotFather then re-run." >&2
  exit 1
fi

if [[ -z "${OPENROUTER_API_KEY:-}${ANTHROPIC_API_KEY:-}${OPENAI_API_KEY:-}" ]]; then
  echo "[deploy] ERROR: configure an LLM provider (OPENROUTER_API_KEY, ANTHROPIC_API_KEY, or OPENAI_API_KEY)." >&2
  exit 1
fi

echo "[deploy] OK — required variables present."

if [[ "${1:-}" == "--push" ]]; then
  if [[ -z "${REGISTRY:-}" ]]; then
    echo "[deploy] ERROR: --push requires REGISTRY env var (e.g. ghcr.io/youruser/hermes-bale-gateway)." >&2
    exit 1
  fi
  echo "[deploy] Building and pushing to ${REGISTRY}…"
  docker build -t "${REGISTRY}:latest" .
  docker push "${REGISTRY}:latest"
  echo "[deploy] Done."
fi