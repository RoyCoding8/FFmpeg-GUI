import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtCore import QSettings

from fftui.ffmpeg.capability_index import CapabilityIndex
from fftui.model import Input, InputStream

from ffgui.controller import Controller
from ffgui.doc import QueueDocument
from ffgui.ui.shell import Shell


@pytest.fixture(scope="session")
def qapp():
    from PySide6.QtWidgets import QApplication

    app = QApplication.instance() or QApplication([])
    yield app


def _stub_probe(path) -> Input:
    """One video stream per file — the shape nearly every fixture assumes."""
    return Input(path=str(path), streams=[
        InputStream(input_index=0, spec="v:0", codec_type="video",
                    codec_name="h264", width=64, height=48)])


@pytest.fixture()
def probe():
    return _stub_probe


@pytest.fixture()
def doc(qapp, tmp_path, probe):
    d = QueueDocument(CapabilityIndex.stub(), prober=probe)
    d.add_files([str(tmp_path / "alpha.mp4")])
    return d


@pytest.fixture()
def wired(qapp, tmp_path, monkeypatch, probe):
    monkeypatch.setenv("FFGUI_CACHE_DIR", str(tmp_path / "cache"))
    settings = QSettings(str(tmp_path / "settings.ini"), QSettings.Format.IniFormat)
    shell = Shell()
    doc = QueueDocument(CapabilityIndex.stub(), prober=probe)
    return shell, doc, Controller(shell, doc, CapabilityIndex.stub(), settings), tmp_path


def close_top_level_widgets() -> None:
    """Destroy every top-level widget still alive.

    Widget tests never delete what they create, so without this the
    windows of all earlier tests pile up in the session QApplication:
    tens of thousands of leaked widgets slow the whole suite and make
    app-wide setStyleSheet repolish effectively hang.
    """
    widgets = sys.modules.get("PySide6.QtWidgets")
    if widgets is None:
        return
    if widgets.QApplication.instance() is None:
        return
    for window in widgets.QApplication.topLevelWidgets():
        window.close()
        window.deleteLater()
    # DeferredDelete events are not flushed by processEvents() on every
    # platform; dispatch them explicitly so the widgets die now.
    qtcore = sys.modules.get("PySide6.QtCore")
    if qtcore is None:
        return
    qtcore.QCoreApplication.sendPostedEvents(None, qtcore.QEvent.Type.DeferredDelete)


@pytest.fixture(autouse=True)
def _close_top_level_widgets_after_test():
    yield
    close_top_level_widgets()
