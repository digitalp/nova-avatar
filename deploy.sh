#!/usr/bin/env bash
# deploy.sh — Push git repo to the live Nova server and restart
# Usage: ./deploy.sh [--no-restart] [--rollback]
set -euo pipefail

SERVER="${NOVA_DEPLOY_SERVER:-penn@192.168.0.249}"
REMOTE_DIR="/opt/avatar-server"
BACKUP_DIR="/opt/avatar-server.rollback"
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"

# Files/dirs to exclude from deployment
EXCLUDES=(
  .git .kiro __pycache__ '*.pyc' '*.db' '*.glb'
  .env users.json piper_voices data/ logs/ .venv/
  avatar_backend_live/ '*.bak' '*.bak_*'
  home_runtime.json avatar_settings.json
  piper/ models/
)

EXCLUDE_ARGS=""
for e in "${EXCLUDES[@]}"; do
  EXCLUDE_ARGS="$EXCLUDE_ARGS --exclude=$e"
done

# SSH multiplexing
export SSH_OPTS="-o ControlMaster=auto -o ControlPath=/tmp/nova-deploy-%r@%h -o ControlPersist=60"
export RSYNC_RSH="ssh $SSH_OPTS"

# ── Rollback mode ─────────────────────────────────────────────────────────────
if [[ "${1:-}" == "--rollback" ]]; then
  echo "⏪ Rolling back to previous deployment..."
  HAS_BACKUP=$(ssh $SSH_OPTS "$SERVER" "test -d $BACKUP_DIR && echo yes || echo no")
  if [[ "$HAS_BACKUP" != "yes" ]]; then
    echo "❌ No rollback backup found at $BACKUP_DIR"
    exit 1
  fi
  ssh $SSH_OPTS "$SERVER" "sudo rsync -a --delete \
    --exclude=.env --exclude=users.json --exclude=home_runtime.json \
    --exclude=avatar_settings.json --exclude='*.db' --exclude=data/ \
    --exclude=logs/ --exclude=.venv/ --exclude=piper/ --exclude=models/ \
    --exclude=static/avatars/ --exclude=config/ \
    $BACKUP_DIR/ $REMOTE_DIR/"
  echo "✅ Files restored from backup"
  echo "🔄 Restarting avatar-backend..."
  ssh $SSH_OPTS "$SERVER" 'sudo systemctl restart avatar-backend'
  sleep 4
  STATUS=$(ssh $SSH_OPTS "$SERVER" 'sudo systemctl is-active avatar-backend 2>/dev/null || echo failed')
  if [[ "$STATUS" == "active" ]]; then
    echo "✅ Rollback complete — avatar-backend is running"
  else
    echo "❌ avatar-backend failed to start after rollback!"
    ssh $SSH_OPTS "$SERVER" 'sudo journalctl -u avatar-backend --no-pager -n 15' 2>/dev/null
    exit 1
  fi
  exit 0
fi

# ── Normal deploy ─────────────────────────────────────────────────────────────
echo "🚀 Deploying nova-v1 to $SERVER:$REMOTE_DIR"
echo ""

# Backup current state before deploying
echo "📦 Backing up current deployment..."
ssh $SSH_OPTS "$SERVER" "sudo rsync -a --delete \
  --exclude=.venv/ --exclude=data/ --exclude=logs/ --exclude='*.db' \
  --exclude=piper/ --exclude=models/ --exclude='*.glb' \
  $REMOTE_DIR/ $BACKUP_DIR/"
echo "   Backup saved to $BACKUP_DIR"

# Sync files
rsync -avz --delete $EXCLUDE_ARGS \
  "$SCRIPT_DIR/" "$SERVER:$REMOTE_DIR/" \
  --rsync-path="rsync"

echo ""
echo "✅ Files synced"

# Restart unless --no-restart
if [[ "${1:-}" != "--no-restart" ]]; then
  echo "🔄 Restarting avatar-backend..."
  ssh $SSH_OPTS "$SERVER" 'sudo systemctl restart avatar-backend'
  sleep 4
  STATUS=$(ssh $SSH_OPTS "$SERVER" 'sudo systemctl is-active avatar-backend 2>/dev/null || echo failed')
  if [[ "$STATUS" == "active" ]]; then
    echo "✅ avatar-backend is running"
    HEALTH=$(ssh $SSH_OPTS "$SERVER" 'curl -s http://localhost:8001/health 2>/dev/null' || echo '{}')
    echo "   $HEALTH"
  else
    echo "❌ avatar-backend failed to start! Run ./deploy.sh --rollback to revert"
    ssh $SSH_OPTS "$SERVER" 'sudo journalctl -u avatar-backend --no-pager -n 15' 2>/dev/null
    exit 1
  fi
else
  echo "⏭  Skipping restart (--no-restart)"
fi

echo ""
echo "🎉 Deploy complete (rollback available via ./deploy.sh --rollback)"
