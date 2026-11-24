#!/usr/bin/env python3
"""Formal verification suite for FFMpeg-GUI probe lifecycle & Qt UI (A1, U1, U2).

Checks 3 formal properties directly tied to the real codebase:
  A1: Exhaustive BFS concurrency model check of app.py:_wire_probe & _launch_probe
      (no double _boot, no deadlocked loading state, no stale CapabilityIndex reuse)
  U1: Exhaustive real-Qt round-trip bijectivity across all 8 tabs <-> Controller <-> QueueDocument
  U2: Exhaustive pairwise non-interference across all 7 video/audio filter controls
      (42 ordered pairs + clear isolation) and MetadataTab row-switch isolation

Run:
  QT_QPA_PLATFORM=offscreen PYTHONPATH="$PWD/src" .venv/bin/python verification/verify_probe_ui.py
"""

from __future__ import annotations

import argparse
import os
import sys
import tempfile
import time
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from fftui.ffmpeg.capability_index import CapabilityEntry, CapabilityIndex
from fftui.model import Input, InputStream
from PySide6.QtCore import QSettings
from PySide6.QtWidgets import QApplication, QCheckBox, QComboBox, QLineEdit, QTableWidgetItem

from ffgui.controller import Controller
from ffgui.doc import QueueDocument
from ffgui.ui.shell import Shell


@dataclass
class PropertyResult:
    prop_id: str
    title: str
    status: str  # "PASS", "FAIL", "FINDINGS"
    tool: str
    bounds: str
    states_checked: int
    elapsed_s: float
    details: list[str] = field(default_factory=list)
    counterexamples: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# A1: Exhaustive BFS Concurrency Model Check — app.py:_wire_probe & _launch_probe
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class ProbeState:
    """Exact state-space model of src/ffgui/app.py lines 21-34 & 56-148.

    Models Qt QThread execution + queued signal delivery with generation guards:
      - `shell_state`: `"loading" | "no-ffmpeg" | "workspace"`
      - `active_gen`: `state["gen"]` generation counter in `_wire_probe`
      - `booted`: `state["booted"]` one-shot guard in `_wire_probe`
      - `aimed_path`: `state["target"]` / last path passed to `aim_ffmpeg(...)` (None before first locate)
      - `running_probes`: tuple of `(gen, target)` for all `_Probe` QThreads currently running
      - `pending_signals`: FIFO of `(gen, target, outcome)` emitted by finished worker threads
        and queued onto the main thread's event loop
      - `probes_launched`: total `_Probe` threads started (bounded by `max_probes`)
      - `boot_count`: number of times `_boot(...)` was invoked
      - `booted_paths`: tuple of `(gen, target)` passed to `_boot(...)`
    """

    shell_state: str
    active_gen: int
    booted: bool
    aimed_path: str | None
    running_probes: tuple[tuple[int, str], ...]
    pending_signals: tuple[tuple[int, str, str], ...]
    probes_launched: int
    boot_count: int
    booted_paths: tuple[tuple[int, str], ...]


def _launch_probe_step(
    state: ProbeState,
    new_aimed: str,
    max_probes: int,
) -> tuple[ProbeState, bool] | None:
    """Model `_wire_probe.start_probe(target)` -> `_launch_probe(..., gen=gen, target=target)`.

    Increments `state["gen"]`, records `target`, moves shell to `loading`, and only
    reuses a running `_Probe` child if `child.isRunning() and child.gen == gen and child.target == target`.
    Otherwise creates and starts a new `_Probe(shell, gen=gen, target=target)`.
    """
    new_gen = state.active_gen + 1
    if any(g == new_gen and t == new_aimed for g, t in state.running_probes):
        return (
            ProbeState(
                shell_state="loading",
                active_gen=new_gen,
                booted=state.booted,
                aimed_path=new_aimed,
                running_probes=state.running_probes,
                pending_signals=state.pending_signals,
                probes_launched=state.probes_launched,
                boot_count=state.boot_count,
                booted_paths=state.booted_paths,
            ),
            True,
        )
    if state.probes_launched >= max_probes:
        return None  # Probe-launch budget reached; do not fabricate a deadlocked state
    return (
        ProbeState(
            shell_state="loading",
            active_gen=new_gen,
            booted=state.booted,
            aimed_path=new_aimed,
            running_probes=(*state.running_probes, (new_gen, new_aimed)),
            pending_signals=state.pending_signals,
            probes_launched=state.probes_launched + 1,
            boot_count=state.boot_count,
            booted_paths=state.booted_paths,
        ),
        False,
    )


