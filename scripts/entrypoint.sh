#!/usr/bin/env bash
set -e

EKCHAIN_HOME="/home/ekchain"
EKCHAIN_CONFIG="$EKCHAIN_HOME/.config"
mkdir -p "$EKCHAIN_CONFIG"

# Copy mounted credential files to a ekchain-accessible location so the
# non-root user can read them without modifying host file permissions.
if [ -f /tmp/gcloud-creds.json ]; then
    cp /tmp/gcloud-creds.json "$EKCHAIN_CONFIG/gcloud-creds.json"
    chown ekchain:ekchain "$EKCHAIN_CONFIG/gcloud-creds.json"
    export GOOGLE_APPLICATION_CREDENTIALS="$EKCHAIN_CONFIG/gcloud-creds.json"
fi

chown -R ekchain:ekchain "$EKCHAIN_CONFIG" 2>/dev/null || true
chown -R ekchain:ekchain "$EKCHAIN_HOME/.ekchain" 2>/dev/null || true
# Ensure the cases directory is writable (may be a bind mount from host)
chmod -R a+rwX "$EKCHAIN_HOME/.ekchain/cases" 2>/dev/null || true

exec gosu ekchain "$@"
