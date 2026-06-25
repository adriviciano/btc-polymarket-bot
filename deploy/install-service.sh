#!/usr/bin/env bash
#
# Install the BTC predictor (paper) bot + web dashboard as a systemd service on
# Raspberry Pi OS / Debian. It will start automatically on every boot and
# restart itself if it ever crashes.
#
# Usage (from the project root, do NOT prefix with sudo):
#   bash deploy/install-service.sh
#
# It will ask for your password only for the systemd steps.

set -euo pipefail

SERVICE_NAME="btc-predictor"
# Project root = parent of this script's folder.
PROJECT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
RUN_USER="${SUDO_USER:-$USER}"
VENV="$PROJECT_DIR/.venv"
PY="$VENV/bin/python"
UNIT="/etc/systemd/system/${SERVICE_NAME}.service"

echo "==> Project dir : $PROJECT_DIR"
echo "==> Run as user : $RUN_USER"
echo "==> Service     : $SERVICE_NAME"
echo

# --- 0) sanity checks -------------------------------------------------------
if [ ! -f "$PROJECT_DIR/.env" ]; then
  echo "!!  No .env found in $PROJECT_DIR"
  echo "    Create it first (copy .env.example to .env and fill in your keys)."
  exit 1
fi

# --- 1) virtualenv + dependencies ------------------------------------------
if [ ! -x "$PY" ]; then
  echo "==> Creating virtualenv at $VENV ..."
  python3 -m venv "$VENV"
fi
echo "==> Installing dependencies (this can take a few minutes on a Pi) ..."
"$PY" -m pip install --upgrade pip >/dev/null
"$PY" -m pip install -r "$PROJECT_DIR/requirements.txt"

# --- 2) write the systemd unit ---------------------------------------------
echo "==> Writing $UNIT ..."
sudo tee "$UNIT" >/dev/null <<EOF
[Unit]
Description=BTC Polymarket predictor (paper) bot + web dashboard
After=network-online.target
Wants=network-online.target
# 0 = never give up restarting (a flapping loop must not trip the start limit and stop
# the unit permanently, which is what caused the multi-day gaps in the paper log).
StartLimitIntervalSec=0

[Service]
Type=simple
User=$RUN_USER
WorkingDirectory=$PROJECT_DIR
Environment=DASHBOARD_HOST=0.0.0.0
Environment=DASHBOARD_PORT=8765
Environment=DASHBOARD_NO_BROWSER=1
Environment=PYTHONUNBUFFERED=1
ExecStart=$PY -m src.predictor_bot
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
EOF

# --- 3) enable + start ------------------------------------------------------
echo "==> Enabling and starting the service ..."
sudo systemctl daemon-reload
sudo systemctl enable "$SERVICE_NAME"
sudo systemctl restart "$SERVICE_NAME"

echo
echo "==> Status:"
sudo systemctl --no-pager --full status "$SERVICE_NAME" || true

IP="$(hostname -I 2>/dev/null | awk '{print $1}')"
echo
echo "============================================================"
echo " Done. The bot now starts on every boot and auto-restarts."
echo
echo " Dashboard:   http://${IP:-<IP-de-tu-Pi>}:8765"
echo " Live logs:   journalctl -u $SERVICE_NAME -f"
echo " Stop:        sudo systemctl stop $SERVICE_NAME"
echo " Start:       sudo systemctl start $SERVICE_NAME"
echo " Restart:     sudo systemctl restart $SERVICE_NAME"
echo " Disable:     sudo systemctl disable --now $SERVICE_NAME"
echo "============================================================"