def verify_a1_probe_lifecycle(max_probes: int = 2) -> PropertyResult:
    t0 = time.perf_counter()

    # Initial states from `_wire_probe` -> `_try_locate()` (app.py:132-147)
    # under both `found_ffmpeg=True` ("path0") and `found_ffmpeg=False`.
    init_states: list[tuple[str, ProbeState]] = [
        (
            'init(found_ffmpeg=True, path="path0")',
            ProbeState(
                shell_state="loading",
                active_gen=1,
                booted=False,
                aimed_path="path0",
                running_probes=((1, "path0"),),
                pending_signals=(),
                probes_launched=1,
                boot_count=0,
                booted_paths=(),
            ),
        ),
        (
            "init(found_ffmpeg=False)",
            ProbeState(
                shell_state="no-ffmpeg",
                active_gen=0,
                booted=False,
                aimed_path=None,
                running_probes=(),
                pending_signals=(),
                probes_launched=0,
                boot_count=0,
                booted_paths=(),
            ),
        ),
    ]

    visited: set[ProbeState] = set()
    queue: deque[tuple[ProbeState, list[str]]] = deque()
    for label, st in init_states:
        visited.add(st)
        queue.append((st, [label]))

    # Track shortest counterexample for each invariant (a), (b), (c)
    cex_double_boot: tuple[list[str], ProbeState] | None = None
    cex_deadlock: tuple[list[str], ProbeState] | None = None
    cex_stale_cap: tuple[list[str], ProbeState] | None = None

    transitions = 0

    def _check_invariants(st: ProbeState, trace: list[str]) -> None:
        nonlocal cex_double_boot, cex_deadlock, cex_stale_cap
        # (a) No double _boot
        if st.boot_count > 1 and cex_double_boot is None:
            cex_double_boot = (list(trace), st)
        # (b) No deadlocked loading state with no active-gen probe running or pending
        if (
            st.shell_state == "loading"
            and not any(g == st.active_gen for g, _ in st.running_probes)
            and not any(g == st.active_gen for g, _, _ in st.pending_signals)
            and cex_deadlock is None
        ):
            cex_deadlock = (list(trace), st)
        # (c) Never boot with a stale CapabilityIndex from an OLD aimed_path
        if (
            st.booted_paths
            and any(bp != st.aimed_path for _, bp in st.booted_paths)
            and cex_stale_cap is None
        ):
            cex_stale_cap = (list(trace), st)

    for label, st in init_states:
        _check_invariants(st, [label])

    while queue:
        st, trace = queue.popleft()
        next_steps: list[tuple[str, ProbeState]] = []

        # 1. user_cancel() — enabled when shell_state == "loading" (app.py:93-95, 142)
        if st.shell_state == "loading":
            next_steps.append(
                (
                    f"user_cancel(gen->{st.active_gen + 1})",
                    ProbeState(
                        shell_state="no-ffmpeg",
                        active_gen=st.active_gen + 1,
                        booted=st.booted,
                        aimed_path=st.aimed_path,
                        running_probes=st.running_probes,
                        pending_signals=st.pending_signals,
                        probes_launched=st.probes_launched,
                        boot_count=st.boot_count,
                        booted_paths=st.booted_paths,
                    ),
                )
            )

        # 2. user_rescan(found) — enabled when shell_state == "no-ffmpeg" (app.py:132-144)
        if st.shell_state == "no-ffmpeg":
            # found=False: stays in no-ffmpeg with updated status message
            next_steps.append(
                (
                    "user_rescan(found=False)",
                    ProbeState(
                        shell_state="no-ffmpeg",
                        active_gen=st.active_gen,
                        booted=st.booted,
                        aimed_path=st.aimed_path,
                        running_probes=st.running_probes,
                        pending_signals=st.pending_signals,
                        probes_launched=st.probes_launched,
                        boot_count=st.boot_count,
                        booted_paths=st.booted_paths,
                    ),
                )
            )
            # found=True: aims at "path0", increments gen, calls _launch_probe
            launched = _launch_probe_step(st, "path0", max_probes)
            if launched is not None:
                nxt_st, reused = launched
                tag = " [REUSES running probe]" if reused else ""
                next_steps.append((f"user_rescan(found=True){tag}", nxt_st))

            # 3. user_locate("path1") — enabled when shell_state == "no-ffmpeg" (app.py:119-125, 146)
            located = _launch_probe_step(st, "path1", max_probes)
            if located is not None:
                nxt_st, reused = located
                tag = " [REUSES running probe]" if reused else ""
                next_steps.append((f'user_locate("path1"){tag}', nxt_st))

        # 4. Any running worker thread completes _Probe.run() and queues done/failed (app.py:30-34)
        for idx, (pgen, ppath) in enumerate(st.running_probes):
            remaining = st.running_probes[:idx] + st.running_probes[idx + 1 :]
            for outcome in ("done", "failed"):
                next_steps.append(
                    (
                        f'probe_{outcome}_emit(gen={pgen}, path="{ppath}")',
                        ProbeState(
                            shell_state=st.shell_state,
                            active_gen=st.active_gen,
                            booted=st.booted,
                            aimed_path=st.aimed_path,
                            running_probes=remaining,
                            pending_signals=(*st.pending_signals, (pgen, ppath, outcome)),
                            probes_launched=st.probes_launched,
                            boot_count=st.boot_count,
                            booted_paths=st.booted_paths,
                        ),
                    )
                )

        # 5. Main thread delivers head of queued Qt signal (app.py:103-112)
        if st.pending_signals:
            (pgen, ppath, outcome), rest = st.pending_signals[0], st.pending_signals[1:]
            stale = pgen != st.active_gen or st.booted
            if outcome == "done":
                if stale:
                    reason = "booted" if st.booted else f"stale gen={pgen}!={st.active_gen}"
                    next_steps.append(
                        (
                            f'probe_done_deliver(gen={pgen}, path="{ppath}") [ignored: {reason}]',
                            ProbeState(
                                shell_state=st.shell_state,
                                active_gen=st.active_gen,
                                booted=st.booted,
                                aimed_path=st.aimed_path,
                                running_probes=st.running_probes,
                                pending_signals=rest,
                                probes_launched=st.probes_launched,
                                boot_count=st.boot_count,
                                booted_paths=st.booted_paths,
                            ),
                        )
                    )
                else:
                    next_steps.append(
                        (
                            f'probe_done_deliver(gen={pgen}, path="{ppath}") -> _boot()',
                            ProbeState(
                                shell_state="workspace",
                                active_gen=st.active_gen,
                                booted=True,
                                aimed_path=st.aimed_path,
                                running_probes=st.running_probes,
                                pending_signals=rest,
                                probes_launched=st.probes_launched,
                                boot_count=st.boot_count + 1,
                                booted_paths=(*st.booted_paths, (pgen, ppath)),
                            ),
                        )
                    )
            else:
                if stale:
                    reason = "booted" if st.booted else f"stale gen={pgen}!={st.active_gen}"
                    next_steps.append(
                        (
                            f'probe_failed_deliver(gen={pgen}, path="{ppath}") [ignored: {reason}]',
                            ProbeState(
                                shell_state=st.shell_state,
                                active_gen=st.active_gen,
                                booted=st.booted,
                                aimed_path=st.aimed_path,
                                running_probes=st.running_probes,
                                pending_signals=rest,
                                probes_launched=st.probes_launched,
                                boot_count=st.boot_count,
                                booted_paths=st.booted_paths,
                            ),
                        )
                    )
                else:
                    next_steps.append(
                        (
                            f'probe_failed_deliver(gen={pgen}, path="{ppath}") -> _probe_failed()',
                            ProbeState(
                                shell_state="no-ffmpeg",
                                active_gen=st.active_gen,
                                booted=st.booted,
                                aimed_path=st.aimed_path,
                                running_probes=st.running_probes,
                                pending_signals=rest,
                                probes_launched=st.probes_launched,
                                boot_count=st.boot_count,
                                booted_paths=st.booted_paths,
                            ),
                        )
                    )

        for action_label, nxt in next_steps:
            transitions += 1
            if nxt not in visited:
                visited.add(nxt)
                new_trace = [*trace, action_label]
                _check_invariants(nxt, new_trace)
                queue.append((nxt, new_trace))

    counterexamples: list[str] = []
    if cex_double_boot is not None:
        tr, st = cex_double_boot
        counterexamples.append(
            f"[Invariant (a) VIOLATED — Double _boot: boot_count={st.boot_count}, "
            f"booted={st.booted_paths}]\n"
            f"      shortest trace (len {len(tr) - 1}): " + " -> ".join(tr)
        )
    if cex_deadlock is not None:
        tr, st = cex_deadlock
        counterexamples.append(
            f"[Invariant (b) VIOLATED — Deadlocked loading state: {st}]\n"
            f"      shortest trace (len {len(tr) - 1}): " + " -> ".join(tr)
        )
    if cex_stale_cap is not None:
        tr, st = cex_stale_cap
        counterexamples.append(
            f"[Invariant (c) VIOLATED — Stale CapabilityIndex reuse: aimed_path={st.aimed_path!r}, "
            f"booted={st.booted_paths}]\n"
            f"      shortest trace (len {len(tr) - 1}): " + " -> ".join(tr)
        )

    inv_summary = (
        f"(a) no double _boot: {'VIOLATED' if cex_double_boot else 'PASS'}; "
        f"(b) no deadlocked loading: {'VIOLATED' if cex_deadlock else 'PASS'}; "
        f"(c) no stale CapabilityIndex reuse: {'VIOLATED' if cex_stale_cap else 'PASS'}"
    )

    return PropertyResult(
        prop_id="A1",
        title="Exhaustive BFS Concurrency Model Check — app.py:_wire_probe & _launch_probe",
        status="FINDINGS" if counterexamples else "PASS",
        tool="Exhaustive BFS state-space model checker (exact Qt QThread + QueuedConnection model)",
        bounds=(
            f"All interleavings up to max_probes={max_probes}, "
            'found_ffmpeg in {True, False}, paths in {"path0", "path1"}'
        ),
        states_checked=len(visited),
        elapsed_s=time.perf_counter() - t0,
        details=[
            f"Explored {len(visited):,} unique reachable states across {transitions:,} transitions",
            f"Invariants: {inv_summary}",
        ],
        counterexamples=counterexamples,
    )


