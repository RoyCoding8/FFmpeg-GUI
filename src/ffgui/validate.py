from __future__ import annotations

import dataclasses
import re

from fftui.model import Job

from ffgui.export.model import contains_hostile
from ffgui.store import TRUTHY, is_truthy

CURATED_TYPES = {
    "crf": "int", "cq": "int", "threads": "int", "g": "int", "bf": "int",
    "refs": "int", "ac": "int", "ar": "int",
}

_JOB_FIELDS = {f.name: f for f in dataclasses.fields(Job)}
_NON_PLAIN_GLOBALS = frozenset({
    "inputs", "outputs", "video_filters", "audio_filters", "filter_complex", "hw_devices",
})
_INT_RE = re.compile(r"[+-]?[0-9]+")
_DISPOSITIONS = frozenset({
    "default", "dub", "original", "comment", "lyrics", "karaoke",
    "forced", "hearing_impaired", "visual_impaired", "clean_effects",
    "attached_pic", "timed_thumbnails", "captions", "descriptions",
    "metadata", "dependent", "still_image",
})
_DISP_VALUE_RE = re.compile(r"[+-]?[A-Za-z0-9_]+(?:[+-][A-Za-z0-9_]+)*")
_STRUCTURAL_INPUT_FLAGS = frozenset({"-i", "-map", "-filter_complex"})
_INPUT_INT_FLAGS = frozenset({"-loop"})
_INPUT_TIME_FLAGS = frozenset({"-ss"})
_TIME_RE = re.compile(r"^[+-]?(?:\d+(?::\d+){1,2}(?:\.\d+)?|\d+(?:\.\d+)?(?:ms|us|s)?)$")
_LEADING_DOT_RE = re.compile(r"^([+-]?)\.(\d+)((?:ms|us|s)?)$")
_FALSY = frozenset({"", "0", "false", "no", "off"})


def valid_disposition(value: str) -> bool:
    if value == "0":
        return True
    if not _DISP_VALUE_RE.fullmatch(value):
        return False
    return all(term in _DISPOSITIONS for term in re.split(r"[+-]", value.lstrip("+-")))


def coerce_str(value: object) -> str:
    return value if isinstance(value, str) else ""


def field_hostile(value: str) -> bool:
    return '"' in value or contains_hostile(value)


def clean_text(value: object, label: str) -> tuple[str, str | None]:
    text = coerce_str(value)
    if field_hostile(text):
        return "", f"{label} must not contain quotes or newlines"
    return text, None


def parse_bool_text(value: object) -> bool | None:
    text = value.strip().lower() if isinstance(value, str) else ""
    if text in TRUTHY:
        return True
    if text in _FALSY:
        return False
    return None


def job_field(key: str):
    return _JOB_FIELDS.get(key) if isinstance(key, str) else None


def is_bool_global(key: str) -> bool:
    field = job_field(key)
    return field is not None and field.type in (bool, "bool")


def coerce_global(key: str, value: str):
    field = job_field(key)
    if field is None:
        return value
    if field.type in (bool, "bool"):
        return is_truthy(value)
    if field.type in (int, "int"):
        try:
            return int(value)
        except ValueError:
            return value
    return value


def validate_option(scope: str, key: str, value: str) -> str | None:
    if scope not in ("video", "audio", "mux", "global"):
        return f"unknown scope {scope!r}"
    if not isinstance(key, str) or not key or field_hostile(key):
        return f"invalid option name {key!r}"
    value, err = clean_text(value, key)
    if err:
        return err
    if scope == "global":
        if key not in _JOB_FIELDS:
            return f"unknown global {key!r}"
        if key in _NON_PLAIN_GLOBALS:
            return f"global {key!r} is not a plain option"
        if key == "two_pass":
            return "two_pass is a per-row toggle, not a plain option"
        if is_bool_global(key) and value and parse_bool_text(value) is None:
            return f"bad flag value {value!r} — expected on/off, true/false, 1/0"
        return None
    if key == "container":
        return "Container is set from the container box, not a codec/mux option"
    if CURATED_TYPES.get(key, "string") == "int" and value and not _INT_RE.fullmatch(value):
        return f"{key} wants an integer, got {value!r}"
    return None
