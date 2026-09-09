#!/usr/bin/env bash
# Cleanly removes what install.sh installed. Only touches files install.sh
# created (venv directory, the portbridge symlink, the man page, and the
# systemd unit if present) -- config/state/logs are kept by default since
# they're your data, not PortBridge's installation footprint.
#
#   ./uninstall.sh            # user install
#   ./uninstall.sh --system   # system-wide install (needs root)
#   ./uninstall.sh --purge    # also remove config, state, and logs
set -euo pipefail

SYSTEM_INSTALL=0
PURGE=0
ASSUME_YES=0

info() { printf '\033[1;34m==>\033[0m %s\n' "$1"; }
warn() { printf '\033[1;33mWARNING:\033[0m %s\n' "$1" >&2; }
err()  { printf '\033[1;31mERROR:\033[0m %s\n' "$1" >&2; }

ask_yes_no() {
  local prompt="$1" default="$2" answer suffix="[y/N]"
  [ "$default" = "y" ] && suffix="[Y/n]"
  if [ ! -t 0 ]; then
    [ "$default" = "y" ]
    return $?
  fi
  read -r -p "$prompt $suffix " answer || answer=""
  answer="$(printf '%s' "$answer" | tr '[:upper:]' '[:lower:]')"
  if [ -z "$answer" ]; then
    [ "$default" = "y" ]
    return $?
  fi
  [ "$answer" = "y" ] || [ "$answer" = "yes" ]
}

while [ $# -gt 0 ]; do
  case "$1" in
    --system) SYSTEM_INSTALL=1 ;;
    --purge) PURGE=1 ;;
    -y|--yes) ASSUME_YES=1 ;;
    -h|--help)
      cat <<'EOF'
Usage: ./uninstall.sh [--system] [--purge] [-y]
  --system   Remove the machine-wide install under /opt and /usr/local
  --purge    Also delete config (~/.config/portbridge) and state/logs
             (~/.local/state/portbridge)
  -y, --yes  Don't prompt before stopping/disabling the systemd service
EOF
      exit 0 ;;
    *) err "Unknown option: $1"; exit 1 ;;
  esac
  shift
done

if [ "$SYSTEM_INSTALL" = 1 ]; then
  if [ "$(id -u)" != 0 ]; then
    err "--system requires root. Re-run as: sudo ./uninstall.sh --system"
    exit 1
  fi
  VENV_DIR="/opt/portbridge"
  BIN_PATH="/usr/local/bin/portbridge"
  MAN_PATH="/usr/local/share/man/man1/portbridge.1"
else
  VENV_DIR="$HOME/.local/share/portbridge"
  BIN_PATH="$HOME/.local/bin/portbridge"
  MAN_PATH="$HOME/.local/share/man/man1/portbridge.1"
fi

UNIT_PATH="/etc/systemd/system/portbridge.service"
if [ -f "$UNIT_PATH" ]; then
  if command -v systemctl >/dev/null 2>&1 && systemctl is-active --quiet portbridge 2>/dev/null; then
    if [ "$ASSUME_YES" = 1 ] || ask_yes_no "portbridge.service is running -- stop it now?" y; then
      if [ "$(id -u)" = 0 ]; then systemctl stop portbridge; else sudo systemctl stop portbridge; fi
    fi
  fi
  if command -v systemctl >/dev/null 2>&1 && systemctl is-enabled --quiet portbridge 2>/dev/null; then
    if [ "$ASSUME_YES" = 1 ] || ask_yes_no "portbridge.service is enabled -- disable it now?" y; then
      if [ "$(id -u)" = 0 ]; then systemctl disable portbridge; else sudo systemctl disable portbridge; fi
    fi
  fi
  if [ "$ASSUME_YES" = 1 ] || ask_yes_no "Remove $UNIT_PATH?" y; then
    if [ "$(id -u)" = 0 ]; then rm -f "$UNIT_PATH"; else sudo rm -f "$UNIT_PATH"; fi
    command -v systemctl >/dev/null 2>&1 && { [ "$(id -u)" = 0 ] && systemctl daemon-reload || sudo systemctl daemon-reload; } || true
    info "Removed systemd unit."
  fi
fi

if [ -L "$BIN_PATH" ] || [ -f "$BIN_PATH" ]; then
  rm -f "$BIN_PATH"
  info "Removed $BIN_PATH"
else
  warn "$BIN_PATH not found (already removed?)"
fi

if [ -f "$MAN_PATH" ]; then
  rm -f "$MAN_PATH"
  info "Removed $MAN_PATH"
fi

if [ -d "$VENV_DIR" ]; then
  rm -rf "$VENV_DIR"
  info "Removed $VENV_DIR"
else
  warn "$VENV_DIR not found (already removed?)"
fi

if [ "$PURGE" = 1 ]; then
  CONFIG_DIR="${XDG_CONFIG_HOME:-$HOME/.config}/portbridge"
  STATE_DIR="${XDG_STATE_HOME:-$HOME/.local/state}/portbridge"
  if [ "$ASSUME_YES" = 1 ] || ask_yes_no "Delete config ($CONFIG_DIR) and state/logs ($STATE_DIR)?" n; then
    rm -rf "$CONFIG_DIR" "$STATE_DIR"
    info "Removed config and state/log directories."
  fi
else
  info "Config and state/logs were left in place (~/.config/portbridge, ~/.local/state/portbridge)."
  info "Re-run with --purge to remove them too."
fi

echo
info "PortBridge uninstalled."