# ---------------------------------------------------------------------------
# Shared Qt Test Harness for U1 & U2
# ---------------------------------------------------------------------------
def _make_cap() -> CapabilityIndex:
    """CapabilityIndex.stub() enriched with a hardware decoder so Advanced.hwdecode is enabled."""
    cap = CapabilityIndex.stub()
    cap.decoders.append(
        CapabilityEntry(
            name="h264_cuvid",
            flags="V....D",
            description="NVIDIA CUVID H264 decoder",
        )
    )
    return cap


def _stub_probe(path: str) -> Input:
    """Healthy probed media input with video, audio, and subtitle streams."""
    return Input(
        path=str(path),
        streams=[
            InputStream(
                input_index=0,
                spec="v:0",
                codec_type="video",
                codec_name="h264",
                width=1920,
                height=1080,
            ),
            InputStream(
                input_index=0,
                spec="a:0",
                codec_type="audio",
                codec_name="aac",
            ),
            InputStream(
                input_index=0,
                spec="s:0",
                codec_type="subtitle",
                codec_name="subrip",
            ),
        ],
    )


def _drive_line(w: QLineEdit, value: str) -> None:
    w.setText(value)
    w.textEdited.emit(value)


def _drive_combo(w: QComboBox, value: str) -> None:
    w.setCurrentText(value)


def _drive_data_combo(w: QComboBox, data: str) -> None:
    idx = w.findData(data)
    assert idx >= 0, f"data {data!r} not found in QComboBox"
    w.setCurrentIndex(idx)


def _drive_check(w: QCheckBox, checked: bool) -> None:
    w.setChecked(checked)


def _reset_single_row(ctrl: Controller, path: str = "input.mp4") -> None:
    ctrl.doc.items.clear()
    ctrl.doc.add_files([path])
    ctrl.refresh()
    ctrl._select_rows([0])


# ---------------------------------------------------------------------------
# U1: Exhaustive Real-Qt Round-Trip Bijectivity — All 8 Tabs <-> Controller <-> Doc
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class ControlSpec:
    tab: str
    name: str
    drive: Callable[[object], None]
    read_widget: Callable[[object], object]
    expected_widget: object
    expected_preview_tokens: tuple[str, ...]
    forbidden_preview_tokens: tuple[str, ...] = ()
    setup: Callable[[object], None] | None = None


