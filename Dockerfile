FROM python:3.11-slim

RUN apt-get update && apt-get install -y --no-install-recommends \
    ca-certificates curl git jq tini nodejs npm \
    && npm install -g radius-cli \
    && rm -rf /var/lib/apt/lists/*

# Install Hermes Agent core
RUN pip install --no-cache-dir hermes-agent[messaging,cron,cli,pty]

WORKDIR /app
COPY scripts /app/scripts
COPY plugins /app/plugins
COPY erc8004_registry /app/erc8004_registry

# Inject Bale plugin directly into hermes-agent package path
RUN mkdir -p /usr/local/lib/python3.11/site-packages/hermes_agent/plugins/platforms/ && \
    cp -r /app/plugins/platforms/bale /usr/local/lib/python3.11/site-packages/hermes_agent/plugins/platforms/

RUN sed -i 's/\r$//' /app/scripts/bootstrap.sh && chmod +x /app/scripts/bootstrap.sh

ENV PYTHONUNBUFFERED=1 \
  HERMES_HOME=/data/.hermes \
  HOME=/data

ENTRYPOINT ["tini", "--"]
CMD ["/app/scripts/bootstrap.sh"]