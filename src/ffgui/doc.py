from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from PySide6.QtCore import QObject, Signal

from fftui.errors import FFtuiError
from fftui.ffmpeg.capability_index import CapabilityIndex
from fftui.ffmpeg.probe import probe
from fftui.model import Chapter, FilterChain, Job, Output
from fftui.util.command_builder import build, build_two_pass, render_ffmetadata
from fftui.util.validation import valid_stream_spec

from ffgui.export.model import contains_hostile
from ffgui.store import UnparsedRow, fresh_meta, is_truthy, load_queue, save_queue
from ffgui.validate import (
    CURATED_TYPES as CURATED_TYPES,
    METADATA_TAGS,
    _INPUT_INT_FLAGS,
    _INPUT_TIME_FLAGS,
    _INT_RE,
    _LEADING_DOT_RE,
    _STRUCTURAL_INPUT_FLAGS,
    _TIME_RE,
    clean_text as _clean,
    coerce_global as _coerce_global,
    coerce_str as _s,
    field_hostile as _field_hostile,
    is_bool_global,
    job_field,
    parse_bool_text,
    valid_disposition,
    validate_option,
)

AUDIO_SUFFIXES = {".mp3", ".m4a", ".wav", ".flac", ".ogg", ".opus", ".aac", ".wma"}
STREAM_FIELDS = {"video": "video_codec", "audio": "audio_codec", "subtitle": "subtitle_codec"}
_OUTPUT_FIELDS = frozenset({
    "path", "container", "cover_art", "start", "duration", "end",
    "segment_time", "subtitle_burn_in",
})
_SCOPE_ATTRS = {"video": "video_options", "audio": "audio_options", "mux": "options"}