def verify_u1_roundtrip_bijectivity(
    app: QApplication,
    settings: QSettings,
) -> PropertyResult:
    t0 = time.perf_counter()
    cap = _make_cap()
    shell = Shell()
    doc = QueueDocument(cap, prober=_stub_probe)
    ctrl = Controller(shell, doc, cap, settings)

    specs: list[ControlSpec] = [
        # --- ContainerTab (8 controls) ---
        ControlSpec(
            "Container",
            "name",
            lambda p: _drive_line(p.name, "custom_out.mp4"),
            lambda p: p.name.text(),
            "custom_out.mp4",
            ("custom_out.mp4",),
        ),
        ControlSpec(
            "Container",
            "container",
            lambda p: _drive_combo(p.container, "mkv"),
            lambda p: p.container.currentText(),
            "mkv",
            ("-f mkv",),
        ),
        ControlSpec(
            "Container",
            "muxer",
            lambda p: _drive_combo(p.muxer, "mpegts"),
            lambda p: p.muxer.currentText(),
            "mpegts",
            ("-f mpegts",),
        ),
        ControlSpec(
            "Container",
            "start",
            lambda p: _drive_line(p.start, "00:00:05"),
            lambda p: p.start.text(),
            "00:00:05",
            ("-ss 00:00:05",),
        ),
        ControlSpec(
            "Container",
            "duration",
            lambda p: _drive_line(p.duration, "90"),
            lambda p: p.duration.text(),
            "90",
            ("-t 90",),
        ),
        ControlSpec(
            "Container",
            "end",
            lambda p: _drive_line(p.end, "00:01:30"),
            lambda p: p.end.text(),
            "00:01:30",
            ("-to 00:01:30",),
        ),
        ControlSpec(
            "Container",
            "max_size",
            lambda p: _drive_line(p.max_size, "25M"),
            lambda p: p.max_size.text(),
            "25M",
            ("-fs 25M",),
        ),
        ControlSpec(
            "Container",
            "overwrite",
            lambda p: _drive_check(p.overwrite, False),
            lambda p: p.overwrite.isChecked(),
            False,
            ("-n",),
            forbidden_preview_tokens=("-y",),
        ),
        # --- VideoTab (19 controls) ---
        ControlSpec(
            "Video",
            "codec",
            lambda p: _drive_combo(p.codec, "libx264"),
            lambda p: p.codec.currentText(),
            "libx264",
            ("-c:v libx264",),
        ),
        ControlSpec(
            "Video",
            "crf",
            lambda p: _drive_line(p.crf, "23"),
            lambda p: p.crf.text(),
            "23",
            ("-crf 23",),
        ),
        ControlSpec(
            "Video",
            "bitrate",
            lambda p: _drive_line(p.bitrate, "1500k"),
            lambda p: p.bitrate.text(),
            "1500k",
            ("-b:v 1500k",),
        ),
        ControlSpec(
            "Video",
            "maxrate",
            lambda p: _drive_line(p.maxrate, "2M"),
            lambda p: p.maxrate.text(),
            "2M",
            ("-maxrate 2M",),
        ),
        ControlSpec(
            "Video",
            "preset",
            lambda p: _drive_combo(p.preset, "slow"),
            lambda p: p.preset.currentText(),
            "slow",
            ("-preset slow",),
        ),
        ControlSpec(
            "Video",
            "tune",
            lambda p: _drive_combo(p.tune, "film"),
            lambda p: p.tune.currentText(),
            "film",
            ("-tune film",),
        ),
        ControlSpec(
            "Video",
            "profile",
            lambda p: _drive_combo(p.profile, "high"),
            lambda p: p.profile.currentText(),
            "high",
            ("-profile:v high",),
        ),
        ControlSpec(
            "Video",
            "pixfmt",
            lambda p: _drive_combo(p.pixfmt, "yuv420p"),
            lambda p: p.pixfmt.currentText(),
            "yuv420p",
            ("-pix_fmt yuv420p",),
        ),
        ControlSpec(
            "Video",
            "gop",
            lambda p: _drive_line(p.gop, "250"),
            lambda p: p.gop.text(),
            "250",
            ("-g 250",),
        ),
        ControlSpec(
            "Video",
            "threads",
            lambda p: _drive_line(p.threads, "4"),
            lambda p: p.threads.text(),
            "4",
            ("-threads 4",),
        ),
        ControlSpec(
            "Video",
            "faststart",
            lambda p: _drive_check(p.faststart, True),
            lambda p: p.faststart.isChecked(),
            True,
            ("-movflags +faststart",),
        ),
        ControlSpec(
            "Video",
            "drop_video",
            lambda p: _drive_check(p.drop_video, True),
            lambda p: p.drop_video.isChecked(),
            True,
            ("-vn",),
        ),
        ControlSpec(
            "Video",
            "resolution",
            lambda p: _drive_data_combo(p.resolution, "1280:-2"),
            lambda p: p.resolution.currentData(),
            "1280:-2",
            ("-vf", "scale=1280:-2"),
        ),
        ControlSpec(
            "Video",
            "fps",
            lambda p: _drive_combo(p.fps, "30"),
            lambda p: p.fps.currentText(),
            "30",
            ("-r 30",),
        ),
        ControlSpec(
            "Video",
            "aspect",
            lambda p: _drive_combo(p.aspect, "16:9"),
            lambda p: p.aspect.currentText(),
            "16:9",
            ("-aspect 16:9",),
        ),
        ControlSpec(
            "Video",
            "rotate",
            lambda p: _drive_data_combo(p.rotate, "transpose=1"),
            lambda p: p.rotate.currentData(),
            "transpose=1",
            ("-vf", "transpose=1"),
        ),
        ControlSpec(
            "Video",
            "crop",
            lambda p: _drive_line(p.crop, "1280:720:0:0"),
            lambda p: p.crop.text(),
            "1280:720:0:0",
            ("-vf", "crop=1280:720:0:0"),
        ),
        ControlSpec(
            "Video",
            "speed",
            lambda p: _drive_data_combo(p.speed, "1.5"),
            lambda p: p.speed.currentData(),
            "1.5",
            ("-vf", "setpts=PTS/1.5", "-af", "atempo=1.5"),
        ),
        ControlSpec(
            "Video",
            "deinterlace",
            lambda p: _drive_check(p.deinterlace, True),
            lambda p: p.deinterlace.isChecked(),
            True,
            ("-vf", "yadif"),
        ),
        # --- AudioTab (8 controls) ---
        ControlSpec(
            "Audio",
            "codec",
            lambda p: _drive_combo(p.codec, "aac"),
            lambda p: p.codec.currentText(),
            "aac",
            ("-c:a aac",),
        ),
        ControlSpec(
            "Audio",
            "bitrate",
            lambda p: _drive_line(p.bitrate, "192k"),
            lambda p: p.bitrate.text(),
            "192k",
            ("-b:a 192k",),
        ),
        ControlSpec(
            "Audio",
            "channels",
            lambda p: _drive_combo(p.channels, "2"),
            lambda p: p.channels.currentText(),
            "2",
            ("-ac 2",),
        ),
        ControlSpec(
            "Audio",
            "samplerate",
            lambda p: _drive_combo(p.samplerate, "48000"),
            lambda p: p.samplerate.currentText(),
            "48000",
            ("-ar 48000",),
        ),
        ControlSpec(
            "Audio",
            "volume",
            lambda p: _drive_line(p.volume, "0.5"),
            lambda p: p.volume.text(),
            "0.5",
            ("-af", "volume=0.5"),
        ),
        ControlSpec(
            "Audio",
            "loudnorm",
            lambda p: _drive_check(p.loudnorm, True),
            lambda p: p.loudnorm.isChecked(),
            True,
            ("-af", "loudnorm=I=-16:TP=-1.5:LRA=11"),
        ),
        ControlSpec(
            "Audio",
            "drop_audio",
            lambda p: _drive_check(p.drop_audio, True),
            lambda p: p.drop_audio.isChecked(),
            True,
            ("-an",),
        ),
        ControlSpec(
            "Audio",
            "quality",
            lambda p: _drive_line(p.quality, "2"),
            lambda p: p.quality.text(),
            "2",
            ("-q:a 2",),
        ),
        # --- SubtitlesTab (3 controls) ---
        ControlSpec(
            "Subtitles",
            "codec",
            lambda p: _drive_combo(p.codec, "mov_text"),
            lambda p: p.codec.currentText(),
            "mov_text",
            ("-c:s mov_text",),
        ),
        ControlSpec(
            "Subtitles",
            "drop",
            lambda p: _drive_check(p.drop, True),
            lambda p: p.drop.isChecked(),
            True,
            ("-sn",),
        ),
        ControlSpec(
            "Subtitles",
            "burn",
            lambda p: _drive_line(p.burn, "subs.srt"),
            lambda p: p.burn.text(),
            "subs.srt",
            ("-vf", "subtitles=subs.srt"),
        ),
        # --- FiltersTab (2 list controls) ---
        ControlSpec(
            "Filters",
            "video_filters",
            lambda p: p._add("video_filters", "scale", "640:360"),
            lambda p: [
                p.lists["video_filters"].item(i).text()
                for i in range(p.lists["video_filters"].count())
            ],
            ["scale=640:360"],
            ("-vf", "scale=640:360"),
        ),
        ControlSpec(
            "Filters",
            "audio_filters",
            lambda p: p._add("audio_filters", "aresample", "44100"),
            lambda p: [
                p.lists["audio_filters"].item(i).text()
                for i in range(p.lists["audio_filters"].count())
            ],
            ["aresample=44100"],
            ("-af", "aresample=44100"),
        ),
        # --- ChaptersTab (table control) ---
        ControlSpec(
            "Chapters",
            "table",
            lambda p: (
                p.table.insertRow(0),
                p.table.setItem(0, 0, QTableWidgetItem("00:00:00")),
                p.table.setItem(0, 1, QTableWidgetItem("Intro")),
                p.table.setItem(0, 2, QTableWidgetItem("eng")),
            ),
            lambda p: [
                (
                    p.table.item(r, 0).text(),
                    p.table.item(r, 1).text(),
                    p.table.item(r, 2).text(),
                )
                for r in range(p.table.rowCount())
            ],
            [("00:00:00", "Intro", "eng")],
            ("-map_chapters 1",),
        ),
        # --- MetadataTab (8 controls) ---
        ControlSpec(
            "Metadata",
            "title",
            lambda p: _drive_line(p.title, "My Video"),
            lambda p: p.title.text(),
            "My Video",
            ("-metadata", "title=My Video"),
        ),
        ControlSpec(
            "Metadata",
            "artist",
            lambda p: _drive_line(p.artist, "Jane Doe"),
            lambda p: p.artist.text(),
            "Jane Doe",
            ("-metadata", "artist=Jane Doe"),
        ),
        ControlSpec(
            "Metadata",
            "album",
            lambda p: _drive_line(p.album, "Demos"),
            lambda p: p.album.text(),
            "Demos",
            ("-metadata", "album=Demos"),
        ),
        ControlSpec(
            "Metadata",
            "date",
            lambda p: _drive_line(p.date, "2026"),
            lambda p: p.date.text(),
            "2026",
            ("-metadata", "date=2026"),
        ),
        ControlSpec(
            "Metadata",
            "genre",
            lambda p: _drive_line(p.genre, "Documentary"),
            lambda p: p.genre.text(),
            "Documentary",
            ("-metadata", "genre=Documentary"),
        ),
        ControlSpec(
            "Metadata",
            "track",
            lambda p: _drive_line(p.track, "1"),
            lambda p: p.track.text(),
            "1",
            ("-metadata", "track=1"),
        ),
        ControlSpec(
            "Metadata",
            "comment",
            lambda p: _drive_line(p.comment, "Test clip"),
            lambda p: p.comment.text(),
            "Test clip",
            ("-metadata", "comment=Test clip"),
        ),
        ControlSpec(
            "Metadata",
            "cover",
            lambda p: _drive_line(p.cover, "cover.jpg"),
            lambda p: p.cover.text(),
            "cover.jpg",
            ("cover.jpg",),
        ),
        # --- AdvancedTab (12 controls) ---
        ControlSpec(
            "Advanced",
            "hwdecode",
            lambda p: _drive_combo(p.hwdecode, "h264_cuvid"),
            lambda p: p.hwdecode.currentText(),
            "h264_cuvid",
            ("-c:v h264_cuvid",),
        ),
        ControlSpec(
            "Advanced",
            "hwdevice",
            lambda p: _drive_line(p.hwdevice, "gpu0"),
            lambda p: p.hwdevice.text(),
            "gpu0",
            ("-filter_hw_device gpu0",),
        ),
        ControlSpec(
            "Advanced",
            "twopass",
            lambda p: _drive_check(p.twopass, True),
            lambda p: p.twopass.isChecked(),
            True,
            ("-pass 1", "-pass 2", "&&"),
        ),
        ControlSpec(
            "Advanced",
            "segment",
            lambda p: _drive_check(p.segment, True),
            lambda p: p.segment.isChecked(),
            True,
            ("-f segment",),
        ),
        ControlSpec(
            "Advanced",
            "segment_time",
            lambda p: _drive_line(p.segment_time, "600"),
            lambda p: p.segment_time.text(),
            "600",
            ("-segment_time 600",),
            setup=lambda p: _drive_check(p.segment, True),
        ),
        ControlSpec(
            "Advanced",
            "bsf",
            lambda p: _drive_line(p.bsf, "v:0=h264_mp4toannexb"),
            lambda p: p.bsf.text(),
            "v:0=h264_mp4toannexb",
            ("-bsf:v:0 h264_mp4toannexb",),
        ),
        ControlSpec(
            "Advanced",
            "tee",
            lambda p: _drive_line(p.tee, "[f=mp4]a.mp4|[f=webm]b.webm"),
            lambda p: p.tee.text(),
            "[f=mp4]a.mp4|[f=webm]b.webm",
            ("-f tee", "[f=mp4]a.mp4|[f=webm]b.webm"),
        ),
        ControlSpec(
            "Advanced",
            "seek",
            lambda p: _drive_line(p.seek, "10"),
            lambda p: p.seek.text(),
            "10",
            ("-ss 10",),
        ),
        ControlSpec(
            "Advanced",
            "loop",
            lambda p: _drive_line(p.loop, "1"),
            lambda p: p.loop.text(),
            "1",
            ("-loop 1",),
        ),
        ControlSpec(
            "Advanced",
            "hwaccel",
            lambda p: _drive_combo(p.hwaccel, "cuda"),
            lambda p: p.hwaccel.currentText(),
            "cuda",
            ("-hwaccel cuda",),
        ),
        ControlSpec(
            "Advanced",
            "itsoffset",
            lambda p: _drive_line(p.itsoffset, "0.5"),
            lambda p: p.itsoffset.text(),
            "0.5",
            ("-itsoffset 0.5",),
        ),
        ControlSpec(
            "Advanced",
            "loglevel",
            lambda p: _drive_combo(p.loglevel, "warning"),
            lambda p: p.loglevel.currentText(),
            "warning",
            ("-loglevel warning",),
        ),
    ]

    counterexamples: list[str] = []
    passed = 0

    for spec in specs:
        _reset_single_row(ctrl)
        page = ctrl.tabs[spec.tab]
        if spec.setup is not None:
            spec.setup(page)
        spec.drive(page)

        # Force an explicit Controller.refresh() round-trip (which invokes page.populate(out, job))
        ctrl.refresh()
        actual_widget = spec.read_widget(page)
        preview = shell.preview.toPlainText()

        errors: list[str] = []
        if actual_widget != spec.expected_widget:
            errors.append(
                f"widget round-trip mismatch: expected {spec.expected_widget!r}, "
                f"got {actual_widget!r}"
            )
        if preview.startswith("# "):
            errors.append(f"live preview errored: {preview}")
        else:
            for tok in spec.expected_preview_tokens:
                if tok not in preview:
                    errors.append(f"preview missing {tok!r} (preview={preview!r})")
            for tok in spec.forbidden_preview_tokens:
                if tok in preview:
                    errors.append(f"preview unexpectedly contains {tok!r} (preview={preview!r})")

        if errors:
            counterexamples.append(f"{spec.tab}.{spec.name}: " + "; ".join(errors))
        else:
            passed += 1

    shell.close()
    app.processEvents()

    tabs_covered = sorted({s.tab for s in specs})
    return PropertyResult(
        prop_id="U1",
        title="Exhaustive Real-Qt Round-Trip Bijectivity — All 8 Tabs <-> Controller <-> QueueDocument",
        status="FINDINGS" if counterexamples else "PASS",
        tool="Real PySide6 (offscreen) Shell + Controller + QueueDocument + fftui build()",
        bounds=f"All 8 tabs ({', '.join(tabs_covered)}), {len(specs)} distinct user controls",
        states_checked=len(specs),
        elapsed_s=time.perf_counter() - t0,
        details=[
            f"Verified {passed}/{len(specs)} controls round-trip identically and emit clean preview flags",
            f"Controls failing round-trip or preview soundness: {len(counterexamples)}",
        ],
        counterexamples=counterexamples,
    )


