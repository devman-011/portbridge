# Contributing to PortBridge

PortBridge is a small, dependency-free Python CLI. Contributions,
bug reports, and forks are welcome.

## Project layout

```
portbridge/
  src/portbridge/
    cli.py          Argument parsing, interactive menu, all do_*() commands
    core.py          Shared probe logic (no printing, no prompts)
    relay.py         Thin wrapper around the `frpc` binary: install, config generation, spawn
    monitor.py       Background health-check / auto-reconnect loop; supervises frpc as a child
    process.py       Spawning/terminating the detached monitor process
    state.py         state.json read/write, stale-PID detection
    config.py        config.toml read/write, defaults, relay token file handling
    netcheck.py       Plain TCP connectivity checks
    ui.py            Terminal rendering (boxes, prompts, colors)
    validation.py    Port validation shared by CLI args and prompts
    errors.py        User-facing exception types (message + hint)
    testserver.py    Disposable echo listener (`portbridge listen`)
    logging_setup.py Rotating log file + tail/follow
  install.sh / uninstall.sh
  systemd/portbridge.service   Optional unit template (never auto-enabled)
  man/portbridge.1
  README.md / INSTALL.md / TROUBLESHOOTING.md / TESTING.md
```

Note the relay server (`frps`) itself is not part of this repo — it's a
separate one-time deployment (Dockerfile + frps.toml in the README) that
you run on whatever host gives you a public TCP port. PortBridge only
ever manages the client side (`frpc`).

`cli.py` is the only place that prints to the terminal or prompts for
input — every other module either returns data or raises a
`PortBridgeError` (see `errors.py`). Keep that separation: it's what lets
`portbridge start --foreground` (used by the systemd unit) and the
detached background monitor share the exact same logic as the interactive
menu.

## Design principles (please keep these)

- **Stdlib only.** The single conditional dependency is `tomli` on Python
  < 3.11 (see `requirements.txt`). Don't add a dependency for something
  the standard library already does.
- **No silent scope creep.** PortBridge only ever touches the exact port
  mapping you asked for. It never scans, never opens extra ports, never
  modifies firewall rules, and never executes anything received over the
  forwarded TCP connection.
- **Ask before system-modifying actions.** Anything that installs
  software, starts/enables a system service, or runs `sudo` must go
  through an explicit confirmation (see `_confirm_system_change` in
  `cli.py`) — and must never silently proceed just because stdin isn't a
  TTY. Only an explicit `--yes` flag or a real answer at a real terminal
  should trigger those.
- **No raw tracebacks for expected failures.** Anything a user can
  reasonably hit (missing binary, bad port, network down, etc.) should be
  a `PortBridgeError` subclass in `errors.py` with a plain-English
  `message` and a `hint` (a diagnostic command), not a bare exception.
- **Honesty about relay/frp limits.** Don't paper over `frpc` failures or
  relay misconfiguration with vague messages — surface the real cause
  (bad token, unreachable relay, port mismatch) clearly instead.

## Setting up a dev environment

```bash
git clone https://github.com/devman-011/portbridge.git
cd portbridge
python3 -m venv .venv
source .venv/bin/activate
pip install -e .
portbridge --version
```

## Making changes

1. Compile-check before anything else:
   ```bash
   python3 -m py_compile src/portbridge/*.py
   ```
2. Run through the relevant scenarios in [TESTING.md](TESTING.md) — there's
   no automated test suite yet (a good first contribution if you want one:
   pytest around `config.py`/`state.py`/`validation.py`/`core.py`, which
   have no `frpc` dependency and are easy to unit test in isolation).
3. Keep `README.md`/`TROUBLESHOOTING.md`/the man page in sync with any
   user-facing CLI change (new flag, new command, changed default).
4. Open a pull request describing what changed and why.

## Reporting bugs

Include:
- `portbridge --version`
- `frpc --version`
- Your distro (`cat /etc/os-release`)
- The relevant excerpt from `~/.local/state/portbridge/portbridge.log`
  (PortBridge logs full tracebacks there even when the terminal only shows
  a short message)
