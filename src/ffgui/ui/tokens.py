"""Design tokens: every pixel, color, and font the UI may use, as code. No widget
may hardcode a px or hex outside this module."""

from __future__ import annotations

SPACING = (4, 8, 12, 16, 24, 32, 48)
TYPE = {"secondary": 11, "body": 13, "title": 15, "window_title": 20}
RADII = {"control": 6, "card": 10, "preview": 0}
FONT_STACK = '"Segoe UI", system-ui, sans-serif'
MONO_STACK = '"Cascadia Mono", Consolas, "DejaVu Sans Mono", monospace'
MONO_PT = 12
ROW_HEIGHT = 32  # 20px 13pt line + 2x4px control padding + 2x1px border, rounded up
SIDEBAR_WIDTH = 220
PREVIEW_MIN_HEIGHT = 120

TAB_NAMES = ("Container", "Video", "Audio", "Subtitles", "Filters", "Chapters",
             "Metadata", "Advanced")

LIGHT = {
    "bg": "#F5F5F4", "surface": "#FFFFFF", "border": "#E2E2E0",
    "text": "#1C1C1A", "secondary": "#6B6B68", "accent": "#2563EB",
    "accent_hover": "#1D4ED8", "danger": "#DC2626", "warn": "#D97706",
    "ok": "#059669", "preview_bg": "#1E1E1E", "preview_fg": "#E8E8E6",
}

DARK = {
    "bg": "#191918", "surface": "#20201F", "border": "#333331",
    "text": "#EDEDEA", "secondary": "#A3A39E", "accent": "#60A5FA",
    "accent_hover": "#93C5FD", "danger": "#F87171", "warn": "#FBBF24",
    "ok": "#34D399", "preview_bg": "#111110", "preview_fg": "#E8E8E6",
}

PALETTES = {"light": LIGHT, "dark": DARK}
