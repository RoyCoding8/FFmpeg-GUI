"""Qt-side document over Job + command_builder. Settings ride the (Job, meta)
pair whole, so reorder can't detach them."""

from __future__ import annotations

import dataclasses
import os
from dataclasses import dataclass
from pathlib import Path

import re

from PySide6.QtCore import QObject, Signal

from fftui.errors import FFtuiError
from fftui.ffmpeg.capability_index import CapabilityIndex
from fftui.ffmpeg.probe import probe
from fftui.model import Chapter, FilterChain, Job, Output
from fftui.util.command_builder import build, build_two_pass, render_ffmetadata
from fftui.util.validation import valid_stream_spec

from ffgui.export.model import contains_hostile
from ffgui.store import TRUTHY, UnparsedRow, fresh_meta, is_truthy, load_queue, save_queue

AUDIO_SUFFIXES = {".mp3", ".m4a", ".wav", ".flac", ".ogg", ".opus", ".aac", ".wma"}

STREAM_FIELDS = {"video": "video_codec", "audio": "audio_codec", "subtitle": "subtitle_codec"}

CURATED_TYPES = {
    "crf": "int", "cq": "int", "threads": "int", "g": "int", "bf": "int",
    "refs": "int", "ac": "int", "ar": "int",
}

_INT_RE = re.compile(r"[+-]?[0-9]+")

#: ffmpeg's fixed disposition flag set (avformat.h; ffprobe prints the same
#: names). The builder passes dispositions through raw, so this edit-time
#: gate is the only check. Newer builds add non_diegetic/multilayer —
#: deliberately excluded: accepting a name the user's build lacks would fail
#: at runtime instead.
_DISPOSITIONS = frozenset({
    "default", "dub", "original", "comment", "lyrics", "karaoke",
    "forced", "hearing_impaired", "visual_impaired", "clean_effects",
    "attached_pic", "timed_thumbnails", "captions", "descriptions",
    "metadata", "dependent", "still_image",
})
_DISP_VALUE_RE = re.compile(r"[+-]?[A-Za-z0-9_]+(?:[+-][A-Za-z0-9_]+)*")


def _valid_disposition(value: str) -> bool:
    """ffmpeg's -disposition grammar: 0 clears, else +/-chained flag names."""
    if value == "0":
        return True
    if not _DISP_VALUE_RE.fullmatch(value):
        return False
    return all(term in _DISPOSITIONS for term in re.split(r"[+-]", value.lstrip("+-")))

#: Input flags the queue places itself — setting them as pre-`-i` options
#: would double them (builder emits its own) or move output-side flags early.
_STRUCTURAL_INPUT_FLAGS = frozenset({"-i", "-map", "-filter_complex"})
_INPUT_INT_FLAGS = frozenset({"-loop"})
_INPUT_TIME_FLAGS = frozenset({"-ss"})
_TIME_RE = re.compile(r"^[+-]?(?:\d+(?::\d+){1,2}(?:\.\d+)?|\d+(?:\.\d+)?(?:ms|us|s)?)$")
_LEADING_DOT_RE = re.compile(r"^([+-]?)\.(\d+)((?:ms|us|s)?)$")

def _s(value: object) -> str:
    """Wire values are strings; None (or any non-string) coerces to empty,
    which every setter already treats as clear."""
    return value if isinstance(value, str) else ""


def _field_hostile(value: str) -> bool:
    """Quote or line-break check for user-typed fields: `"` breaks cmd.exe
    quoting in every position, line breaks/NUL splice script lines
    (see `HOSTILE_CHARS`). One spelling for the layer's whole contract."""
    return '"' in value or contains_hostile(value)


_FALSY = frozenset({"", "0", "false", "no", "off"})


