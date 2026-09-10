"""`.bat` serialization, probed through real cmd.exe: `%` doubles, tokens wrap
per BAT_UNSAFE, `setlocal DisableDelayedExpansion` is mandatory, sidecars embed
as `> ( echo( … )` caret-escaped blocks, `%VAR%` tokens splice raw."""

from __future__ import annotations

import re

from fftui.util.command_builder import Sidecar

from ffgui.export.model import (
    ExportBlock, ScriptHeader, header_comment, reject_hostile,
)

BAT_UNSAFE = frozenset(' \t"\'&|<>^()!`,;=%')
_ECHO_META = frozenset("&|<>^()")
_ENV_REF = re.compile(r"%[A-Za-z_][A-Za-z0-9_]*%")
_TRAILING_BS = re.compile(r"\\+$")


def bat_token(token: str) -> str:
    """One argv token as cmd.exe must receive it (the file's line, not its expansion)."""
    reject_hostile(token)
    if '"' in token:
        raise ValueError(f"token cannot be represented in a .bat file: {token!r}")
    if _ENV_REF.fullmatch(token):
        return token
    body = token.replace("%", "%%")
    if token and not any(c in BAT_UNSAFE for c in token):
        return body
    if run := _TRAILING_BS.search(body):
        body += "\\" * (run.end() - run.start())
    return f'"{body}"'


def _cmd_literal(line: str) -> str:
    """Escape free text for a cmd.exe line: `%` doubles, `&|<>^()` caret-escape.

    Shared by `echo(` sidecar bodies and `rem` comments — cmd parses
    separators, redirections, and `%`-expansion before either command runs, so
    a bare `&`, `>`, or `%0` in a comment would execute or expand."""
    out = []
    for ch in line.replace("%", "%%"):
        out.append(f"^{ch}" if ch in _ECHO_META else ch)
    return "".join(out)


def _sidecar_lines(car: Sidecar) -> list[str]:
    if not car.content.endswith("\n"):
        raise ValueError(f"sidecar {car.path!r} must end with a newline")
    if "\r" in car.content:
        raise ValueError(f"sidecar {car.path!r} cannot be represented in a .bat file")
    if '"' in car.content:
        raise ValueError(f"sidecar {car.path!r} cannot be represented in a .bat file")
    lines = [f"> {bat_token(car.path)} ("]
    for line in car.content.split("\n")[:-1]:
        lines.append(f" echo({_cmd_literal(line)}")
    lines.append(")")
    return lines


def bat_script(blocks: list[ExportBlock], header: ScriptHeader) -> str:
    lines = ["@echo off", "setlocal DisableDelayedExpansion",
             *("rem " + _cmd_literal(line) for line in header_comment(header, ""))]
    for block in blocks:
        for car in block.sidecars:
            lines += _sidecar_lines(car)
        if note := " ".join(block.workdir_note.split()):
            lines.append(f"rem {_cmd_literal(note)}")
        lines.append(" ".join(bat_token(t) for t in block.argv))
        lines.append("if not %errorlevel%==0 goto ffgui_fail")
    lines += ["echo All commands finished.", "pause", "endlocal & exit /b 0",
              ":ffgui_fail", "echo One of the commands failed.", "pause",
              "endlocal & exit /b 1"]
    return "\r\n".join(lines) + "\r\n"
