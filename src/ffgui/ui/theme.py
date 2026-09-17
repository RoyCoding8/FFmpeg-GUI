"""One application-wide stylesheet generated from tokens.py: every value an
``@token`` reference substituted in one pass."""

from __future__ import annotations

import re

from PySide6.QtCore import Qt
from PySide6.QtGui import QGuiApplication

from ffgui.ui import icons
from ffgui.ui.tokens import (
    DARK, FONT_STACK, LIGHT, MONO_PT, MONO_STACK, PALETTES, RADII, ROW_HEIGHT,
    SPACING, TYPE,
)

UNITS = {
    "font": FONT_STACK, "font_mono": MONO_STACK,
    "font_body": f"{TYPE['body']}pt", "font_title": f"{TYPE['title']}pt",
    "font_window": f"{TYPE['window_title']}pt", "font_secondary": f"{TYPE['secondary']}pt",
    "font_mono_size": f"{MONO_PT}pt",
    "radius_card": f"{RADII['card']}px", "radius_control": f"{RADII['control']}px",
    # Content height: full row minus vertical control padding (s1) and 1px borders.
    "row_min": f"{ROW_HEIGHT - 2 * SPACING[0] - 2}px",
    "s1": f"{SPACING[0]}px", "s2": f"{SPACING[1]}px",
    "s3": f"{SPACING[2]}px", "s4": f"{SPACING[3]}px",
}

QSS = """
* { outline: none; }
QWidget {
    background: @bg; color: @text;
    font-family: @font; font-size: @font_body;
}
QMainWindow, QDialog { background: @bg; }
QLabel { background: transparent; }
QLabel#windowTitle { font-size: @font_window; font-weight: 600; }
QLabel#title { font-size: @font_title; font-weight: 600; }
QLabel#secondary { font-size: @font_secondary; color: @secondary; }
QLabel#status { font-size: @font_secondary; color: @secondary; }
QLabel#statusError { font-size: @font_secondary; color: @danger; }
QLabel#statusOk { font-size: @font_secondary; color: @ok; }
QLabel#statusWarn { font-size: @font_secondary; color: @warn; }

#card, #sidebar, #bottomBar {
    background: @surface; border: 1px solid @border; border-radius: @radius_card;
}
#preview {
    background: @preview_bg; color: @preview_fg;
    border: none; border-radius: 0px;
    font-family: @font_mono; font-size: @font_mono_size;
}
QPlainTextEdit#preview QScrollBar { background: @preview_bg; }

QPushButton {
    background: @surface; color: @text;
    border: 1px solid @border; border-radius: @radius_control;
    padding: @s1 @s4; min-height: @row_min;
}
QPushButton:hover { border-color: @secondary; }
QPushButton:pressed { background: @bg; }
QPushButton:disabled { color: @secondary; background: @bg; }
QPushButton[variant="primary"] {
    background: @accent; color: #FFFFFF; border: 1px solid @accent; font-weight: 600;
}
QPushButton[variant="primary"]:hover { background: @accent_hover; border-color: @accent_hover; }
QPushButton[variant="primary"]:disabled { background: @border; border-color: @border; color: @bg; }
QPushButton[variant="danger"] { color: @danger; border-color: @danger; }

QLineEdit, QComboBox, QSpinBox, QDoubleSpinBox, QTimeEdit, QDateEdit {
    background: @surface; color: @text;
    border: 1px solid @border; border-radius: @radius_control;
    padding: @s1 @s2; min-height: @row_min;
    selection-background-color: @accent;
}
QLineEdit:focus, QComboBox:focus, QSpinBox:focus, QDoubleSpinBox:focus {
    border-color: @accent;
}
QLineEdit[invalid="true"] { border-color: @danger; color: @danger; }
QComboBox::drop-down { border: none; width: 20px; }
QComboBox QAbstractItemView {
    background: @surface; color: @text;
    border: 1px solid @border; border-radius: @radius_card;
    selection-background-color: @accent; selection-color: #FFFFFF;
}

QCheckBox, QRadioButton { background: transparent; spacing: @s2; }
QCheckBox::indicator, QRadioButton::indicator { width: 15px; height: 15px; }
QCheckBox::indicator:unchecked { border: 1px solid @border; border-radius: 3px;
    background: @surface; }
QCheckBox::indicator:checked { border: 1px solid @accent; border-radius: 3px; background: @accent; }
QRadioButton::indicator:unchecked { border: 1px solid @border; border-radius: 8px;
    background: @surface; }
QRadioButton::indicator:checked { border: 4px solid @accent; border-radius: 8px;
    background: @surface; }

QTabWidget::pane { border: 1px solid @border; border-radius: @radius_card;
    background: @surface; top: -1px; }
QTabBar { background: transparent; }
QTabBar::tab {
    background: transparent; color: @secondary;
    border: 1px solid transparent; border-radius: @radius_control;
    padding: @s1 @s3; margin-right: @s1;
}
QTabBar::tab:selected { background: @surface; color: @text; border-color: @border; }
QTabBar::tab:hover:!selected { color: @text; }

QTableView, QListWidget, QTreeView {
    background: @surface; alternate-background-color: @surface;
    border: 1px solid @border; border-radius: @radius_card;
    selection-background-color: @accent; selection-color: #FFFFFF;
    gridline-color: transparent;
}
QTableView::item, QListWidget::item, QTreeView::item { min-height: @row_min; padding: 0px @s2; }
QHeaderView::section {
    background: @surface; color: @secondary;
    border: none; border-bottom: 1px solid @border;
    padding: @s2; font-size: @font_secondary; font-weight: 600;
}
QTableCornerButton::section { background: @surface; border: none; }

QScrollBar:vertical { background: transparent; width: 10px; margin: 2px; }
QScrollBar::handle:vertical { background: @border; border-radius: 4px; min-height: 32px; }
QScrollBar::handle:vertical:hover { background: @secondary; }
QScrollBar:horizontal { background: transparent; height: 10px; margin: 2px; }
QScrollBar::handle:horizontal { background: @border; border-radius: 4px; min-width: 32px; }
QScrollBar::handle:horizontal:hover { background: @secondary; }
QScrollBar::add-line, QScrollBar::sub-line { width: 0; height: 0; }
QScrollBar::add-page, QScrollBar::sub-page { background: transparent; }

QSplitter::handle { background: transparent; }
QSplitter::handle:hover { background: @border; }
QSplitter::handle:horizontal { width: @s1; }
QSplitter::handle:vertical { height: @s1; }

QToolTip {
    background: @text; color: @bg;
    border: none; border-radius: @radius_control; padding: @s1 @s2;
}
QMenu {
    background: @surface; color: @text;
    border: 1px solid @border; border-radius: @radius_card; padding: @s1;
}
QMenu::item { padding: @s1 @s4; border-radius: @radius_control; }
QMenu::item:selected { background: @accent; color: #FFFFFF; }
QMenu::separator { height: 1px; background: @border; margin: @s1 @s2; }

QProgressBar {
    background: @surface; border: 1px solid @border; border-radius: @radius_control;
    text-align: center; color: @secondary;
}
QProgressBar::chunk { background: @accent; border-radius: @radius_control; }
"""

