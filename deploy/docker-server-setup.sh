#!/usr/bin/env bash
# One-time setup of the Ubuntu server for the Docker deploy.
# Installs Docker + Compose, lets your user run docker, and keeps the laptop
# awake with the lid closed.
#
# Usage, on the server:   sudo ./docker-server-setup.sh
set -euo pipefail

blue() { printf '\033[34m%s\033[0m\n' "$*"; }
green() { printf '\033[32m%s\033[0m\n' "$*"; }
yellow() { printf '\033[33m%s\033[0m\n' "$*"; }
red() { printf '\033[31m%s\033[0m\n' "$*" >&2; }

if [[ $EUID -ne 0 ]]; then
  red "Run as root: sudo $0"
  exit 1
fi
TARGET_USER="${SUDO_USER:-}"

if ! command -v docker >/dev/null 2>&1 || ! docker compose version >/dev/null 2>&1; then
  blue "Installing Docker"
  apt-get update -qq
  apt-get install -y -qq docker.io docker-compose-v2 >/dev/null
fi
systemctl enable --now docker >/dev/null

if [[ -n "$TARGET_USER" ]] && ! id -nG "$TARGET_USER" | grep -qw docker; then
  blue "Adding $TARGET_USER to the docker group"
  usermod -aG docker "$TARGET_USER"
  yellow "Log out and back in (or reconnect ssh) so the group applies."
fi

blue "Keeping the laptop awake with the lid closed"
mkdir -p /etc/systemd/logind.conf.d
cat > /etc/systemd/logind.conf.d/jairbet.conf <<'CONF'
[Login]
HandleLidSwitch=ignore
HandleLidSwitchExternalPower=ignore
HandleLidSwitchDocked=ignore
CONF
systemctl mask sleep.target suspend.target hibernate.target hybrid-sleep.target \
  >/dev/null
systemctl restart systemd-logind || yellow "Reboot to apply the lid settings."

if systemctl list-unit-files jairbet-bot.service >/dev/null 2>&1 \
   && systemctl is-enabled jairbet-bot >/dev/null 2>&1; then
  yellow "Disabling the old systemd jairbet-bot service (Docker replaces it)"
  systemctl disable --now jairbet-bot
fi

green "Server ready: $(docker --version), $(docker compose version)"