def _parse_bool_text(value: object) -> bool | None:
    """Toggle text under the shared truthy/falsy sets; None when the text is
    neither (garbage like 'banana' must error, not silently read as off).
    Non-strings read as clear — they can only arrive programmatically, where
    absent means off."""
    text = value.strip().lower() if isinstance(value, str) else ""
    if text in TRUTHY:
        return True
    if text in _FALSY:
        return False
    return None


def _default_output(path: Path) -> str:
    suffix = path.suffix if path.suffix.lower() in AUDIO_SUFFIXES else ".mp4"
    return path.stem + "_out" + suffix


@dataclass
class QueueItem:
    job: Job
    meta: dict
    error: str | None = None


    unparsed: UnparsedRow | None = None


class QueueDocument(QObject):
    itemChanged = Signal(int)
    orderChanged = Signal()

    def __init__(self, cap: CapabilityIndex, prober=probe) -> None:
        super().__init__()
        self.cap = cap
        self.prober = prober
        self.items: list[QueueItem] = []


    def add_files(self, paths: list[str] | str | None) -> list[int]:
        if isinstance(paths, (str, os.PathLike)):
            paths = [paths]
        if not paths:
            return []
        added = []
        for path in paths:
            if not isinstance(path, (str, os.PathLike)):
                continue
            p = Path(path)
            try:
                inp = self.prober(str(p))
                if inp is None:
                    raise ValueError(f"probe returned no input for {path!r}")
            except Exception as exc:
                self.items.append(QueueItem(
                    Job(inputs=[], outputs=[]), fresh_meta(p.name),
                    error=str(exc)))
            else:
                out = Output(path=_default_output(p))
                job = Job(inputs=[inp], outputs=[out])
                self.items.append(QueueItem(job, fresh_meta(p.stem)))
            added.append(len(self.items) - 1)
        if added:
            self.orderChanged.emit()
        return added

    def remove_rows(self, rows: list[int]) -> None:


        if not isinstance(rows, (list, tuple)):
            return
        targets = sorted({r for r in rows
                          if isinstance(r, int) and not isinstance(r, bool)
                          and 0 <= r < len(self.items)}, reverse=True)
        if not targets:
            return
        for row in targets:
            del self.items[row]
        self.orderChanged.emit()

    def move_rows(self, rows: list[int], to: int) -> None:
        if not isinstance(rows, (list, tuple)):
            return
        sel = sorted({r for r in rows if isinstance(r, int)
                      and not isinstance(r, bool) and 0 <= r < len(self.items)})
        if not sel:
            return
        picked = [self.items[r] for r in sel]
        remaining = [it for n, it in enumerate(self.items) if n not in set(sel)]
        target = min(max(to - sum(1 for r in sel if r < to), 0), len(remaining))
        self.items = remaining[:target] + picked + remaining[target:]
        self.orderChanged.emit()

    def duplicate_row(self, row: int) -> int:
        src = self.item(row)
        meta = {**src.meta, "name": f"{src.meta.get('name') or ''} copy"}
        copy = QueueItem(Job.from_dict(src.job.to_dict()), meta, src.error, src.unparsed)
        self.items.insert(row + 1, copy)
        self.orderChanged.emit()
        return row + 1

    def item(self, row: int) -> QueueItem:


        if isinstance(row, bool) or not isinstance(row, int) \
                or not 0 <= row < len(self.items):
            raise IndexError(f"no such row {row}")
        return self.items[row]

    def _out(self, row: int) -> Output | None:
        outputs = self.item(row).job.outputs
        return outputs[0] if outputs else None

    def _inp(self, row: int):
        inputs = self.item(row).job.inputs
        return inputs[0] if inputs else None

    def touch(self, row: int) -> None:
        """Mark a row edited (clears parked unparsed state)."""
        self.item(row).unparsed = None
        self.itemChanged.emit(row)

    def __len__(self) -> int:
        return len(self.items)


    def _codec_warning(self, out: Output, stream: str, codec: str) -> str | None:
        """Warning-only compat gate: subtitle codecs and unknown muxers can't be judged."""
        if stream == "subtitle" or self.cap.entry("encoder", codec):
            return None
        container = out.container or Path(out.path or "").suffix.lstrip(".")
        if not container or self.cap.entry("muxer", container) is None:
            return None
        return f"{codec} is not in this build's encoder list"

    def set_codec(self, row: int, stream: str, codec: str) -> str | None:
        if not isinstance(stream, str) or stream not in STREAM_FIELDS:
            return f"unknown stream {stream!r}"
        codec = _s(codec)
        if _field_hostile(codec):
            return f"{stream} codec must not contain quotes or newlines"
        out = self._out(row)
        if out is None:
            return "row has no output yet"
        if not codec:


            setattr(out, STREAM_FIELDS[stream], None)
            if stream == "subtitle":


                out.sn_explicit = False
            self.touch(row)
            return None
        warning = self._codec_warning(out, stream, codec)
        if stream == "subtitle" and codec == "none":
            out.subtitle_codec = None
            out.sn_explicit = True
        else:
            setattr(out, STREAM_FIELDS[stream], codec)
            if stream == "subtitle":
                out.sn_explicit = False
        self.touch(row)
        return warning

    def set_option(self, row: int, scope: str, key: str, value: str) -> str | None:
        value = _s(value)
        error = _validate(scope, key, value)
        if error:
            return error
        item = self.item(row)
        if scope == "global":


            if value or _is_bool_global(key):
                setattr(item.job, key, _coerce_global(key, value))
            else:
                setattr(item.job, key, None)
        else:
            out = self._out(row)
            if out is None:
                return "row has no output yet"
            target = {"video": "video_options", "audio": "audio_options",
                      "mux": "options"}[scope]
            opts = getattr(out, target)
            if value:
                opts[key] = value
            else:
                opts.pop(key, None)
            if scope == "mux" and key == "f" and value:
                out.container = None
            if scope == "video" and key in ("crf", "cq"):


                opts.pop("cq" if key == "crf" else "crf", None)
        self.touch(row)
        return None

    def set_output_field(self, row: int, key: str, value: str) -> str | None:
        """Basic per-item output edits: path, container, cover art, trim range,
        segment time. Also the single validated home of the segment_time slot."""
        if key not in ("path", "container", "cover_art", "start", "duration",
                       "end", "segment_time"):
            return f"unknown output field {key!r}"
        value = _s(value)
        if _field_hostile(value):
            return f"{key} must not contain quotes or newlines"
        if key == "path" and not value:
            return "output path must not be empty"
        if key in ("duration",) and value.startswith("-"):
            return "duration must not be negative"
        out = self._out(row)
        if out is None:
            return "row has no output yet"
        warning = None
        if key in ("start", "duration", "end") and value and out.video_codec == "copy":
            warning = ("trim with video_codec=copy cuts on keyframes only — "
                       "re-encode for frame-accurate trims")
        if key == "container" and value:
            out.options.pop("f", None)
        setattr(out, key, value or None)
        self.touch(row)
        return warning

    def set_metadata(self, row: int, key: str, value: str) -> str | None:
        if key not in ("title", "comment"):
            return f"unknown tag {key!r}"
        value = _s(value)
        if _field_hostile(value):
            return f"{key} must not contain quotes or newlines"
        out = self._out(row)
        if out is None:
            return "row has no output yet"
        if value:
            out.metadata[key] = value
        else:
            out.metadata.pop(key, None)
        self.touch(row)
        return None

    def bulk_set_option(self, rows: list[int], scope: str, key: str,
                        value: str) -> dict[int, str | None]:


        out: dict[int, str | None] = {}
        if not isinstance(rows, (list, tuple)):
            return out
        for row in rows:
            try:
                out[row] = self.set_option(row, scope, key, value)
            except IndexError:
                out[row] = f"no such row {row}"
        return out


    def set_two_pass(self, row: int, on: bool | str) -> None:
        # Total over Qt variants: bare "False" is truthy as a string, so text
        # goes through the shared parser instead of a truthiness test.
        self.set_flag(row, "two_pass",
                      "1" if (is_truthy(on) if isinstance(on, str) else bool(on))
                      else "")

    def set_flag(self, row: int, key: str, value: str) -> str | None:
        """Flip one boolean Job field from toggle text. Non-bool fields are
        refused: silently coercing an int/str field to True/False corrupts it."""
        field = _job_field(key)
        if field is None:
            return f"unknown flag {key!r}"
        if not (field.type is bool or field.type == "bool"):
            return f"not a flag {key!r}"
        on = _parse_bool_text(value)
        if on is None:
            return f"bad flag value {value!r} — expected on/off, true/false, 1/0"
        setattr(self.item(row).job, key, on)
        self.touch(row)
        return None

    def set_faststart(self, row: int, value: str) -> str | None:
        """Toggle `faststart` inside the mux-slot movflags, preserving sibling
        flags. The single home of the movflags slot: legacy rows carrying it
        in `video_options` converge here. Off with no flags left pops the key."""
        out = self._out(row)
        if out is None:
            return "row has no output yet"
        out.video_options.pop("movflags", None)
        flags = {f for f in (out.options.get("movflags") or "").split("+") if f}
        (flags.add if is_truthy(value) else flags.discard)("faststart")
        merged = "".join(f"+{f}" for f in sorted(flags))
        if merged:
            out.options["movflags"] = merged
        else:
            out.options.pop("movflags", None)
        self.touch(row)
        return None

    def set_segment_enabled(self, row: int, value: str) -> str | None:
        """Toggle the segment mover; enabling without a container falls back
        to the output container, defaulting to mp4."""
        out = self._out(row)
        if out is None:
            return "row has no output yet"
        out.segment_enabled = is_truthy(value)
        if out.segment_enabled:
            out.segment_format = out.container or "mp4"
        self.touch(row)
        return None

    def set_filters(self, row: int, stream: str, exprs: list[str] | None) -> str | None:
        """Replace a -vf/-af chain wholesale; each expr is one filter spec."""
        if not isinstance(stream, str) or stream not in ("video_filters", "audio_filters"):
            return f"unknown filter stream {stream!r}"
        if exprs is None:
            exprs = []
        if isinstance(exprs, str):
            exprs = [exprs]
        for expr in exprs:
            if not isinstance(expr, str) or _field_hostile(expr):
                return f"filter {expr!r} must not contain quotes or newlines"
        if self._out(row) is None:
            return "row has no output yet"
        setattr(self.item(row).job, stream,
                FilterChain(filters=list(exprs)) if exprs else FilterChain())
        self.touch(row)
        return None

    def set_chapters(self, row: int, chapters: list[Chapter] | str | None) -> str | None:
        """Chapter rows as a Chapter list, '\x1f'-separated wire text, or None
        to clear. Owns the wire shape, so callers forward the text untouched."""
        if isinstance(chapters, str):
            parts = [r.split("\x1f") for r in chapters.split("\n") if r]
            if any(len(p) != 3 for p in parts):
                return "chapter rows need start, title and language"
            chapters = [Chapter(start=s, title=t, lang=l or "eng")
                        for s, t, l in parts]
        if self._out(row) is None:
            return "row has no output yet"
        rows = list(chapters) if chapters is not None else []
        if any(not isinstance(c, Chapter) for c in rows):
            return "chapter must be a Chapter row"
        if any(not (c.start or "").strip() for c in rows):
            return "chapter needs a start time"


        if any(sep in (c.start or "") + (c.title or "") + (c.lang or "")
               for c in rows for sep in ("\n", "\r", "\x1f")):
            return "chapter text must be single-line"
        if any(bad in (c.start or "") + (c.title or "") + (c.lang or "")
               for c in rows for bad in ('"', "\0")):
            return "chapter text must not contain quotes or NUL"
        try:
            render_ffmetadata(rows)
        except FFtuiError as exc:
            return str(exc)
        self.item(row).job.outputs[0].chapters = rows
        self.touch(row)
        return None

    def set_stream_meta(self, row: int, spec: str, key: str, value: str) -> str | None:
        value = _s(value)
        if (not isinstance(spec, str) or not valid_stream_spec(spec)
                or not isinstance(key, str) or not key
                or any(c in key for c in "|=")
                or _field_hostile(key + value)):
            return f"invalid stream tag {spec}:{key}"
        out = self._out(row)
        if out is None:
            return "row has no output yet"
        tag = f"{spec}|{key}"
        if value:
            out.stream_metadata[tag] = value
        else:
            out.stream_metadata.pop(tag, None)
        out.stream_metadata.pop(f"{spec}:{key}", None)
        self.touch(row)
        return None

    def set_disposition(self, row: int, spec: str, value: str) -> str | None:
        value = _s(value)
        if (not isinstance(spec, str) or not valid_stream_spec(spec)
                or _field_hostile(value)):
            return f"invalid disposition {spec}={value!r}"
        if value and not _valid_disposition(value):
            return (f"unknown disposition {value!r} "
                    "— expected 0 or +/-chained names like default, +forced")
        out = self._out(row)
        if out is None:
            return "row has no output yet"
        if value:
            out.dispositions[spec] = value
        else:
            out.dispositions.pop(spec, None)
        self.touch(row)
        return None

    def set_hw_decode(self, row: int, decoder: str) -> str | None:
        decoder = _s(decoder)
        if _field_hostile(decoder):
            return "decoder must not contain quotes or newlines"
        inp = self._inp(row)
        if inp is None:
            return "row has no input yet"
        inp.decoder_v = decoder or ""
        self.touch(row)
        return None

    def set_input_arg(self, row: int, flag: str, value: str) -> str | None:
        """Set or clear one pre-``-i`` input option (fast seek, loop, subtitle delay)."""
        value = _s(value)
        if (not isinstance(flag, str) or not flag.startswith("-")
                or _field_hostile(flag + value)):
            return f"invalid input option {flag!r}"
        if flag in _STRUCTURAL_INPUT_FLAGS:
            return f"{flag} is placed by the queue itself, not an input option"
        if value and flag in _INPUT_INT_FLAGS and not _INT_RE.fullmatch(value):
            return f"{flag} wants an integer, got {value!r}"
        if value and flag in _INPUT_TIME_FLAGS and not _TIME_RE.fullmatch(
                _LEADING_DOT_RE.sub(r"\g<1>0.\g<2>\g<3>", value)):
            return f"{flag} has invalid time {value!r}"
        inp = self._inp(row)
        if inp is None:
            return "row has no input yet"
        args = inp.input_args
        while flag in args:
            i = args.index(flag)
            args.pop(i)


            if i < len(args):
                args.pop(i)
        if value:
            args += [flag, value]
        self.touch(row)
        return None

    def join_parts(self, row: int, paths: list[str]) -> str | None:
        """Join *paths* onto the row's input through the concat demuxer.

        Seeds the list with the row's own file, dedupes, and stages
        ``-f concat -safe 0``: without it build() points `-i` at the list
        file but ffmpeg reads it as media and fails."""
        inp = self._inp(row)
        if inp is None:
            return "row has no input yet"
        if isinstance(paths, str) or not isinstance(paths, (list, tuple)):
            return f"invalid parts {paths!r}"
        clean: list[str] = []
        for part in paths:
            if not isinstance(part, str) or not part.strip():
                return f"invalid concat part {part!r}"
            if contains_hostile(part):
                return f"concat part {part!r} must be single-line"
            if "://" not in part and not Path(part).is_file():
                return f"part not found: {part}"
            if part not in clean:
                clean.append(part)
        merged = list(dict.fromkeys([*(inp.concat_paths or [inp.path]), *clean]))
        if len(merged) < 2:
            return "join needs at least two parts"
        for flag, val in (("-f", "concat"), ("-safe", "0")):
            error = self.set_input_arg(row, flag, val)
            if error:
                return error
        inp.concat_paths = merged
        self.touch(row)
        return None

    def set_bsf(self, row: int, value: str) -> str | None:
        """Replace the -bsf map from 'spec=filter' text; empty text clears it."""
        value = _s(value)
        if _field_hostile(value):
            return "bsf must not contain quotes or newlines"
        out = self._out(row)
        if out is None:
            return "row has no output yet"
        out.bsf.clear()
        spec, _, flt = value.partition("=")
        if flt:
            out.bsf[spec or "v:0"] = flt
        self.touch(row)
        return None

    def set_tee(self, row: int, value: str) -> str | None:
        value = _s(value)
        if _field_hostile(value):
            return "tee must not contain quotes or newlines"
        out = self._out(row)
        if out is None:
            return "row has no output yet"
        out.tee_spec = value
        self.touch(row)
        return None

    def argv(self, row: int) -> list[str]:
        return build(self.item(row).job)

    def argvs(self, row: int) -> list[list[str]]:
        job = self.item(row).job
        return build_two_pass(job) if job.two_pass else [build(job)]


    def save(self, dir=None) -> None:
        save_queue([(it.unparsed, it.meta) if it.unparsed is not None
                    else (it.job, it.meta) for it in self.items], dir)

    @classmethod
    def load(cls, cap: CapabilityIndex, dir=None, prober=probe) -> QueueDocument:
        doc = cls(cap, prober)
        for job, meta in load_queue(dir):
            if isinstance(job, UnparsedRow):
                doc.items.append(QueueItem(Job(), meta, error="unparseable row",
                                           unparsed=job))
            else:
                doc.items.append(QueueItem(job, meta))
        return doc


