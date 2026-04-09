#!/usr/bin/env bash
set -euo pipefail

SOURCE_DIR="${SOURCE_DIR:-/opt/avatar-server}"
TARGET_DIR="${1:-/opt/nova-v2}"
SERVICE_NAME="${SERVICE_NAME:-nova-v2}"
PORT="${PORT:-8011}"
PUBLIC_URL="${PUBLIC_URL:-http://127.0.0.1:${PORT}}"

if [[ $EUID -ne 0 ]]; then
  echo "Run as root: sudo $0 [target-dir]" >&2
  exit 1
fi

if [[ ! -d "${SOURCE_DIR}" ]]; then
  echo "Source dir not found: ${SOURCE_DIR}" >&2
  exit 1
fi

mkdir -p "${TARGET_DIR}"
rsync -a \
  --exclude='.git' \
  --exclude='.env' \
  --exclude='.venv' \
  --exclude='__pycache__' \
  --exclude='*.pyc' \
  "${SOURCE_DIR}/" "${TARGET_DIR}/"

mkdir -p "${TARGET_DIR}/logs" "${TARGET_DIR}/data"

if [[ ! -f "${TARGET_DIR}/.env" ]]; then
  cat > "${TARGET_DIR}/.env" <<EOF
HOST=0.0.0.0
PORT=${PORT}
LOG_LEVEL=INFO
PUBLIC_URL=${PUBLIC_URL}
CORS_ORIGINS=${PUBLIC_URL}
EOF
fi

cat > "/etc/systemd/system/${SERVICE_NAME}.service" <<EOF
[Unit]
Description=${SERVICE_NAME}
After=network.target

[Service]
Type=simple
WorkingDirectory=${TARGET_DIR}
Environment=NOVA_APP_ROOT=${TARGET_DIR}
Environment=NOVA_ENV_FILE=${TARGET_DIR}/.env
Environment=PYTHONPATH=${TARGET_DIR}
EnvironmentFile=${TARGET_DIR}/.env
ExecStart=${TARGET_DIR}/.venv/bin/uvicorn avatar_backend.main:app --host 0.0.0.0 --port ${PORT} --log-level info
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
EOF

echo "Scaffolded parallel app:"
echo "  target: ${TARGET_DIR}"
echo "  service: ${SERVICE_NAME}"
echo "  port: ${PORT}"
echo ""
echo "Next steps:"
echo "  1. Create ${TARGET_DIR}/.venv and install requirements"
echo "  2. Fill in ${TARGET_DIR}/.env"
echo "  3. systemctl daemon-reload"
echo "  4. systemctl enable --now ${SERVICE_NAME}"
