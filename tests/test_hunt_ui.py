import pytest
from PySide6.QtCore import QSettings, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QPushButton

from fftui.ffmpeg.capability_index import CapabilityIndex

import ffgui.app as app_mod
from ffgui.controller import Controller
from ffgui.doc import QueueDocument
from ffgui.ui.shell import Shell


def test_saved_tab_survives_controller_boot(qapp, tmp_path, monkeypatch):
    monkeypatch.setenv("FFGUI_CACHE_DIR", str(tmp_path / "cache"))
    settings = QSettings(str(tmp_path / "settings.ini"), QSettings.Format.IniFormat)
    settings.setValue("tab", 6)
    shell = Shell()
    app_mod._restore_state(settings, shell)
    assert shell.tabs.currentIndex() == 6
    cap = CapabilityIndex.stub()
    Controller(shell, QueueDocument(cap), cap, settings)
    assert shell.tabs.currentIndex() == 6
    assert shell.tabs.tabText(shell.tabs.currentIndex()) == "Metadata"


@pytest.mark.parametrize("outcome", ["failed", "cancelled"])
def test_initial_probe_can_recover_with_rescan(qapp, tmp_path, monkeypatch, outcome):
    settings = QSettings(str(tmp_path / "settings.ini"), QSettings.Format.IniFormat)
    shell = Shell()
    attempts = []

    def launch(shell, done, failed):
        attempts.append((done, failed))

    monkeypatch.setattr(app_mod, "locate_ffmpeg", lambda settings: (None, True))
    monkeypatch.setattr(app_mod, "_launch_probe", launch)
    app_mod._wire_probe(qapp, shell, settings, [])
    shell.show()
    qapp.processEvents()
    assert shell.state_stack.currentWidget() is shell.loading
    if outcome == "failed":
        attempts[0][1]("probe failed")
    else:
        cancel = next(b for b in shell.loading.findChildren(QPushButton)
                      if b.text() == "Cancel")
        QTest.mouseClick(cancel, Qt.MouseButton.LeftButton)
    qapp.processEvents()
    assert shell.no_ffmpeg.rescan.isVisible()
    QTest.mouseClick(shell.no_ffmpeg.rescan, Qt.MouseButton.LeftButton)
    qapp.processEvents()
    assert shell.state_stack.currentWidget() is shell.loading
    assert len(attempts) == 2
