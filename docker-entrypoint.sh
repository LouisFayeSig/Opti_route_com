#!/bin/sh
set -eu

# Azure Container Apps secret volumes expose every secret under its own name.
# Streamlit, however, only loads /app/.streamlit/secrets.toml. Copy the
# Key Vault-backed secret into that expected location at container startup.
secret_source="/mnt/opti-route-secrets/streamlit-secrets"
secret_target="/app/.streamlit/secrets.toml"

if [ -f "$secret_source" ]; then
  umask 077
  cp "$secret_source" "$secret_target"
fi

exec streamlit run app.py \
  --server.address=0.0.0.0 \
  --server.port=8501 \
  --server.headless=true \
  --server.maxUploadSize=20 \
  --server.enableXsrfProtection=true
