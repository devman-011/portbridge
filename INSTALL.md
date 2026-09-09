# Installing PortBridge

## Requirements

- Linux (any distribution with Python 3.8+; `install.sh` detects your
  distro and architecture and tells you exactly what to run if something's
  missing).
- `python3` with the `venv` module.
- The `frpc` binary (`portbridge setup` offers to download it for you,
  matched to this machine's CPU architecture).
- A relay of your own already deployed somewhere reachable — see README's
  [Deploying your own relay](README.md#deploying-your-own-relay).
  PortBridge doesn't set this part up for you; it's a one-time step
  outside PortBridge itself.

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
  -y, --yes        Assume yes for optional-but-safe prompts (dependencies)
  --with-systemd   Also install the systemd unit (still not enabled/started)
  --no-systemd     Never prompt about the systemd unit
  -h, --help       Show help
```

The installer never enables or starts anything on its own — it only asks,
installs files, and tells you the exact commands to run next.

## First-time setup

You need a relay deployed first — see README's
[Deploying your own relay](README.md#deploying-your-own-relay) if you
haven't done that yet. Once you have its control address/port, data port,
public address/port, and auth token:

```bash
portbridge setup
```

Checks whether `frpc` is installed (offers to download it for this
machine's architecture if not), walks you through entering the relay
details above, and confirms it's actually reachable.

```bash
portbridge configure --relay-token <token>   # if you didn't enter it during setup
portbridge configure                         # optional: interactive wizard for other defaults
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
portbridge configure --local-port 9001
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
