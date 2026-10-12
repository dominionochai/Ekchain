# syntax=docker/dockerfile:1

# MITRE ATT&CK Enterprise + ICS STIX data (data-only; pin to build platform to avoid QEMU)
FROM --platform=$BUILDPLATFORM ubuntu:22.04 AS attack-fetch

ENV DEBIAN_FRONTEND=noninteractive

RUN apt-get update && apt-get install -y --no-install-recommends ca-certificates curl \
    && rm -rf /var/lib/apt/lists/*

RUN mkdir -p /opt/attack \
    && curl -fsSL https://raw.githubusercontent.com/mitre-attack/attack-stix-data/master/enterprise-attack/enterprise-attack.json \
        -o /opt/attack/enterprise-attack.json \
    && curl -fsSL https://raw.githubusercontent.com/mitre-attack/attack-stix-data/master/ics-attack/ics-attack.json \
        -o /opt/attack/ics-attack.json

# Sigma rules
ARG SIGMA_VERSION=r2024-09-02
FROM --platform=$BUILDPLATFORM ubuntu:22.04 AS sigma-fetch

ENV DEBIAN_FRONTEND=noninteractive

RUN apt-get update && apt-get install -y --no-install-recommends ca-certificates curl \
    && rm -rf /var/lib/apt/lists/*

RUN curl -fsSL "https://github.com/SigmaHQ/sigma/archive/refs/tags/${SIGMA_VERSION}.tar.gz" \
        -o /tmp/sigma.tar.gz \
    && mkdir -p /opt/sigma-rules \
    && tar xzf /tmp/sigma.tar.gz -C /opt/sigma-rules --strip-components=1 \
    && rm /tmp/sigma.tar.gz

# Runtime image
FROM ubuntu:22.04 AS runtime

ENV DEBIAN_FRONTEND=noninteractive \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    DISABLE_AUTOUPDATE=1

COPY --from=ghcr.io/astral-sh/uv:0.12.7 /uv /usr/local/bin/uv

RUN apt-get update && apt-get install -y --no-install-recommends \
        software-properties-common \
        gnupg \
        ca-certificates \
        curl \
        git \
        gosu \
    && add-apt-repository -y ppa:deadsnakes/ppa \
    && apt-get update \
    && apt-get install -y --no-install-recommends \
        python3.12 \
        python3.12-venv \
    && update-alternatives --install /usr/bin/python3 python3 /usr/bin/python3.12 1 \
    && update-alternatives --install /usr/bin/python python /usr/bin/python3.12 1 \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/*

# Set up user
RUN useradd -m -s /bin/bash ekchain \
    && install -d -o ekchain -g ekchain /home/ekchain/.cache

COPY --from=attack-fetch /opt/attack/enterprise-attack.json /opt/attack/enterprise-attack.json
COPY --from=attack-fetch /opt/attack/ics-attack.json /opt/attack/ics-attack.json
COPY --from=sigma-fetch /opt/sigma-rules/ /opt/sigma-rules/

COPY . /app
RUN uv pip install --system --no-cache -e /app

# LiteLLM proxy in isolated venv (hard dependency conflicts with ekchain's
# mcp>=2.0 and rich>=15.0; litellm pins older versions of both).
RUN python3 -m venv /opt/litellm \
    && find /opt/litellm -type d -name __pycache__ -prune -exec rm -rf {} + \
    && /opt/litellm/bin/pip install --no-cache-dir --no-compile 'litellm[proxy]' pyyaml 'botocore[crt]' \
    && ln -s /opt/litellm/bin/litellm /usr/local/bin/litellm

RUN mkdir -p /ekchain-investigation \
    && cp /app/.mcp.json /ekchain-investigation/.mcp.json \
    && cd /ekchain-investigation && git init && git config user.email "ekchain@local" && git config user.name "ekchain" && git add -A && git commit -m "init"

RUN chown -R ekchain:ekchain /ekchain-investigation

COPY scripts/entrypoint.sh /usr/local/bin/entrypoint.sh
RUN chmod +x /usr/local/bin/entrypoint.sh

# --cwd defaults to ~/.ekchain/workspace on a native install; pin it back to the
# directory this image already creates, populates and git-inits above so
# container behaviour is unchanged.
ENV EKCHAIN_CWD=/ekchain-investigation

# The image is already provisioned, so pin the resolver to /opt outright. It is
# exclusive: without it a bind-mounted host $HOME carrying
# ~/.local/share/ekchain/assets would silently supplement (and could shadow) the
# image's curated, version-matched tree. `ekchain setup` inside the container
# consequently exits 1 -- /opt is root:root 0755 and the process runs as
# `ekchain` -- which is correct; there is nothing for it to do here.
ENV EKCHAIN_ASSET_ROOT=/opt

HEALTHCHECK --interval=60s --timeout=5s --start-period=10s --retries=3 \
    CMD ["ekchain", "--version"]

WORKDIR /ekchain-investigation
ENTRYPOINT ["entrypoint.sh"]
CMD ["ekchain", "investigate"]