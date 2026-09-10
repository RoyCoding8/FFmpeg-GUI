"""ffgui.paths: platform override, XDG/macOS layout, cache-dir override."""

from ffgui import paths


def test_system_honors_platform_override(monkeypatch):
    monkeypatch.setenv("FFTUI_PLATFORM", "darwin")
    assert paths.system() == "Darwin"
    monkeypatch.setenv("FFTUI_PLATFORM", "windows")
    assert paths.system() == "Windows"
    monkeypatch.delenv("FFTUI_PLATFORM", raising=False)
    import platform
    assert paths.system() == platform.system()


def test_app_dir_uses_xdg_on_linux(monkeypatch, tmp_path):
    monkeypatch.setenv("FFTUI_PLATFORM", "linux")
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cx"))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "cfg"))
    assert paths.app_dir("cache") == tmp_path / "cx" / "ffgui"
    assert paths.app_dir("config") == tmp_path / "cfg" / "ffgui"


def test_app_dir_mac_layout(monkeypatch, tmp_path):
    monkeypatch.setenv("FFTUI_PLATFORM", "macos")
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    # expanduser("~") reads USERPROFILE, not HOME, on Windows.
    monkeypatch.setenv("USERPROFILE", str(tmp_path / "home"))
    assert paths.app_dir("cache") == tmp_path / "home" / "Library/Caches" / "ffgui"


def test_cache_dir_override_and_default(monkeypatch, tmp_path):
    monkeypatch.setenv("FFTUI_PLATFORM", "linux")
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cx"))
    monkeypatch.delenv("FFGUI_CACHE_DIR", raising=False)
    assert paths.cache_dir() == tmp_path / "cx" / "ffgui"
    assert paths.cache_dir(str(tmp_path / "over")) == tmp_path / "over"
    monkeypatch.setenv("FFGUI_CACHE_DIR", str(tmp_path / "env"))
    assert paths.cache_dir() == tmp_path / "env"