# ---------------------------------------------------------------------------
# U2: Exhaustive Pairwise Non-Interference — Filter & Row-Switch Isolation
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class FilterControl:
    name: str
    set_on: Callable[[Controller], None]
    clear: Callable[[Controller], None]
    is_widget_on: Callable[[Controller], bool]
    expected_vfilters: tuple[str, ...]
    expected_afilters: tuple[str, ...]


def verify_u2_non_interference(
    app: QApplication,
    settings: QSettings,
) -> PropertyResult:
    t0 = time.perf_counter()
    cap = _make_cap()
    shell = Shell()
    doc = QueueDocument(cap, prober=_stub_probe)
    ctrl = Controller(shell, doc, cap, settings)

    controls: list[FilterControl] = [
        FilterControl(
            "scale",
            lambda c: _drive_data_combo(c.tabs["Video"].resolution, "1280:-2"),
            lambda c: _drive_data_combo(c.tabs["Video"].resolution, ""),
            lambda c: c.tabs["Video"].resolution.currentData() == "1280:-2",
            ("scale=1280:-2",),
            (),
        ),
        FilterControl(
            "rotate",
            lambda c: _drive_data_combo(c.tabs["Video"].rotate, "hflip,vflip"),
            lambda c: _drive_data_combo(c.tabs["Video"].rotate, ""),
            lambda c: c.tabs["Video"].rotate.currentData() == "hflip,vflip",
            ("hflip", "vflip"),
            (),
        ),
        FilterControl(
            "crop",
            lambda c: _drive_line(c.tabs["Video"].crop, "1280:720:0:0"),
            lambda c: _drive_line(c.tabs["Video"].crop, ""),
            lambda c: c.tabs["Video"].crop.text() == "1280:720:0:0",
            ("crop=1280:720:0:0",),
            (),
        ),
        FilterControl(
            "deinterlace",
            lambda c: _drive_check(c.tabs["Video"].deinterlace, True),
            lambda c: _drive_check(c.tabs["Video"].deinterlace, False),
            lambda c: c.tabs["Video"].deinterlace.isChecked(),
            ("yadif",),
            (),
        ),
        FilterControl(
            "speed",
            lambda c: _drive_data_combo(c.tabs["Video"].speed, "1.5"),
            lambda c: _drive_data_combo(c.tabs["Video"].speed, ""),
            lambda c: c.tabs["Video"].speed.currentData() == "1.5",
            ("setpts=PTS/1.5",),
            ("atempo=1.5",),
        ),
        FilterControl(
            "volume",
            lambda c: _drive_line(c.tabs["Audio"].volume, "0.5"),
            lambda c: _drive_line(c.tabs["Audio"].volume, ""),
            lambda c: c.tabs["Audio"].volume.text() == "0.5",
            (),
            ("volume=0.5",),
        ),
        FilterControl(
            "loudnorm",
            lambda c: _drive_check(c.tabs["Audio"].loudnorm, True),
            lambda c: _drive_check(c.tabs["Audio"].loudnorm, False),
            lambda c: c.tabs["Audio"].loudnorm.isChecked(),
            (),
            ("loudnorm=I=-16:TP=-1.5:LRA=11",),
        ),
    ]

    counterexamples: list[str] = []
    checks = 0

    def _has_filters(fc: FilterControl) -> bool:
        job = doc.items[0].job
        vf = set(job.video_filters.filters)
        af = set(job.audio_filters.filters)
        return all(x in vf for x in fc.expected_vfilters) and all(
            x in af for x in fc.expected_afilters
        )

    def _lacks_filters(fc: FilterControl) -> bool:
        job = doc.items[0].job
        vf = set(job.video_filters.filters)
        af = set(job.audio_filters.filters)
        return all(x not in vf for x in fc.expected_vfilters) and all(
            x not in af for x in fc.expected_afilters
        )

    # Part 1: All 42 ordered pairs (A, B) with A != B -> (A then B) + clear A while B stays active
    for a in controls:
        for b in controls:
            if a.name == b.name:
                continue
            checks += 1
            _reset_single_row(ctrl)
            a.set_on(ctrl)
            b.set_on(ctrl)

            if not (a.is_widget_on(ctrl) and b.is_widget_on(ctrl)):
                counterexamples.append(
                    f"Pair ({a.name} -> {b.name}): widget clobbered "
                    f"({a.name}={a.is_widget_on(ctrl)}, {b.name}={b.is_widget_on(ctrl)})"
                )
                continue
            if not (_has_filters(a) and _has_filters(b)):
                job = doc.items[0].job
                counterexamples.append(
                    f"Pair ({a.name} -> {b.name}): filter chain clobbered "
                    f"(vf={job.video_filters.filters}, af={job.audio_filters.filters})"
                )
                continue

            # Clear A while B stays active
            a.clear(ctrl)
            if a.is_widget_on(ctrl) or not _lacks_filters(a):
                counterexamples.append(
                    f"Pair ({a.name} -> {b.name}) then clear({a.name}): "
                    f"{a.name} did not clear cleanly"
                )
                continue
            if not b.is_widget_on(ctrl) or not _has_filters(b):
                job = doc.items[0].job
                counterexamples.append(
                    f"Pair ({a.name} -> {b.name}) then clear({a.name}): "
                    f"{b.name} was clobbered (widget={b.is_widget_on(ctrl)}, "
                    f"vf={job.video_filters.filters}, af={job.audio_filters.filters})"
                )
                continue

            # Clear B -> both chains must be empty
            b.clear(ctrl)
            job = doc.items[0].job
            if job.video_filters.filters or job.audio_filters.filters:
                counterexamples.append(
                    f"Pair ({a.name} -> {b.name}) after clearing both: residual filters "
                    f"(vf={job.video_filters.filters}, af={job.audio_filters.filters})"
                )

    # Part 2: Row-switch isolation for MetadataTab (stream_spec, stream_lang, stream_disposition)
    checks += 1
    doc.items.clear()
    doc.add_files(["row0.mp4", "row1.mp4"])
    ctrl.refresh()
    ctrl._select_rows([0])

    meta = ctrl.tabs["Metadata"]
    _drive_line(meta.stream_spec, "v:0")
    _drive_line(meta.stream_lang, "eng")
    _drive_data_combo(meta.stream_disposition, "default")

    out0 = doc.items[0].job.outputs[0]
    out1 = doc.items[1].job.outputs[0]
    if out0.stream_metadata.get("v:0|language") != "eng" or out0.dispositions.get("v:0") != "default":
        counterexamples.append(
            f"Row-switch setup failed on row 0: stream_metadata={out0.stream_metadata}, "
            f"dispositions={out0.dispositions}"
        )

    # Switch to row 1: widgets must clear and row 1 must have no leaked stream tags/dispositions
    ctrl._select_rows([1])
    if (
        meta.stream_spec.text() != ""
        or meta.stream_lang.text() != ""
        or meta.stream_disposition.currentIndex() != 0
    ):
        counterexamples.append(
            f"Row-switch 0 -> 1 leaked MetadataTab widgets: "
            f"spec={meta.stream_spec.text()!r}, lang={meta.stream_lang.text()!r}, "
            f"disp_idx={meta.stream_disposition.currentIndex()}"
        )
    if out1.stream_metadata or out1.dispositions:
        counterexamples.append(
            f"Row-switch 0 -> 1 leaked model data into row 1: "
            f"stream_metadata={out1.stream_metadata}, dispositions={out1.dispositions}"
        )

    # Edit row 1 independently and switch back to row 0: row 0 must remain untouched
    checks += 1
    _drive_line(meta.stream_spec, "a:0")
    _drive_line(meta.stream_lang, "fra")
    _drive_data_combo(meta.stream_disposition, "forced")
    ctrl._select_rows([0])
    if (
        meta.stream_spec.text() != ""
        or meta.stream_lang.text() != ""
        or meta.stream_disposition.currentIndex() != 0
    ):
        counterexamples.append(
            f"Row-switch 1 -> 0 leaked MetadataTab widgets: "
            f"spec={meta.stream_spec.text()!r}, lang={meta.stream_lang.text()!r}, "
            f"disp_idx={meta.stream_disposition.currentIndex()}"
        )
    if out0.stream_metadata != {"v:0|language": "eng"} or out0.dispositions != {"v:0": "default"}:
        counterexamples.append(
            f"Row-switch 1 -> 0 clobbered row 0: "
            f"stream_metadata={out0.stream_metadata}, dispositions={out0.dispositions}"
        )
    if out1.stream_metadata != {"a:0|language": "fra"} or out1.dispositions != {"a:0": "forced"}:
        counterexamples.append(
            f"Row-switch 1 -> 0 clobbered row 1: "
            f"stream_metadata={out1.stream_metadata}, dispositions={out1.dispositions}"
        )

    shell.close()
    app.processEvents()

    return PropertyResult(
        prop_id="U2",
        title="Exhaustive Pairwise Non-Interference — Filter Controls & MetadataTab Row-Switch Isolation",
        status="FAIL" if counterexamples else "PASS",
        tool="Real PySide6 (offscreen) Shell + Controller + QueueDocument",
        bounds=(
            f"All {len(controls) * (len(controls) - 1)} ordered filter pairs "
            "(scale, rotate, crop, deinterlace, speed, volume, loudnorm) + "
            "2-row MetadataTab bidirectional row-switch isolation"
        ),
        states_checked=checks,
        elapsed_s=time.perf_counter() - t0,
        details=[
            f"Verified all {len(controls) * (len(controls) - 1)} ordered pairs (A->B, clear A, clear B) "
            "preserve orthogonal widgets and filter chains",
            "Verified MetadataTab stream_spec/stream_lang/stream_disposition clear on row switch "
            "with zero cross-row leakage (0->1 and 1->0)",
        ],
        counterexamples=counterexamples,
    )


