"""ffgui's own stores: schema-2 queue/presets plus one-way TUI queue import.
Unparseable queue rows carry through untouched; a wholly unusable file — or one
preset entry this build cannot parse — is quarantined beside itself and empty."""

from __future__ import annotations

import hashlib
import json
import os
import threading
from dataclasses import dataclass
from pathlib import Path

from fftui.model import Job
from fftui.util.atomic import (
    is_schema_stamp,
    quarantine_or_raise,
    sweep_stale_temps,
    write_text_atomic,
)
from fftui.util.filelock import interprocess_lock

from ffgui.paths import app_dir, cache_dir

GUI_SCHEMA = 2
_META_KEYS = ("name", "enabled", "notes", "source_hash")
_META_DEFAULTS = {"name": "", "enabled": True, "notes": "", "source_hash": ""}
_QUEUE_LOCK = threading.RLock()


def gui_dirs() -> tuple[Path, Path]:
    """ffgui's own (cache, config) directories — never the TUI's ``fftui`` ones."""
    return app_dir("cache"), app_dir("config")


#: The one truthy set for toggle text ("True" from Qt signals, "yes"/"on"
#: pasted or restored). Leaf-owned: doc, controller, and the expert check
#: editor all share this function — never a re-spelled tuple.
TRUTHY = frozenset({"1", "true", "yes", "on"})


def is_truthy(value: object) -> bool:
    """Toggle text → bool under the single shared truthy set.

    Fail-closed on non-strings (None clears/off) so Qt slots never raise.
    """
    return isinstance(value, str) and value.strip().lower() in TRUTHY


def fresh_meta(name: str = "") -> dict:
    """The one default gui-meta shape; call sites must not re-spell it."""
    return {**_META_DEFAULTS, "name": name}


def _meta(raw: object) -> dict:
    """Fixed gui meta shape: missing keys default, unknown drop, hostile types coerce."""
    src = raw if isinstance(raw, dict) else {}
    meta = {k: src.get(k, _META_DEFAULTS[k]) for k in _META_KEYS}
    for key in ("name", "notes", "source_hash"):
        if not isinstance(meta[key], str):
            meta[key] = _META_DEFAULTS[key]
    enabled = meta["enabled"]
    if isinstance(enabled, str):

        meta["enabled"] = is_truthy(enabled)
    elif enabled is None:
        meta["enabled"] = _META_DEFAULTS["enabled"]
    elif isinstance(enabled, (bool, int, float)):
        meta["enabled"] = bool(enabled)
    else:


        meta["enabled"] = _META_DEFAULTS["enabled"]
    return meta


@dataclass
class UnparsedRow:
    """A row this build cannot turn into a ``Job``; carried through saves untouched."""

    raw: dict


Row = tuple[Job | UnparsedRow, dict]


def _queue_path(dir: str | os.PathLike[str] | None) -> Path:
    return cache_dir(dir) / "queue.json"


def _read_queue_raw(dir) -> list[dict]:
    path = _queue_path(dir)
    sweep_stale_temps(path)
    if not path.is_file():
        return []
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError, UnicodeDecodeError):
        quarantine_or_raise(path)
        return []
    if (isinstance(raw, dict) and set(raw) == {"schema", "rows"}
            and is_schema_stamp(raw["schema"]) and raw["schema"] == GUI_SCHEMA
            and isinstance(raw["rows"], list)
            and all(isinstance(e, dict) for e in raw["rows"])):
        return raw["rows"]
    quarantine_or_raise(path)
    return []


def load_queue(dir=None) -> list[Row]:
    """The queue as ``(Job | UnparsedRow, gui-meta)`` pairs in stored order."""
    out: list[Row] = []
    with _QUEUE_LOCK, interprocess_lock(_queue_path(dir).with_name("queue.json.lock")):
        for entry in _read_queue_raw(dir):
            meta = _meta(entry.get("gui"))
            raw = entry.get("job")
            try:
                job = Job.from_dict(raw) if isinstance(raw, dict) else None
            except Exception:
                job = None
            out.append((job if job is not None else UnparsedRow(raw), meta))
    return out


def _row_entry(row: Row) -> dict:
    job, meta = row
    if not isinstance(job, (Job, UnparsedRow)):
        raise ValueError(
            f"queue row must be a Job or UnparsedRow, got {type(job).__name__}")


    gui = _meta(meta)
    if isinstance(job, UnparsedRow):
        return {"job": job.raw, "gui": gui}
    return {"job": job.to_dict(), "gui": gui}


def save_queue(rows: list[Row], dir=None) -> None:
    """Persist the queue; an ``UnparsedRow`` is written back byte-equal, never dropped."""
    if not isinstance(rows, (list, tuple)):
        raise ValueError(f"queue rows must be a list, got {type(rows).__name__}")
    with _QUEUE_LOCK, interprocess_lock(_queue_path(dir).with_name("queue.json.lock")):
        write_text_atomic(_queue_path(dir), json.dumps(
            {"schema": GUI_SCHEMA, "rows": [_row_entry(r) for r in rows]}, indent=2))


