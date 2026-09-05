FROM python:3.11-slim

RUN apt-get update && apt-get install -y --no-install-recommends \
    ca-certificates curl git jq tini nodejs npm \
    && npm install -g radius-cli \
    && rm -rf /var/lib/apt/lists/*

RUN pip install --no-cache-dir --upgrade pip setuptools wheel
RUN pip install --no-cache-dir hermes-agent[messaging,cron,cli,pty] fastapi uvicorn pyjwt[crypto] cryptography httpx a2a-sdk web3 requests

WORKDIR /app
COPY plugins /app/plugins
COPY erc8004_registry /app/erc8004_registry

# Inject Bale plugin directly into hermes-agent package path
RUN mkdir -p /usr/local/lib/python3.11/site-packages/hermes_agent/plugins/platforms/ && \
    cp -r /app/plugins/platforms/bale /usr/local/lib/python3.11/site-packages/hermes_agent/plugins/platforms/

ENV PYTHONUNBUFFERED=1 HERMES_HOME=/data/.hermes HOME=/data

ENTRYPOINT ["tini", "--"]
# دستور نهایی و مستقیم بدون واسطه فایل شل
CMD ["sh", "-c", "mkdir -p $HERMES_HOME && python3 -m agent_server.main & hermes gateway"]
