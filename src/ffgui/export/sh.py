"""`.sh` serialization: shlex.quote per token (never hand-rolled — 15/15 hostile
tokens round-trip byte-exact), heredoc sidecars, `set -e`."""

from __future__ import annotations

import shlex

from fftui.util.command_builder import Sidecar

from ffgui.export.model import (
    ExportBlock, ScriptHeader, header_comment, reject_hostile, sidecar_parent,
)

_HEREDOC = "FFGUI_EOF"


def sh_token(token: str) -> str:
    """One argv token POSIX-quoted. Newline/NUL tokens are unrepresentable (a
    hostile name would splice script lines) — fail loudly."""
    reject_hostile(token)
    return shlex.quote(token)


def _sidecar_lines(car: Sidecar) -> list[str]:
    lines = [f"mkdir -p {sh_token(sidecar_parent(car.path))}"]
    if not car.content.endswith("\n"):
        return [*lines, f"printf %s {shlex.quote(car.content)} > {sh_token(car.path)}"]
    delim = _HEREDOC
    while delim in car.content:
        delim += "_"
    return [*lines, f"cat > {sh_token(car.path)} <<'{delim}'", *car.content.split("\n")[:-1], delim]


def sh_script(blocks: list[ExportBlock], header: ScriptHeader) -> str:
    lines = ["#!/bin/sh", "set -e",
             "trap 'printf \"Press Enter to close... \"; read -r dummy || true' EXIT",
             *header_comment(header, "# ")]
    for block in blocks:
        for car in block.sidecars:
            lines += _sidecar_lines(car)
        if note := " ".join(block.workdir_note.split()):
            lines.append(f"# {note}")
        lines.append(" ".join(sh_token(t) for t in block.argv))
    return "\n".join(lines) + "\n"