def source_hash(raw: dict) -> str:
    """The content identity of a stored job dict (import idempotence key)."""
    return hashlib.sha256(json.dumps(raw, sort_keys=True).encode()).hexdigest()


# NOTE: no pre-filtering here — import_tui_queue reports non-object rows with
# accurate 1-based numbers, which filtering would shift.
def _tui_queue_rows(tui_dir) -> tuple[list, list[str]]:
    path = Path(tui_dir) / "queue.json" if tui_dir else None
    if path is None or not path.is_file():
        return [], []
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError, UnicodeDecodeError):
        return [], [f"{path}: unreadable TUI queue"]
    if isinstance(raw, list):
        return raw, []
    if (isinstance(raw, dict) and is_schema_stamp(raw.get("schema"))
            and isinstance(raw.get("jobs"), list)):
        return raw["jobs"], []
    return [], [f"{path}: not a TUI queue store"]


def _default_name(raw: dict) -> str:
    out = raw.get("outputs")
    ok = isinstance(out, list) and bool(out) and isinstance(out[0], dict)
    path = out[0].get("path", "") if ok else ""
    return Path(path).stem or "imported"


def import_tui_queue(tui_dir=None, dir=None) -> tuple[int, int, list[str]]:
    """Import the TUI schema-1 queue one-way and idempotently; returns
    ``(imported, skipped, errors)``."""
    rows, errors = _tui_queue_rows(tui_dir)
    if not rows and not errors:
        return 0, 0, []
    imported = skipped = 0
    with _QUEUE_LOCK, interprocess_lock(_queue_path(dir).with_name("queue.json.lock")):
        current = _read_queue_raw(dir)
        seen = {h for e in current if (h := _meta(e.get("gui")).get("source_hash"))}
        for n, raw in enumerate(rows):
            if not isinstance(raw, dict):
                errors.append(f"row {n + 1}: not an object")
                continue
            h = source_hash(raw)
            if h in seen:
                skipped += 1
                continue
            try:
                Job.from_dict(raw)
            except Exception as exc:
                errors.append(f"row {n + 1}: {exc}")
                continue
            current.append({"job": raw, "gui": _meta({
                "name": _default_name(raw), "source_hash": h})})
            seen.add(h)
            imported += 1
        if imported:
            write_text_atomic(_queue_path(dir), json.dumps(
                {"schema": GUI_SCHEMA, "rows": current}, indent=2))
    return imported, skipped, errors


def _presets_path(dir) -> Path:
    return cache_dir(dir) / "presets.json"


def _presets_dict(raw: object) -> dict | None:
    """The presets envelope both readers and blind-merge writers agree on."""
    if (not isinstance(raw, dict) or set(raw) != {"schema", "presets"}
            or not is_schema_stamp(raw["schema"]) or raw["schema"] != GUI_SCHEMA
            or not isinstance(raw["presets"], dict)):
        return None
    return raw["presets"]


def load_presets(dir=None) -> dict[str, tuple[Job, dict]]:
    """The preset table; one unparseable entry quarantines the file — a preset
    this build cannot parse must never vanish silently from the list."""
    path = _presets_path(dir)
    sweep_stale_temps(path)
    if not path.is_file():
        return {}
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError, UnicodeDecodeError):
        quarantine_or_raise(path)
        return {}
    presets = _presets_dict(raw)
    if presets is None:
        quarantine_or_raise(path)
        return {}
    out: dict[str, tuple[Job, dict]] = {}
    for name, entry in presets.items():
        if not isinstance(entry, dict):
            quarantine_or_raise(path)
            return {}
        try:
            job = Job.from_dict(entry.get("job"))
        except Exception:
            quarantine_or_raise(path)
            return {}
        out[name] = (job, _meta(entry.get("gui")))
    return out


def save_preset(name: str, job: Job, meta: dict, dir=None) -> None:
    _write_presets(lambda presets: presets.__setitem__(
        name, {"job": job.to_dict(), "gui": _meta(meta)}), dir)


def delete_preset(name: str, dir=None) -> None:
    _write_presets(lambda presets: presets.pop(name, None), dir)


def _write_presets(mutate, dir) -> None:
    path = _presets_path(dir)
    with interprocess_lock(path.with_name("presets.json.lock")):
        try:
            raw = json.loads(path.read_text(encoding="utf-8")) if path.is_file() else None
        except (json.JSONDecodeError, OSError, UnicodeDecodeError):
            quarantine_or_raise(path)
            raw = None
        presets = _presets_dict(raw)
        if presets is None:
            if raw is not None:
                quarantine_or_raise(path)
            presets = {}
        mutate(presets)
        write_text_atomic(path, json.dumps({"schema": GUI_SCHEMA, "presets": presets}, indent=2))
