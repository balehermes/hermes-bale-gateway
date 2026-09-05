FROM python:3.11-slim

RUN apt-get update \
  && DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends \
    ca-certificates \
    curl \
    git \
    jq \
    tini \
    nodejs \
    npm \
  && npm install -g radius-cli \
  && rm -rf /var/lib/apt/lists/*

# Install Hermes Agent and deps
RUN pip install --no-cache-dir --upgrade pip setuptools wheel
RUN pip install --no-cache-dir \
    hermes-agent[messaging,cron,cli,pty] \
    fastapi uvicorn pyjwt[crypto] cryptography httpx a2a-sdk web3 requests

WORKDIR /app
COPY scripts /app/scripts
COPY plugins /app/plugins
COPY erc8004_registry /app/erc8004_registry

RUN sed -i 's/\r$//' /app/scripts/entrypoint.sh && chmod +x /app/scripts/entrypoint.sh

ENV PYTHONUNBUFFERED=1 \
  HERMES_HOME=/data/.hermes \
  HOME=/data

ENTRYPOINT ["tini", "--"]
CMD ["/app/scripts/entrypoint.sh"]