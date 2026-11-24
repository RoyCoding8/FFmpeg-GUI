from __future__ import annotations

import xml.etree.ElementTree as ET
from functools import lru_cache
from pathlib import Path

from PySide6.QtCore import QByteArray, Qt
from PySide6.QtGui import QIcon, QPainter, QPixmap
from PySide6.QtSvg import QSvgRenderer

ASSETS = Path(__file__).resolve().parents[3] / "assets" / "icons"

NAMES = frozenset((
    "plus", "folder", "film", "x", "trash-2", "copy", "play", "file-text",
    "settings", "sun", "moon", "triangle-alert", "check", "chevron-down",
    "grip-vertical", "circle-chevron-down",
))


def _text_color() -> str:
    from ffgui.ui import theme
    return theme.active_text()


def svg_source(name: str, color: str) -> str:
    try:
        path = (ASSETS / f"{name}.svg").resolve()
        if path.parent != ASSETS.resolve():
            return ""
        text = path.read_text(encoding="utf-8")
        ET.fromstring(text)
    except (OSError, UnicodeDecodeError, ET.ParseError):
        return ""
    return text.replace('stroke="currentColor"', f'stroke="{color}"')


@lru_cache(maxsize=256)
def icon(name: str, color: str | None = None, size: int = 16) -> QIcon:
    result = QIcon()
    source = svg_source(name, color or _text_color())
    if not source:
        return result
    renderer = QSvgRenderer(QByteArray(source.encode("utf-8")))
    for scale in (1, 2):
        pm = QPixmap(size * scale, size * scale)
        pm.fill(Qt.GlobalColor.transparent)
        painter = QPainter(pm)
        renderer.render(painter)
        painter.end()
        pm.setDevicePixelRatio(scale)
        result.addPixmap(pm)
    return result