def _validate(scope: str, key: str, value: str) -> str | None:
    if scope not in ("video", "audio", "mux", "global"):
        return f"unknown scope {scope!r}"
    if not isinstance(key, str) or not key or _field_hostile(key):
        return f"invalid option name {key!r}"
    value = _s(value)
    if _field_hostile(value):
        return f"{key} must not contain quotes or newlines"
    if scope == "global":
        if _job_field(key) is None:
            return f"unknown global {key!r}"
        if key in ("inputs", "outputs", "video_filters", "audio_filters",
                   "filter_complex", "hw_devices"):
            return f"global {key!r} is not a plain option"
        if key == "two_pass":
            return "two_pass is a per-row toggle, not a plain option"
        if _is_bool_global(key) and value and _parse_bool_text(value) is None:
            return (f"bad flag value {value!r} "
                    "— expected on/off, true/false, 1/0")
        return None
    if scope in ("video", "audio", "mux") and key == "container":
        return "Container is set from the container box, not a codec/mux option"
    kind = CURATED_TYPES.get(key, "string")


    if kind == "int" and value and not _INT_RE.fullmatch(value):
        return f"{key} wants an integer, got {value!r}"
    return None


def _job_field(key: str):
    """The Job dataclass field named *key*, or None (single lookup site)."""
    return next((f for f in dataclasses.fields(Job) if f.name == key), None)


def _is_bool_global(key: str) -> bool:
    field = _job_field(key)
    return field is not None and (field.type is bool or field.type == "bool")


def _coerce_global(key: str, value: str):
    field = _job_field(key)
    if field is not None and (field.type is bool or field.type == "bool"):
        return is_truthy(value)
    if field is not None and (field.type is int or field.type == "int"):
        try:
            return int(value)
        except ValueError:
            return value
    return value