_theme_connection: list = []
_applied_mode = "system"


def build_qss(pal: dict) -> str:
    """QSS with every @token replaced in one pass (values are literal text)."""
    tokens = {**pal, **UNITS}
    names = {m.group(1) for m in re.finditer(r"@(\w+)", QSS)}
    if missing := sorted(names - set(tokens)):
        raise ValueError(f"palette missing tokens: {missing}")
    if bad := sorted(n for n in names if not isinstance(tokens[n], str)):
        raise ValueError(f"palette tokens must be strings: {bad}")
    pattern = re.compile(
        "@(" + "|".join(re.escape(n) for n in sorted(tokens, key=len, reverse=True)) + r")\b")
    return pattern.sub(lambda m: tokens[m.group(1)], QSS)


def resolve(mode: str) -> dict:
    """The palette for a mode ("light"|"dark"|"system"), system following Qt's hint."""
    if mode in PALETTES:
        return dict(PALETTES[mode])
    if mode != "system":
        raise ValueError(f"unknown theme mode: {mode!r}")
    scheme = QGuiApplication.styleHints().colorScheme()
    return dict(DARK) if scheme == Qt.ColorScheme.Dark else dict(LIGHT)


def coerce_mode(mode) -> str:
    """Persisted theme value sanitized: anything unknown falls back to system."""
    return mode if mode in ("light", "dark", "system") else "system"


def applied_mode() -> str:
    """Last mode passed to apply_theme ("system" keeps following the OS)."""
    return _applied_mode


def active_text() -> str:
    """Text color of the currently applied theme (what default-tint icons use)."""
    return resolve(_applied_mode)["text"]


def apply_theme(app, mode: str = "system") -> None:
    """Set the stylesheet for the whole application; system mode re-applies on change."""
    global _applied_mode
    stylesheet = build_qss(resolve(mode))
    _applied_mode = mode
    icons.icon.cache_clear()
    while _theme_connection:
        QGuiApplication.styleHints().colorSchemeChanged.disconnect(_theme_connection.pop())
    if mode == "system":
        _theme_connection.append(
            QGuiApplication.styleHints().colorSchemeChanged.connect(
                lambda *_: (app.setStyleSheet(build_qss(resolve("system"))),
                            icons.icon.cache_clear())))
    app.setStyleSheet(stylesheet)


def token_refs(pal: dict) -> set:
    """Token names the stylesheet references (@bg …), for the coverage test."""
    return {name for name in pal if re.search(rf"@{name}\b", QSS)}