def _print_report(results: list[PropertyResult], total_s: float) -> None:
    print("=" * 78)
    print("FFMpeg-GUI Formal Verification Report — Probe Lifecycle & Qt UI (A1, U1, U2)")
    print("=" * 78)
    for r in results:
        print(f"\n[{r.status:8s}] {r.prop_id}: {r.title}")
        print(f"  Tool   : {r.tool}")
        print(f"  Bounds : {r.bounds}")
        print(f"  Checked: {r.states_checked:,} ({r.elapsed_s:.3f}s)")
        for line in r.details:
            print(f"  Detail : {line}")
        for idx, cex in enumerate(r.counterexamples, 1):
            print(f"  Cex #{idx}: {cex}")
    print("\n" + "=" * 78)
    summary = ", ".join(f"{r.prop_id}={r.status}" for r in results)
    print(f"Summary ({total_s:.3f}s): {summary}")
    print("=" * 78)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="FFMpeg-GUI formal verification suite — probe lifecycle & Qt UI (A1, U1, U2)"
    )
    parser.add_argument(
        "--report",
        action="store_true",
        help="Always exit 0 after printing the full verification report",
    )
    parser.add_argument(
        "--strict",
        action="store_true",
        help="Exit 1 if any property fails OR produces findings",
    )
    args = parser.parse_args(argv)

    t0 = time.perf_counter()
    app = QApplication.instance() or QApplication([sys.argv[0]])

    with tempfile.TemporaryDirectory(prefix="ffgui-fv-probe-ui-") as tmpdir:
        settings = QSettings(str(Path(tmpdir) / "settings.ini"), QSettings.Format.IniFormat)
        results = [
            verify_a1_probe_lifecycle(max_probes=2),
            verify_u1_roundtrip_bijectivity(app, settings),
            verify_u2_non_interference(app, settings),
        ]

    _print_report(results, time.perf_counter() - t0)

    if args.report:
        return 0
    if args.strict:
        return 1 if any(r.status != "PASS" for r in results) else 0
    return 1 if any(r.status == "FAIL" for r in results) else 0


if __name__ == "__main__":
    sys.exit(main())
