#!/usr/bin/env bash
# PortBridge installer.
#
#   git clone https://github.com/devman-011/portbridge.git
#   cd portbridge
#   ./install.sh
#
# By default installs for the current user only (no root required):
#   venv:       ~/.local/share/portbridge/venv
#   executable: ~/.local/bin/portbridge
#   man page:   ~/.local/share/man/man1/portbridge.1
#
# Pass --system to install machine-wide instead (requires root):
#   venv:       /opt/portbridge/venv
#   executable: /usr/local/bin/portbridge
#   man page:   /usr/local/share/man/man1/portbridge.1
#
# Never overwrites unrelated files, never enables a systemd service on its
# own, and never installs Tailscale or a systemd unit without asking first
# (unless -y/--yes is combined with the relevant explicit flag, matching
# normal Linux installer conventions).
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

SYSTEM_INSTALL=0
ASSUME_YES=0
WITH_SYSTEMD=0
NO_SYSTEMD_PROMPT=0

info()  { printf '\033[1;34m==>\033[0m %s\n' "$1"; }
warn()  { printf '\033[1;33mWARNING:\033[0m %s\n' "$1" >&2; }
err()   { printf '\033[1;31mERROR:\033[0m %s\n' "$1" >&2; }

usage() {
  cat <<'EOF'
Usage: ./install.sh [options]

Options:
  --system         Install machine-wide under /opt and /usr/local (needs root)
  -y, --yes        Assume yes for optional-but-safe prompts (deps, Tailscale)
  --with-systemd   Also install the systemd unit (still not enabled/started)
  --no-systemd     Never prompt about the systemd unit
  -h, --help       Show this help
EOF
}

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
    -y|--yes) ASSUME_YES=1 ;;
    --with-systemd) WITH_SYSTEMD=1 ;;
    --no-systemd) NO_SYSTEMD_PROMPT=1 ;;
    -h|--help) usage; exit 0 ;;
    *) err "Unknown option: $1"; usage; exit 1 ;;
  esac
  shift
done

if [ ! -f "$SCRIPT_DIR/pyproject.toml" ]; then
  err "Could not find pyproject.toml next to install.sh."
  err "Run this script from inside the cloned portbridge/ directory."
  exit 1
fi

if [ "$(uname -s)" != "Linux" ]; then
  err "PortBridge is Linux-only (relies on /proc, systemd, and Tailscale Funnel). Detected: $(uname -s)"
  exit 1
fi

DISTRO_ID="unknown"
DISTRO_NAME="unknown Linux"
if [ -r /etc/os-release ]; then
  # shellcheck disable=SC1091
  . /etc/os-release
  DISTRO_ID="${ID:-unknown}"
  DISTRO_NAME="${PRETTY_NAME:-unknown Linux}"
fi
ARCH="$(uname -m)"
info "Detected: $DISTRO_NAME ($ARCH)"

# --- python3 -----------------------------------------------------------
if ! command -v python3 >/dev/null 2>&1; then
  err "python3 was not found."
  case "$DISTRO_ID" in
    ubuntu|debian|raspbian) echo "  sudo apt-get update && sudo apt-get install -y python3 python3-venv python3-pip" ;;
    fedora)                 echo "  sudo dnf install -y python3" ;;
    rhel|centos|rocky|almalinux|ol) echo "  sudo yum install -y python3" ;;
    arch)                   echo "  sudo pacman -S python" ;;
    opensuse*|sles)         echo "  sudo zypper install -y python3" ;;
    *)                      echo "  Install Python 3.8+ using your distribution's package manager." ;;
  esac
  exit 1
fi

PY_OK="$(python3 -c 'import sys; print(1 if sys.version_info >= (3, 8) else 0)')"
PY_VERSION="$(python3 -c 'import sys; print(".".join(map(str, sys.version_info[:3])))')"
if [ "$PY_OK" != "1" ]; then
  err "python3 $PY_VERSION found, but PortBridge requires Python 3.8 or newer."
  exit 1
fi
info "python3 $PY_VERSION OK"

if ! python3 -c 'import venv' >/dev/null 2>&1; then
  warn "python3's 'venv' module is not available."
  case "$DISTRO_ID" in
    ubuntu|debian|raspbian)
      if [ "$ASSUME_YES" = 1 ] || ask_yes_no "Install python3-venv now with sudo apt-get?" y; then
        sudo apt-get update && sudo apt-get install -y python3-venv
      else
        err "Cannot continue without the venv module."
        exit 1
      fi
      ;;
    *)
      err "Install your distribution's python3-venv (or equivalent) package and re-run."
      exit 1
      ;;
  esac
fi

# --- tailscale -----------------------------------------------------------
if command -v tailscale >/dev/null 2>&1; then
  info "tailscale found: $(tailscale version 2>/dev/null | head -n1)"
