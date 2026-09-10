"""Render ffgui's original app icon (authored SVG) to build/ffgui.ico."""

from __future__ import annotations

import sys
from pathlib import Path

from PySide6.QtCore import QByteArray, Qt
from PySide6.QtGui import QGuiApplication, QIcon, QPainter, QPixmap
from PySide6.QtSvg import QSvgRenderer

SVG = """<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 64 64">
  <rect x="4" y="4" width="56" height="56" rx="12" fill="#2563EB"/>
  <rect x="14" y="18" width="36" height="28" rx="4" fill="#FFFFFF"/>
  <rect x="10" y="20" width="6" height="24" fill="#FFFFFF" opacity="0.85"/>
  <rect x="48" y="20" width="6" height="24" fill="#FFFFFF" opacity="0.85"/>
  <circle cx="13" cy="24" r="1.8" fill="#2563EB"/>
  <circle cx="13" cy="32" r="1.8" fill="#2563EB"/>
  <circle cx="13" cy="40" r="1.8" fill="#2563EB"/>
  <circle cx="51" cy="24" r="1.8" fill="#2563EB"/>
  <circle cx="51" cy="32" r="1.8" fill="#2563EB"/>
  <circle cx="51" cy="40" r="1.8" fill="#2563EB"/>
  <path d="M28 26 L40 32 L28 38 Z" fill="#2563EB"/>
</svg>
"""


def main() -> int:
    _app = QGuiApplication.instance() or QGuiApplication(sys.argv)
    renderer = QSvgRenderer(QByteArray(SVG.encode()))
    icon = QIcon()
    for size in (16, 24, 32, 48, 64, 128, 256):
        pm = QPixmap(size, size)
        pm.fill(Qt.GlobalColor.transparent)
        painter = QPainter(pm)
        renderer.render(painter)
        painter.end()
        icon.addPixmap(pm)
    out = Path(__file__).parent / "ffgui.ico"
    ok = icon.pixmap(256, 256).save(str(out))
    print(f"{out}: {'ok' if ok else 'FAILED'}")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
