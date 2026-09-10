"""Curation overlay (assets/curation/*.yaml): friendly Expert labels/groups,
merged additively — never removes rows."""

from __future__ import annotations

from pathlib import Path

import yaml

ASSETS = Path(__file__).resolve().parents[2] / "assets" / "curation"


def load(dir: Path | str | None = None) -> dict[str, dict]:
    out: dict[str, dict] = {}
    for path in sorted(Path(dir or ASSETS).glob("*.yaml")):
        try:
            data = yaml.safe_load(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, yaml.YAMLError):
            continue
        if not isinstance(data, dict):
            continue
        for key, value in data.items():


            if isinstance(key, str) and isinstance(value, dict):
                out[key] = {k: v for k, v in value.items()
                            if k in ("label", "group") and isinstance(v, str)}
    return out


def label_of(curations: dict[str, dict], kind: str, component: str, name: str) -> str:


    entry = curations.get(f"{kind}:{component}:{name}")
    if not isinstance(entry, dict):
        return name
    label = entry.get("label", name)
    return label if isinstance(label, str) else name
