#!/usr/bin/env bash
# Install or update Jairbet as a systemd service on Ubuntu/Debian.
#
# Usage (from a clone of the repository):
#   sudo ./deploy/install.sh            # install or update
#   sudo ./deploy/install.sh --no-sleep # also keep the laptop awake with the lid closed
set -euo pipefail

APP_DIR=/opt/jairbet
DATA_DIR=/var/lib/jairbet
SERVICE=jairbet-bot
SERVICE_USER=jairbet
SOURCE_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

blue() { printf '\033[34m%s\033[0m\n' "$*"; }
green() { printf '\033[32m%s\033[0m\n' "$*"; }
yellow() { printf '\033[33m%s\033[0m\n' "$*"; }
red() { printf '\033[31m%s\033[0m\n' "$*" >&2; }

if [[ $EUID -ne 0 ]]; then
  red "Run as root: sudo $0 $*"
  exit 1
fi

blue "Installing system packages"
apt-get update -qq
apt-get install -y -qq python3 python3-venv sqlite3 rsync >/dev/null

if ! python3 -c 'import sys; sys.exit(sys.version_info < (3, 11))'; then
  red "Python 3.11+ is required (found $(python3 --version))."
  red "Use Ubuntu 24.04 / Debian 12 or newer, or install python3.11 first."
  exit 1
fi

if ! id -u "$SERVICE_USER" >/dev/null 2>&1; then
  blue "Creating system user $SERVICE_USER"
  useradd --system --home-dir "$DATA_DIR" --shell /usr/sbin/nologin "$SERVICE_USER"
fi

blue "Copying the application to $APP_DIR"
mkdir -p "$APP_DIR" "$DATA_DIR/backups"
if [[ "$SOURCE_DIR" != "$APP_DIR" ]]; then
  rsync -a --delete \
    --exclude .venv --exclude .env --exclude '*.db' --exclude '*.db-*' \
    --exclude backups --exclude __pycache__ --exclude '*.egg-info' \
    "$SOURCE_DIR/" "$APP_DIR/"
fi

blue "Creating the virtual environment"
python3 -m venv "$APP_DIR/.venv"
"$APP_DIR/.venv/bin/pip" install -q --upgrade pip
"$APP_DIR/.venv/bin/pip" install -q -e "$APP_DIR"

if [[ ! -f "$APP_DIR/.env" ]]; then
  blue "Creating $APP_DIR/.env from the example"
  sed -e "s|^DB_PATH=.*|DB_PATH=$DATA_DIR/jairbet.db|" \
      -e "s|^BACKUP_DIR=.*|BACKUP_DIR=$DATA_DIR/backups|" \
      "$APP_DIR/.env.example" > "$APP_DIR/.env"
  yellow "Edit $APP_DIR/.env with your tokens and Telegram IDs before starting."
fi
chown root:"$SERVICE_USER" "$APP_DIR/.env"
chmod 640 "$APP_DIR/.env"

if [[ -f "$SOURCE_DIR/jairbet.db" && ! -f "$DATA_DIR/jairbet.db" ]]; then
  blue "Copying the existing database to $DATA_DIR"
  sqlite3 "$SOURCE_DIR/jairbet.db" ".backup '$DATA_DIR/jairbet.db'"
fi
chown -R "$SERVICE_USER":"$SERVICE_USER" "$DATA_DIR"

blue "Installing the systemd service"
install -m 644 "$APP_DIR/deploy/$SERVICE.service" "/etc/systemd/system/$SERVICE.service"
systemctl daemon-reload
systemctl enable "$SERVICE" >/dev/null

if [[ "${1:-}" == "--no-sleep" ]]; then
  blue "Keeping the laptop awake with the lid closed"
  mkdir -p /etc/systemd/logind.conf.d
  cat > /etc/systemd/logind.conf.d/jairbet.conf <<'CONF'
[Login]
HandleLidSwitch=ignore
HandleLidSwitchExternalPower=ignore
HandleLidSwitchDocked=ignore
CONF
  systemctl mask sleep.target suspend.target hibernate.target hybrid-sleep.target >/dev/null
  yellow "Lid settings apply after a reboot (or: systemctl restart systemd-logind)."
fi

if grep -q '^TELEGRAM_BOT_TOKEN=.\+' "$APP_DIR/.env"; then
  systemctl restart "$SERVICE"
  green "Jairbet is running. Logs: journalctl -u $SERVICE -f"
else
  yellow "Fill in $APP_DIR/.env, then: sudo systemctl start $SERVICE"
fi
