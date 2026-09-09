# Installing PortBridge

## Requirements

- Linux (any distribution with Python 3.8+; `install.sh` detects your
  distro and architecture and tells you exactly what to run if something's
  missing).
- `python3` with the `venv` module.
- The `tailscale` CLI (the installer offers to install it for you via
  Tailscale's official script if it's missing).

## Standard install (per-user, no root required)

```bash
git clone https://github.com/devman-011/portbridge.git
cd portbridge
./install.sh
```

This creates a virtual environment at `~/.local/share/portbridge/venv`,
installs PortBridge into it, and symlinks the `portbridge` command into
`~/.local/bin/portbridge`. If `~/.local/bin` isn't already on your `PATH`,
the installer tells you the exact line to add to your shell profile.

Verify:

```bash
portbridge --version
```

## System-wide install (all users, requires root)

```bash
git clone https://github.com/devman-011/portbridge.git
cd portbridge
sudo ./install.sh --system
```

Installs to `/opt/portbridge/venv`, symlinked as `/usr/local/bin/portbridge`.

## Installer options

```
./install.sh [options]

  --system         Install machine-wide under /opt and /usr/local (needs root)
  -y, --yes        Assume yes for optional-but-safe prompts (deps, Tailscale)
  --with-systemd   Also install the systemd unit (still not enabled/started)
  --no-systemd     Never prompt about the systemd unit
  -h, --help       Show help
```

The installer never enables or starts anything on its own — it only asks,
installs files, and tells you the exact commands to run next.

## First-time setup

```bash
portbridge setup
```

This checks whether Tailscale is installed, running, and authenticated,
offering to fix each gap for you (installing it, starting the
`tailscaled` service, running `tailscale up`) with a confirmation prompt
before each action -- see README's [First-time setup](README.md#first-time-setup)
for exactly what it does and why the login step still needs you personally.

```bash
portbridge configure   # optional: set defaults (local/external port, etc.)
```

## Exact first-run commands

```bash
# Terminal A: a local service to forward (any TCP service works; this is
# just a disposable example)
portbridge listen --port 9001

# Terminal B: start forwarding
portbridge start --port 9001
portbridge status
portbridge endpoint
```

See [TESTING.md](TESTING.md) for the full two-machine verification procedure.

## Enabling the systemd unit (optional)

Only if you passed `--with-systemd` (or answered yes to the prompt) during
install:

```bash
portbridge configure --local-port 9001 --external-port 10000
sudo systemctl daemon-reload
sudo systemctl enable --now portbridge
```

This is entirely optional and never happens automatically.

## Uninstalling

```bash
cd portbridge
./uninstall.sh                 # matches a user install
sudo ./uninstall.sh --system   # matches a --system install
./uninstall.sh --purge         # also deletes config/state/logs
```

`uninstall.sh` only removes what `install.sh` created: the virtual
environment, the `portbridge` symlink, the man page, and — if present — the
systemd unit (stopping/disabling it first, with confirmation). Your
config, state, and logs are kept unless you pass `--purge`.