else
  warn "The 'tailscale' CLI was not found. PortBridge needs it to create the public endpoint."
  if [ "$ASSUME_YES" = 1 ] || ask_yes_no "Install Tailscale now via the official install script (curl https://tailscale.com/install.sh | sh)?" y; then
    curl -fsSL https://tailscale.com/install.sh | sh
  else
    warn "Skipping. Install it later with: curl -fsSL https://tailscale.com/install.sh | sh"
  fi
fi

# --- install prefix -----------------------------------------------------
if [ "$SYSTEM_INSTALL" = 1 ]; then
  if [ "$(id -u)" != 0 ]; then
    err "--system requires root. Re-run as: sudo ./install.sh --system"
    exit 1
  fi
  VENV_DIR="/opt/portbridge/venv"
  BIN_DIR="/usr/local/bin"
  MAN_DIR="/usr/local/share/man/man1"
  INSTALL_USER="${SUDO_USER:-root}"
else
  if [ "$(id -u)" = 0 ]; then
    err "Running as root without --system. Re-run as a normal user, or pass --system."
    exit 1
  fi
  VENV_DIR="$HOME/.local/share/portbridge/venv"
  BIN_DIR="$HOME/.local/bin"
  MAN_DIR="$HOME/.local/share/man/man1"
  INSTALL_USER="$(id -un)"
fi

info "Creating virtual environment at $VENV_DIR"
mkdir -p "$(dirname "$VENV_DIR")"
python3 -m venv "$VENV_DIR"
"$VENV_DIR/bin/pip" install --upgrade pip >/dev/null

info "Installing PortBridge"
"$VENV_DIR/bin/pip" install "$SCRIPT_DIR"

mkdir -p "$BIN_DIR"
ln -sf "$VENV_DIR/bin/portbridge" "$BIN_DIR/portbridge"
info "Installed executable: $BIN_DIR/portbridge"

case ":${PATH:-}:" in
  *":$BIN_DIR:"*) ;;
  *)
    warn "$BIN_DIR is not on your PATH."
    echo "  Add this to your ~/.bashrc or ~/.zshrc, then restart your shell:"
    echo "    export PATH=\"$BIN_DIR:\$PATH\""
    ;;
esac

mkdir -p "$MAN_DIR"
cp "$SCRIPT_DIR/man/portbridge.1" "$MAN_DIR/portbridge.1"
info "Installed man page: $MAN_DIR/portbridge.1"
if [ "$SYSTEM_INSTALL" = 1 ] && command -v mandb >/dev/null 2>&1; then
  mandb -q || true
fi

# --- optional systemd unit (never enabled/started) -----------------------
WANT_SYSTEMD=0
if [ "$WITH_SYSTEMD" = 1 ]; then
  WANT_SYSTEMD=1
elif [ "$NO_SYSTEMD_PROMPT" = 0 ] && [ "$ASSUME_YES" = 0 ] && [ -t 0 ]; then
  if ask_yes_no "Install a systemd unit for PortBridge? (it will NOT be enabled or started)" n; then
    WANT_SYSTEMD=1
  fi
fi

if [ "$WANT_SYSTEMD" = 1 ]; then
  if [ "$(id -u)" != 0 ] && ! command -v sudo >/dev/null 2>&1; then
    warn "Need root or sudo to install the systemd unit; skipping."
  else
    UNIT_TMP="$(mktemp)"
    sed -e "s#__PORTBRIDGE_USER__#$INSTALL_USER#g" \
        -e "s#__PORTBRIDGE_EXEC__#$VENV_DIR/bin/portbridge#g" \
        "$SCRIPT_DIR/systemd/portbridge.service" > "$UNIT_TMP"
    if [ "$(id -u)" = 0 ]; then
      cp "$UNIT_TMP" /etc/systemd/system/portbridge.service
    else
      sudo cp "$UNIT_TMP" /etc/systemd/system/portbridge.service
    fi
    rm -f "$UNIT_TMP"
    info "Installed (NOT enabled) systemd unit: /etc/systemd/system/portbridge.service"
  fi
fi

echo
info "PortBridge installed."
echo
echo "Verify:"
echo "  portbridge --version"
echo
echo "First-time setup:"
echo "  sudo tailscale up                                # authenticate this machine"
echo "  sudo tailscale set --operator=$INSTALL_USER          # optional: unattended reconnect without sudo"
echo "  portbridge configure                             # set defaults (optional)"
echo "  portbridge start --port <your-local-port>"
echo
if [ "$WANT_SYSTEMD" = 1 ]; then
  echo "Systemd unit was installed but not enabled. To use it:"
  echo "  portbridge configure --local-port <PORT> --external-port <443|8443|10000>"
  echo "  sudo systemctl daemon-reload"
  echo "  sudo systemctl enable --now portbridge"
  echo
fi
echo "Uninstall any time with: ./uninstall.sh"