def _set_or_pop(mapping: dict, key: str, value: str) -> None:
    if value:
        mapping[key] = value
    else:
        mapping.pop(key, None)


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

    def _valid_row(self, row: object) -> bool:
        return isinstance(row, int) and not isinstance(row, bool) and 0 <= row < len(self.items)

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
                self.items.append(QueueItem(Job(inputs=[], outputs=[]), fresh_meta(p.name), error=str(exc)))
            else:
                self.items.append(QueueItem(Job(inputs=[inp], outputs=[Output(path=_default_output(p))]), fresh_meta(p.stem)))
            added.append(len(self.items) - 1)
        if added:
            self.orderChanged.emit()
        return added

    def remove_rows(self, rows: list[int]) -> None:
        if not isinstance(rows, (list, tuple)):
            return
        targets = sorted({r for r in rows if self._valid_row(r)}, reverse=True)
        if not targets:
            return
        for row in targets:
            del self.items[row]
        self.orderChanged.emit()

    def move_rows(self, rows: list[int], to: int) -> None:
        if not isinstance(rows, (list, tuple)):
            return
        sel = sorted({r for r in rows if self._valid_row(r)})
        if not sel:
            return
        picked = [self.items[r] for r in sel]
        sel_set = set(sel)
        remaining = [it for n, it in enumerate(self.items) if n not in sel_set]
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
        if not self._valid_row(row):
            raise IndexError(f"no such row {row}")
        return self.items[row]

    def _out(self, row: int) -> Output | None:
        outputs = self.item(row).job.outputs
        return outputs[0] if outputs else None

    def _inp(self, row: int):
        inputs = self.item(row).job.inputs
        return inputs[0] if inputs else None

    def _edit_out(self, row: int, fn) -> str | None:
        out = self._out(row)
        if out is None:
            return "row has no output yet"
        result = fn(out)
        self.touch(row)
        return result

    def _edit_inp(self, row: int, fn) -> str | None:
        inp = self._inp(row)
        if inp is None:
            return "row has no input yet"
        result = fn(inp)
        self.touch(row)
        return result

    def touch(self, row: int) -> None:
        it = self.item(row)
        if it.unparsed is not None:
            it.error = None
        it.unparsed = None
        self.itemChanged.emit(row)

    def __len__(self) -> int:
        return len(self.items)

    def _codec_warning(self, out: Output, stream: str, codec: str) -> str | None:
        if stream == "subtitle" or codec == "copy" or self.cap.entry("encoder", codec):
            return None
        container = out.container or Path(out.path or "").suffix.lstrip(".")
        if not container or self.cap.entry("muxer", container) is None:
            return None
        return f"{codec} is not in this build's encoder list"

    def set_codec(self, row: int, stream: str, codec: str) -> str | None:
        if not isinstance(stream, str) or stream not in STREAM_FIELDS:
            return f"unknown stream {stream!r}"
        codec, err = _clean(codec, f"{stream} codec")
        if err:
            return err

        def _apply(out: Output) -> str | None:
            if not codec:
                setattr(out, STREAM_FIELDS[stream], None)
                if stream == "subtitle":
                    out.sn_explicit = False
                    out.options.pop("sn", None)
                return None
            if stream == "subtitle" and codec == "none":
                out.subtitle_codec = None
                out.sn_explicit = True
                out.options["sn"] = ""
                return None
            warning = self._codec_warning(out, stream, codec)
            setattr(out, STREAM_FIELDS[stream], codec)
            if stream == "subtitle":
                out.sn_explicit = False
                out.options.pop("sn", None)
            elif stream == "video":
                out.options.pop("vn", None)
                if codec == "copy":
                    self.item(row).job.video_filters = FilterChain()
                    out.video_filters = FilterChain()
                    out.subtitle_burn_in = ""
            elif stream == "audio":
                out.options.pop("an", None)
                if codec == "copy":
                    self.item(row).job.audio_filters = FilterChain()
                    out.audio_filters = FilterChain()
            return warning

        return self._edit_out(row, _apply)

    def set_stream_drop(self, row: int, flag: str, value: str) -> str | None:
        if not isinstance(flag, str) or flag not in ("vn", "an"):
            return f"unknown stream drop {flag!r}"

        def _apply(out: Output) -> None:
            if is_truthy(_s(value)):
                out.options[flag] = ""
                setattr(out, "video_codec" if flag == "vn" else "audio_codec", None)
            else:
                out.options.pop(flag, None)

        return self._edit_out(row, _apply)

    def set_option(self, row: int, scope: str, key: str, value: str) -> str | None:
        value = _s(value)
        if error := validate_option(scope, key, value):
            return error
        if scope == "global":
            val = _coerce_global(key, value) if (value or is_bool_global(key)) else None
            setattr(self.item(row).job, key, val)
            self.touch(row)
            return None

        def _apply(out: Output) -> None:
            opts = getattr(out, _SCOPE_ATTRS[scope])
            _set_or_pop(opts, key, value)
            if scope == "mux" and key == "f" and value:
                out.container = None
            if scope == "video" and key in ("crf", "cq"):
                opts.pop("cq" if key == "crf" else "crf", None)

        return self._edit_out(row, _apply)

    def set_output_field(self, row: int, key: str, value: str) -> str | None:
        if not isinstance(key, str) or key not in _OUTPUT_FIELDS:
            return f"unknown output field {key!r}"
        value, err = _clean(value, key)
        if err:
            return err
        if key == "path" and not value:
            return "output path must not be empty"
        if key == "duration" and value.startswith("-"):
            return "duration must not be negative"

        def _apply(out: Output) -> str | None:
            warning = (
                "trim with video_codec=copy cuts on keyframes only — re-encode for frame-accurate trims"
                if key in ("start", "duration", "end") and value and out.video_codec == "copy"
                else None
            )
            if value:
                if key == "container":
                    out.options.pop("f", None)
                    out.segment_enabled = False
                    out.tee_spec = ""
                elif key == "cover_art" and not out.mappings:
                    inp = self._inp(row)
                    typed = [
                        f"0:{s.spec}"
                        for s in (inp.streams if inp else [])
                        if s.mapped and s.spec[:1] in ("v", "a", "s")
                    ]
                    out.mappings = typed or ["0:v?", "0:a?"]
                elif key == "duration":
                    out.end = None
                elif key == "end":
                    out.duration = None
                elif key == "subtitle_burn_in" and out.video_codec == "copy":
                    out.video_codec = None
            setattr(out, key, value if key == "subtitle_burn_in" else value or None)
            return warning

        return self._edit_out(row, _apply)

    def set_metadata(self, row: int, key: str, value: str) -> str | None:
        if not isinstance(key, str) or key not in METADATA_TAGS:
            return f"unknown tag {key!r}"
        value, err = _clean(value, key)
        return err or self._edit_out(row, lambda out: _set_or_pop(out.metadata, key, value))

    def bulk_set_option(self, rows: list[int], scope: str, key: str, value: str) -> dict[int, str | None]:
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
        flag = is_truthy(on) if isinstance(on, str) else bool(on)
        self.set_flag(row, "two_pass", "1" if flag else "")

    def set_flag(self, row: int, key: str, value: str) -> str | None:
        field = job_field(key)
        if field is None:
            return f"unknown flag {key!r}"
        if field.type not in (bool, "bool"):
            return f"not a flag {key!r}"
        on = parse_bool_text(value)
        if on is None:
            return f"bad flag value {value!r} — expected on/off, true/false, 1/0"
        setattr(self.item(row).job, key, on)
        self.touch(row)
        return None

    def set_faststart(self, row: int, value: str) -> str | None:
        def _apply(out: Output) -> None:
            legacy = out.video_options.pop("movflags", None)
            flags = {f for src in (out.options.get("movflags"), legacy)
                     for f in (src or "").split("+") if f}
            (flags.add if is_truthy(value) else flags.discard)("faststart")
            _set_or_pop(out.options, "movflags", "".join(f"+{f}" for f in sorted(flags)))

        return self._edit_out(row, _apply)

    def set_segment_enabled(self, row: int, value: str) -> str | None:
        def _apply(out: Output) -> None:
            out.segment_enabled = is_truthy(value)
            if out.segment_enabled:
                out.segment_format = out.container or Path(out.path).suffix.lstrip(".").lower() or "mp4"
                out.container = None
                out.tee_spec = ""

        return self._edit_out(row, _apply)

    def set_filters(self, row: int, stream: str, exprs: list[str] | None) -> str | None:
        if not isinstance(stream, str) or stream not in ("video_filters", "audio_filters"):
            return f"unknown filter stream {stream!r}"
        exprs = [] if exprs is None else [exprs] if isinstance(exprs, str) else exprs
        for expr in exprs:
            if not isinstance(expr, str) or _field_hostile(expr):
                return f"filter {expr!r} must not contain quotes or newlines"

        def _apply(_out: Output) -> None:
            job = self.item(row).job
            setattr(job, stream, FilterChain(filters=list(exprs)) if exprs else FilterChain())
            codec_attr = "video_codec" if stream == "video_filters" else "audio_codec"
            for out in job.outputs:
                setattr(out, stream, FilterChain())
                if exprs and getattr(out, codec_attr) == "copy":
                    setattr(out, codec_attr, None)

        return self._edit_out(row, _apply)

    def set_chapters(self, row: int, chapters: list[Chapter] | str | None) -> str | None:
        if isinstance(chapters, str):
            parts = [r.split("\x1f") for r in chapters.split("\n") if r]
            if any(len(p) != 3 for p in parts):
                return "chapter rows need start, title and language"
            chapters = [Chapter(start=s, title=t, lang=l or "eng") for s, t, l in parts]
        out = self._out(row)
        if out is None:
            return "row has no output yet"
        rows = list(chapters) if chapters is not None else []
        if any(not isinstance(c, Chapter) for c in rows):
            return "chapter must be a Chapter row"
        if any(not (c.start or "").strip() for c in rows):
            return "chapter needs a start time"
        joined = [(c.start or "") + (c.title or "") + (c.lang or "") for c in rows]
        if any(sep in text for text in joined for sep in ("\n", "\r", "\x1f")):
            return "chapter text must be single-line"
        if any(bad in text for text in joined for bad in ('"', "\0")):
            return "chapter text must not contain quotes or NUL"
        try:
            render_ffmetadata(rows)
        except FFtuiError as exc:
            return str(exc)
        out.chapters = rows
        self.touch(row)
        return None

    def set_stream_meta(self, row: int, spec: str, key: str, value: str) -> str | None:
        value = _s(value)
        if (not isinstance(spec, str) or not valid_stream_spec(spec)
                or not isinstance(key, str) or not key
                or any(c in key for c in "|=") or _field_hostile(key + value)):
            return f"invalid stream tag {spec}:{key}"

        def _apply(out: Output) -> None:
            _set_or_pop(out.stream_metadata, f"{spec}|{key}", value)
            out.stream_metadata.pop(f"{spec}:{key}", None)

        return self._edit_out(row, _apply)

    def set_disposition(self, row: int, spec: str, value: str) -> str | None:
        value = _s(value)
        if not isinstance(spec, str) or not valid_stream_spec(spec) or _field_hostile(value):
            return f"invalid disposition {spec}={value!r}"
        if value and not valid_disposition(value):
            return f"unknown disposition {value!r} — expected 0 or +/-chained names like default, +forced"
        return self._edit_out(row, lambda out: _set_or_pop(out.dispositions, spec, value))

    def set_hw_decode(self, row: int, decoder: str) -> str | None:
        decoder, err = _clean(decoder, "decoder")
        return err or self._edit_inp(row, lambda inp: setattr(inp, "decoder_v", decoder))

    def set_input_arg(self, row: int, flag: str, value: str) -> str | None:
        value = _s(value)
        if not isinstance(flag, str) or not flag.startswith("-") or _field_hostile(flag + value):
            return f"invalid input option {flag!r}"
        if flag in _STRUCTURAL_INPUT_FLAGS:
            return f"{flag} is placed by the queue itself, not an input option"
        if value and flag in _INPUT_INT_FLAGS and not _INT_RE.fullmatch(value):
            return f"{flag} wants an integer, got {value!r}"
        if value and flag in _INPUT_TIME_FLAGS and not _TIME_RE.fullmatch(
                _LEADING_DOT_RE.sub(r"\g<1>0.\g<2>\g<3>", value)):
            return f"{flag} has invalid time {value!r}"

        def _apply(inp) -> None:
            args = inp.input_args
            while flag in args:
                i = args.index(flag)
                del args[i:i + 2]
            if value:
                args += [flag, value]

        return self._edit_inp(row, _apply)

    def join_parts(self, row: int, paths: list[str]) -> str | None:
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
            if error := self.set_input_arg(row, flag, val):
                return error
        inp.concat_paths = merged
        self.touch(row)
        return None

    def set_bsf(self, row: int, value: str) -> str | None:
        value, err = _clean(value, "bsf")
        if err:
            return err

        def _apply(out: Output) -> None:
            out.bsf.clear()
            spec, sep, flt = value.partition("=")
            if sep and (not spec or valid_stream_spec(spec)):
                out.bsf[spec or "v:0"] = flt
            elif value:
                out.bsf["v:0"] = value

        return self._edit_out(row, _apply)

    def set_tee(self, row: int, value: str) -> str | None:
        value, err = _clean(value, "tee")
        if err:
            return err

        def _apply(out: Output) -> None:
            out.tee_spec = value
            if value:
                out.segment_enabled = False
                out.container = None

        return self._edit_out(row, _apply)

    def argv(self, row: int) -> list[str]:
        return build(self.item(row).job)

    def argvs(self, row: int) -> list[list[str]]:
        job = self.item(row).job
        return build_two_pass(job) if job.two_pass else [build(job)]

    def save(self, dir=None) -> None:
        rows = []
        for it in self.items:
            if it.unparsed is not None:
                rows.append((it.unparsed, it.meta))
            elif it.error is not None:
                # "outputs" must stay unparseable so Job.from_dict won't reload the errored row as healthy.
                rows.append((UnparsedRow({"probe_error": it.error, "outputs": it.error}), it.meta))
            else:
                rows.append((it.job, it.meta))
        save_queue(rows, dir)

    @classmethod
    def load(cls, cap: CapabilityIndex, dir=None, prober=probe) -> QueueDocument:
        doc = cls(cap, prober)
        for job, meta in load_queue(dir):
            if isinstance(job, UnparsedRow):
                raw_err = job.raw.get("probe_error") if isinstance(job.raw, dict) else None
                doc.items.append(QueueItem(Job(), meta, error=raw_err or "unparseable row", unparsed=job))
            else:
                doc.items.append(QueueItem(job, meta))
        return doc
