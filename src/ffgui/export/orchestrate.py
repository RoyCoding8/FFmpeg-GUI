"""Job/queue → ExportBlocks → script text. The only entry point the UI calls."""

from __future__ import annotations

from datetime import datetime, UTC

from fftui.util.command_builder import build, build_two_pass, sidecars_for
from fftui.model import Job

from ffgui import __version__
from ffgui.export.bat import bat_script
from ffgui.export.model import ExportBlock, ScriptHeader, prune_repeated_sidecars
from ffgui.export.sh import sh_script

TARGETS = ("bat", "sh")


def blocks_for_job(job: Job) -> list[ExportBlock]:
    cars = sidecars_for(job)
    if job.two_pass:
        return prune_repeated_sidecars(
            [ExportBlock(list(argv), list(cars)) for argv in build_two_pass(job)])
    return [ExportBlock(build(job), list(cars))]


def _summary(job: Job) -> str:
    mode = "two-pass " if job.two_pass else ""
    first = job.inputs[0] if job.inputs else None
    if first is not None and first.concat_paths:
        inp, total = first.concat_paths[0], len(first.concat_paths)
    else:
        inp, total = (first.path if first is not None else "?"), len(job.inputs)
    tail = f" (+{total - 1} more)" if total > 1 else ""
    out = job.outputs[0].path if job.outputs else "?"
    return f"{mode}{inp}{tail} -> {out}"


def _header(jobs: list[Job], version_hash: str, created_utc: str | None) -> ScriptHeader:
    return ScriptHeader(
        generator=f"ffgui {__version__}",
        ffmpeg_version_hash=version_hash,
        created_utc=created_utc or datetime.now(UTC).strftime("%Y-%m-%d %H:%M UTC"),
        summary="; ".join(_summary(j) for j in jobs),
    )


def _render(target: str, blocks: list[ExportBlock], header: ScriptHeader) -> str:
    return bat_script(blocks, header) if target == "bat" else sh_script(blocks, header)


def script_for_job(
    job: Job, target: str, version_hash: str, *, created_utc: str | None = None
) -> str:
    if target not in TARGETS:
        raise ValueError(f"target must be one of {TARGETS}, got {target!r}")
    return _render(target, blocks_for_job(job), _header([job], version_hash, created_utc))


def script_for_queue(
    jobs: list[Job], target: str, version_hash: str, *, one_file: bool,
    created_utc: str | None = None,
) -> str | list[str]:
    if target not in TARGETS:
        raise ValueError(f"target must be one of {TARGETS}, got {target!r}")
    if one_file:
        blocks = prune_repeated_sidecars(
            [b for j in jobs for b in blocks_for_job(j)])
        return _render(target, blocks, _header(jobs, version_hash, created_utc))
    return [script_for_job(j, target, version_hash, created_utc=created_utc) for j in jobs]
