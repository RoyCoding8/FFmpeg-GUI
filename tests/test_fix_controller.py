"""Gate: export button mode, burn-in escaping route, expert filter semantics, volume dB."""
from types import SimpleNamespace

import pytest
from PySide6.QtCore import QSettings
from PySide6.QtWidgets import QFileDialog
from fftui.ffmpeg.capability_index import CapabilityIndex
from fftui.model import Input, InputStream

from ffgui.controller import Controller
from ffgui.doc import QueueDocument
from ffgui.ui.shell import QUEUE_COLUMNS, Shell


@pytest.fixture()
def wired(qapp, tmp_path, monkeypatch):
    monkeypatch.setenv("FFGUI_CACHE_DIR", str(tmp_path / "cache"))
    probe = lambda path: Input(path=str(path), streams=[
        InputStream(input_index=0, spec="v:0", codec_type="video",
                    codec_name="h264", width=64, height=48),
        InputStream(input_index=0, spec="a:0", codec_type="audio",
                    codec_name="aac", sample_rate=48000)])
    settings = QSettings(str(tmp_path / "settings.ini"), QSettings.Format.IniFormat)
    shell = Shell()
    doc = QueueDocument(CapabilityIndex.stub(), prober=probe)
    controller = Controller(shell, doc, CapabilityIndex.stub(), settings)
    return shell, doc, controller, tmp_path


def _row(wired):
    shell, doc, c, tmp = wired
    c.add_paths([str(tmp / "alpha.mp4")])
    shell.queue.selectRow(0)
    return shell, doc, c, c.selected_rows()[0]


def test_export_button_exports_one_script(wired, monkeypatch):
    shell, doc, c, row = _row(wired)
    c.add_paths([str(wired[3] / "beta.mp4")])
    calls = {}
    monkeypatch.setattr(
        QFileDialog, "getSaveFileName",
        staticmethod(lambda *a, **k: (str(wired[3] / "out.bat"), "")))
    monkeypatch.setattr(
        Controller, "export_to",
        lambda self, path, target, one_file=True: calls.update(one_file=one_file))
    shell.export_btn.click()
    assert calls["one_file"] is True


def test_burn_in_goes_through_subtitle_burn_in(wired):
    shell, doc, c, row = _row(wired)
    assert c._apply_advanced("burn", "", r"C:\subs\it's.srt", row) is None
    out = doc.items[row].job.outputs[0]
    assert out.subtitle_burn_in == r"C:\subs\it's.srt"
    assert not [f for f in out.video_filters.filters if f.startswith("subtitles")]


def test_burn_in_clears(wired):
    shell, doc, c, row = _row(wired)
    assert c._apply_advanced("burn", "", "subs.srt", row) is None
    assert c._apply_advanced("burn", "", "", row) is None
    out = doc.items[row].job.outputs[0]
    assert out.subtitle_burn_in in ("", None)
    assert not [f for f in out.video_filters.filters if f.startswith("subtitles")]


def test_expert_filter_replaces_same_option(wired):
    shell, doc, c, row = _row(wired)
    opt = SimpleNamespace(name="width")
    assert c._apply_expert("filter", "scale", opt, "640") is None
    assert c._apply_expert("filter", "scale", opt, "320") is None
    assert doc.items[row].job.video_filters.filters == ["scale=width=320"]


def test_expert_filter_empty_value_clears_option(wired):
    shell, doc, c, row = _row(wired)
    opt = SimpleNamespace(name="shortest")
    assert c._apply_expert("filter", "overlay", opt, "") is None
    assert doc.items[row].job.video_filters.filters == []


def test_negative_bare_volume_is_db(wired):
    shell, doc, c, row = _row(wired)
    assert c._apply_advanced("volume", "", "-6", row) is None
    assert doc.items[row].job.audio_filters.filters == ["volume=-6dB"]


def test_positive_bare_volume_stays_linear(wired):
    shell, doc, c, row = _row(wired)
    assert c._apply_advanced("volume", "", "0.5", row) is None
    assert doc.items[row].job.audio_filters.filters == ["volume=0.5"]


def test_dead_duration_column_dropped(wired):
    shell, doc, c, tmp = wired
    assert "Duration" not in QUEUE_COLUMNS
    c.add_paths([str(tmp / "alpha.mp4")])
    assert shell.queue.columnCount() == len(QUEUE_COLUMNS)
