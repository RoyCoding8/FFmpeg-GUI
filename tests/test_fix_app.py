"""Gate: the queue must persist across app launches; .ffgui matching is case-insensitive."""
import pytest
from PySide6.QtCore import QSettings

import ffgui.app as app_mod
from fftui.ffmpeg.capability_index import CapabilityIndex

from ffgui.store import load_queue
from ffgui.ui.shell import Shell


@pytest.fixture()
def env(qapp, tmp_path, monkeypatch):
    monkeypatch.setenv("FFGUI_CACHE_DIR", str(tmp_path / "cache"))
    settings = QSettings(str(tmp_path / "s.ini"), QSettings.Format.IniFormat)
    return settings, tmp_path


def test_quit_saves_queue(qapp, env):
    settings, tmp = env
    c = app_mod._boot(qapp, Shell(), settings, CapabilityIndex.stub())
    c.add_paths([str(tmp / "clip.mp4")])
    qapp.aboutToQuit.emit()
    assert len(load_queue()) == 1


def test_boot_loads_persisted_queue(qapp, env):
    settings, tmp = env
    c = app_mod._boot(qapp, Shell(), settings, CapabilityIndex.stub())
    c.add_paths([str(tmp / "clip.mp4")])
    qapp.aboutToQuit.emit()
    c2 = app_mod._boot(qapp, Shell(), settings, CapabilityIndex.stub())
    assert len(c2.doc.items) == 1


def test_job_file_matching_is_case_insensitive(qapp, tmp_path, monkeypatch):
    monkeypatch.setenv("FFGUI_CACHE_DIR", str(tmp_path / "cache"))
    job_file = tmp_path / "HOLIDAY.FFGUI"
    job_file.write_text("{}", encoding="utf-8")
    seen = []
    monkeypatch.setattr("ffgui.controller.Controller.open_job_file",
                        lambda self, p: seen.append(p))
    assert app_mod.main(["--smoke", str(job_file)]) == 0
    assert seen == [str(job_file)]
