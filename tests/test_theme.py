"""Theme tests: WCAG contrast on both palettes, token coverage in the QSS, icon
subset integrity, shell region map offscreen, smoke entry."""

import xml.etree.ElementTree as ET

import pytest

from ffgui.ui import icons, theme
from ffgui.ui.shell import FORMATS, QUEUE_COLUMNS, TAB_NAMES
from ffgui.ui.tokens import DARK, LIGHT

CONTRAST_PAIRS = (
    ("text", "bg"), ("text", "surface"), ("secondary", "bg"),
    ("secondary", "surface"), ("preview_fg", "preview_bg"),
)


def _channel(c: str) -> float:
    v = int(c, 16) / 255
    return v / 12.92 if v <= 0.04045 else ((v + 0.055) / 1.055) ** 2.4


def _luminance(hex_color: str) -> float:
    r, g, b = (hex_color.removeprefix("#")[i:i + 2] for i in (0, 2, 4))
    return 0.2126 * _channel(r) + 0.7152 * _channel(g) + 0.0722 * _channel(b)


def _ratio(a: str, b: str) -> float:
    la, lb = sorted((_luminance(a), _luminance(b)), reverse=True)
    return (la + 0.05) / (lb + 0.05)


@pytest.mark.parametrize("pal", [LIGHT, DARK], ids=["light", "dark"])
def test_contrast(pal):
    for fg, bg in CONTRAST_PAIRS:
        assert _ratio(pal[fg], pal[bg]) >= 4.5, f"{fg} on {bg}"


@pytest.mark.parametrize("pal", [LIGHT, DARK], ids=["light", "dark"])
def test_qss_covers_every_token(pal):
    assert theme.token_refs(pal) == set(pal)


def test_apply_theme_contains_every_hex(qapp):
    for mode, pal in (("light", LIGHT), ("dark", DARK)):
        theme.apply_theme(qapp, mode)
        for value in pal.values():
            assert value in qapp.styleSheet(), f"{mode} missing {value}"


def test_apply_theme_system_does_not_crash(qapp):
    theme.apply_theme(qapp, "system")
    assert qapp.styleSheet()


def test_icon_subset_files_parse(qapp):
    for name in icons.NAMES:
        src = icons.svg_source(name, "#123456")
        assert ET.fromstring(src).tag.endswith("svg")
        assert 'stroke="#123456"' in src


def test_icon_render_and_cache(qapp):
    ic = icons.icon("plus")
    assert not ic.isNull()
    assert icons.icon("plus") is ic
    assert icons.icon("plus", "#123456") is not ic


def test_shell_region_map(qapp):
    from ffgui.ui.shell import Shell

    shell = Shell()
    shell.show()
    assert set(shell.regions) == {"sidebar", "queue", "tabs", "preview", "bottomBar"}
    assert all(w.isVisible() for w in shell.regions.values())
    assert shell.queue.columnCount() == len(QUEUE_COLUMNS)
    assert [shell.tabs.tabText(i) for i in range(shell.tabs.count())] == list(TAB_NAMES)
    assert shell.preview.isReadOnly()
    assert shell.format.count() == len(FORMATS)
    for state in ("workspace", "loading", "no-ffmpeg"):
        shell.set_state(state)
    shell.set_state("workspace")


def test_main_smoke(qapp, monkeypatch):
    from ffgui.app import main

    assert main(["--smoke"]) == 0


def test_missing_icon_asset_is_tolerated(qapp):
    assert icons.svg_source("no-such-icon", "#123456") == ""
    assert icons.icon("no-such-icon").isNull()


def test_broken_icon_assets_are_tolerated(qapp, tmp_path, monkeypatch):
    (tmp_path / "broken.svg").write_text("<svg><unclosed>", encoding="utf-8")
    (tmp_path / "binary.svg").write_bytes(b"\xff\xfe\x00<svg>")
    monkeypatch.setattr(icons, "ASSETS", tmp_path)
    icons.icon.cache_clear()
    try:
        for name in ("broken", "binary"):
            assert icons.svg_source(name, "#123456") == ""
            assert icons.icon(name).isNull()
    finally:
        icons.icon.cache_clear()


