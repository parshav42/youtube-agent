#!/bin/bash
set -e

# Read NGROK_DOMAIN from .env without sourcing (safe for values with spaces)
NGROK_DOMAIN=$(grep '^NGROK_DOMAIN=' /home/parshav/MotivationOS/.env | cut -d= -f2- | tr -d '"' | tr -d "'")

if [ -z "$NGROK_DOMAIN" ]; then
    echo "ERROR: NGROK_DOMAIN not set in .env"
    exit 1
fi

exec /usr/local/bin/ngrok http --domain="$NGROK_DOMAIN" 8000 --log=stdout
