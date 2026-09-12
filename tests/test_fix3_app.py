"""Gate: the loading page's Cancel abandons the probe and returns to the
no-ffmpeg state; a completed probe still boots."""
import pytest
from PySide6.QtCore import QSettings
from fftui.ffmpeg.capability_index import CapabilityIndex

import ffgui.app as app_mod
from ffgui.ui.shell import Shell


@pytest.fixture()
def wiring(qapp, tmp_path, monkeypatch):
    monkeypatch.setenv("FFGUI_CACHE_DIR", str(tmp_path / "cache"))
    settings = QSettings(str(tmp_path / "s.ini"), QSettings.Format.IniFormat)
    shell = Shell()
    handlers = {}
    monkeypatch.setattr(app_mod, "_launch_probe",
                        lambda sh, on_done, on_failed:
                        handlers.update(done=on_done, failed=on_failed))
    return settings, shell, handlers


def test_probe_cancel_abandons_probe(qapp, wiring, monkeypatch):
    settings, shell, handlers = wiring
    boots = []
    monkeypatch.setattr(app_mod, "_boot",
                        lambda app, sh, st, cap, job_files=None: boots.append(cap))
    app_mod._wire_probe(qapp, shell, settings, [])
    shell.loading.cancelled.emit()
    assert shell.state_stack.currentWidget() is shell.no_ffmpeg
    handlers["done"](CapabilityIndex.stub())
    assert boots == []


def test_probe_done_still_boots(qapp, wiring, monkeypatch):
    settings, shell, handlers = wiring
    boots = []
    monkeypatch.setattr(app_mod, "_boot",
                        lambda app, sh, st, cap, job_files=None: boots.append(cap))
    app_mod._wire_probe(qapp, shell, settings, [])
    cap = CapabilityIndex.stub()
    handlers["done"](cap)
    assert boots == [cap]