def test_build_qss_rejects_partial_palette():
    partial = dict(LIGHT)
    del partial["accent"]
    with pytest.raises(ValueError, match="accent"):
        theme.build_qss(partial)
    with pytest.raises(ValueError):
        theme.build_qss({})


def test_build_qss_treats_values_as_literal():
    """Palette values are literal text: never re-scanned for @tokens."""
    pal = dict(LIGHT, accent_hover="@bg", bg="@text")
    out = theme.build_qss(pal)
    assert "@bg" in out and "@text" in out


def test_build_qss_preserves_backslash_values():
    pal = dict(LIGHT, bg=r"C:\new\1x")
    assert r"C:\new\1x" in theme.build_qss(pal)


def test_icon_name_cannot_escape_assets_dir(qapp, tmp_path):
    outside = tmp_path / "evil.svg"
    outside.write_text(
        '<svg xmlns="http://www.w3.org/2000/svg" stroke="currentColor">'
        '<circle cx="8" cy="8" r="4"/></svg>', encoding="utf-8")
    name = str(outside.with_suffix(""))
    assert icons.svg_source(name, "#123456") == ""
    assert icons.svg_source("../x", "#123456") == ""
    icons.icon.cache_clear()
    try:
        assert icons.icon(name).isNull()
    finally:
        icons.icon.cache_clear()


def test_theme_switch_stress_no_leaked_connections(qapp):
    for mode in ("light", "dark", "system", "light", "system", "dark",
                 "system", "light"):
        theme.apply_theme(qapp, mode)
        assert qapp.styleSheet()
        assert icons._text_color() == theme.resolve(mode)["text"]
        assert len(theme._theme_connection) == (1 if mode == "system" else 0)
    theme.apply_theme(qapp, "system")
    theme.apply_theme(qapp, "system")
    assert len(theme._theme_connection) == 1


def test_default_icon_tint_follows_applied_theme(qapp):
    theme.apply_theme(qapp, "light")
    assert icons._text_color() == LIGHT["text"]
    theme.apply_theme(qapp, "dark")
    assert icons._text_color() == DARK["text"]
    theme.apply_theme(qapp, "system")


def test_build_qss_rejects_non_string_values():
    """Non-string palette values must fail loudly, never silent QSS garbage."""
    with pytest.raises(ValueError, match="bg"):
        theme.build_qss(dict(LIGHT, bg=None))
    with pytest.raises(ValueError, match="bg"):
        theme.build_qss(dict(LIGHT, bg=123))


def test_resolve_rejects_unknown_mode():
    with pytest.raises(ValueError, match="nonsense"):
        theme.resolve("nonsense")
    with pytest.raises(ValueError):
        theme.resolve(None)


def test_apply_theme_rejects_unknown_mode_without_poisoning_state(qapp):
    theme.apply_theme(qapp, "light")
    with pytest.raises(ValueError, match="nonsense"):
        theme.apply_theme(qapp, "nonsense")
    assert theme.applied_mode() == "light"
    assert LIGHT["text"] in qapp.styleSheet()
    theme.apply_theme(qapp, "system")


def test_icon_cache_refreshes_on_theme_switch(qapp):
    theme.apply_theme(qapp, "light")
    before = icons.icon("plus")
    assert not before.isNull()
    theme.apply_theme(qapp, "dark")
    after = icons.icon("plus")
    assert not after.isNull()
    assert after is not before
    theme.apply_theme(qapp, "system")


def test_coerce_mode_falls_back_to_system():
    """A hand-edited/corrupt persisted theme must never crash startup."""
    assert theme.coerce_mode("dark") == "dark"
    assert theme.coerce_mode("system") == "system"
    assert theme.coerce_mode("nonsense") == "system"
    assert theme.coerce_mode("") == "system"
    assert theme.coerce_mode(None) == "system"
