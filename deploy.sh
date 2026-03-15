#!/usr/bin/env bash
# ── THE FLEET — Server deploy script ─────────────────────────────────────────
# Usage: ./deploy.sh user@your-server-ip
# Tested on Ubuntu 22.04 / Debian 12
set -euo pipefail

SERVER="${1:-}"
if [[ -z "$SERVER" ]]; then
  echo "Usage: $0 user@server-ip"
  exit 1
fi

echo "▓▓▓ THE FLEET — Deploying to $SERVER"

# ── 1. Bootstrap server ───────────────────────────────────────────────────────
ssh "$SERVER" 'bash -s' <<'REMOTE'
set -euo pipefail
echo "→ Installing system deps"
apt-get update -qq && apt-get install -y -qq python3.12 python3.12-venv git curl

echo "→ Creating fleet user"
id fleet 2>/dev/null || useradd -m -s /bin/bash fleet

echo "→ Setting up directory"
mkdir -p /opt/the-fleet/data
chown -R fleet:fleet /opt/the-fleet
REMOTE

# ── 2. Sync code ─────────────────────────────────────────────────────────────
echo "→ Syncing code"
rsync -az --exclude='.env' --exclude='fleet.enc' --exclude='.venv' \
  --exclude='__pycache__' --exclude='data/' --exclude='.git' \
  ./ "$SERVER:/opt/the-fleet/"

# ── 3. Sync .env ──────────────────────────────────────────────────────────────
echo "→ Uploading .env (secrets)"
scp .env "$SERVER:/opt/the-fleet/.env"
ssh "$SERVER" "chmod 600 /opt/the-fleet/.env && chown fleet:fleet /opt/the-fleet/.env"

# ── 4. Install Python deps ────────────────────────────────────────────────────
ssh "$SERVER" 'bash -s' <<'REMOTE'
cd /opt/the-fleet
python3.12 -m venv .venv
.venv/bin/pip install -q --upgrade pip
.venv/bin/pip install -q -r requirements.txt
chown -R fleet:fleet /opt/the-fleet
REMOTE

# ── 5. Install systemd service ────────────────────────────────────────────────
echo "→ Installing systemd service"
ssh "$SERVER" 'bash -s' <<'REMOTE'
cat > /etc/systemd/system/the-fleet.service <<'SERVICE'
[Unit]
Description=The Fleet — Sovereign AI Telegram Agent
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=fleet
WorkingDirectory=/opt/the-fleet
ExecStart=/opt/the-fleet/.venv/bin/python fleet.py
Restart=on-failure
RestartSec=15
StandardOutput=journal
StandardError=journal
# Memory guard — restart if >256MB
MemoryMax=256M

[Install]
WantedBy=multi-user.target
SERVICE

systemctl daemon-reload
systemctl enable the-fleet
systemctl restart the-fleet
echo "→ Service status:"
systemctl status the-fleet --no-pager -l
REMOTE

echo ""
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "✓ THE FLEET deployed to $SERVER"
echo ""
echo "Commands:"
echo "  ssh $SERVER 'systemctl status the-fleet'   # status"
echo "  ssh $SERVER 'journalctl -u the-fleet -f'   # live logs"
echo "  ssh $SERVER 'systemctl stop the-fleet'     # stop"
echo "  ssh $SERVER 'touch /opt/the-fleet/.wake'   # terminal wake"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
