"""Terminal rendering: the boxed banners, status blocks, and prompts.

Kept dependency-free (no rich/curses) so the tool has zero third-party
runtime requirements beyond the stdlib TOML reader on old Pythons.
"""

from __future__ import annotations

import sys

from portbridge.errors import PortBridgeError

WIDTH = 42


def _supports_color() -> bool:
    if "NO_COLOR" in __import__("os").environ:
        return False
    return sys.stdout.isatty()


_COLOR = _supports_color()


def _c(code: str, text: str) -> str:
    if not _COLOR:
        return text
    return f"\033[{code}m{text}\033[0m"


def green(text: str) -> str:
    return _c("32", text)


def red(text: str) -> str:
    return _c("31", text)


def yellow(text: str) -> str:
    return _c("33", text)


def dim(text: str) -> str:
    return _c("2", text)


def box(lines: list[str], width: int = WIDTH) -> str:
    top = "╔" + "═" * width + "╗"
    bottom = "╚" + "═" * width + "╝"
    body = []
    for line in lines:
        pad = width - len(line)
        left = pad // 2
        right = pad - left
        body.append("║" + " " * left + line + " " * right + "║")
    return "\n".join([top, *body, bottom])


def banner() -> str:
    return box(["PORTBRIDGE", "TCP Forwarding Manager"])


def status_header() -> str:
    return box(["PORTBRIDGE STATUS"])


def check(ok: bool | None, label: str, detail: str | None = None) -> str:
    if ok is True:
        mark = green("✓")
    elif ok is False:
        mark = red("✗")
    else:
        mark = dim("?")
    line = f"  {mark} {label}"
    if detail and ok is not True:
        line += f"\n      {dim(detail)}"
    return line


def yesno(mark: bool) -> str:
    return green("YES") if mark else red("NO")


def print_error(err: PortBridgeError) -> None:
    print(f"\n{red('ERROR:')} {err.message}", file=sys.stderr)
    if err.hint:
        print(f"  {dim(err.hint)}", file=sys.stderr)
    print(file=sys.stderr)


def ask_yes_no(prompt: str, default: bool = False) -> bool:
    suffix = "[Y/n]" if default else "[y/N]"
    if not sys.stdin.isatty():
        return default
    try:
        answer = input(f"{prompt} {suffix} ").strip().lower()
    except (EOFError, KeyboardInterrupt):
        print()
        return default
    if not answer:
        return default
    return answer in ("y", "yes")


def ask(prompt: str, default: str | None = None) -> str:
    suffix = f" [{default}]" if default is not None else ""
    if not sys.stdin.isatty():
        if default is not None:
            return default
        raise EOFError("no TTY available and no default provided")
    try:
        answer = input(f"{prompt}{suffix}: ").strip()
    except (EOFError, KeyboardInterrupt):
        print()
        raise
    return answer or (default or "")


def ask_port(prompt: str, default: int | None = None) -> int:
    from portbridge.validation import parse_port

    default_str = str(default) if default else None
    while True:
        raw = ask(prompt, default_str)
        try:
            return parse_port(raw)
        except PortBridgeError as exc:
            print(f"  {red(exc.message)}")
