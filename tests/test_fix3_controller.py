"""Gate: stream language tags clear; presets reject unhealthy rows and never
change a row's output structure; selecting an error row clears the tabs."""
from PySide6.QtCore import QSettings
from PySide6.QtWidgets import QInputDialog
from fftui.ffmpeg.capability_index import CapabilityIndex
from fftui.model import Input, InputStream, Job, Output

from ffgui.controller import Controller
from ffgui.doc import QueueDocument, QueueItem
from ffgui.store import fresh_meta
from ffgui.ui.shell import Shell


def test_streamtag_clears_language(wired):
    shell, doc, c, tmp = wired
    c.add_paths([str(tmp / "alpha.mp4")])
    meta = c.tabs["Metadata"]
    meta.stream_spec.setText("v:0")
    meta.stream_lang.setText("eng")
    assert c._apply_advanced("streamtag", "language", "eng", 0) is None
    assert doc.items[0].job.outputs[0].stream_metadata == {"v:0|language": "eng"}
    meta.stream_lang.setText("")
    assert c._apply_advanced("streamtag", "language", "", 0) is None
    assert doc.items[0].job.outputs[0].stream_metadata == {}


def test_save_preset_refuses_unhealthy_row(wired, monkeypatch):
    shell, doc, c, tmp = wired
    from ffgui.store import load_presets
    c.add_paths([str(tmp / "alpha.mp4")])
    doc.items.append(QueueItem(
        Job(inputs=[], outputs=[]), fresh_meta("bad.mp4"), error="probe failed"))
    shell.queue.selectRow(1)
    monkeypatch.setattr(QInputDialog, "getText",
                        staticmethod(lambda *a, **k: ("px", True)))
    c.save_preset_dialog()
    assert "px" not in load_presets()


def test_apply_preset_drops_extra_preset_outputs(wired):
    shell, doc, c, tmp = wired
    from ffgui.store import delete_preset, save_preset
    c.add_paths([str(tmp / "alpha.mp4"), str(tmp / "beta.mp4")])
    doc.items[0].job.outputs.append(Output(path="second.mp4"))
    save_preset("px", doc.items[0].job, doc.items[0].meta)
    shell.queue.selectRow(1)
    c.apply_preset("px")
    delete_preset("px")
    assert [o.path for o in doc.items[1].job.outputs] == ["beta_out.mp4"]


def test_selecting_error_row_clears_tabs(qapp, tmp_path, monkeypatch):
    monkeypatch.setenv("FFGUI_CACHE_DIR", str(tmp_path / "cache"))

    def probe(path):
        if "bad" in str(path):
            raise ValueError("probe failed")
        return Input(path=str(path), streams=[
            InputStream(input_index=0, spec="v:0", codec_type="video",
                        codec_name="h264", width=64, height=48)])

    settings = QSettings(str(tmp_path / "settings.ini"), QSettings.Format.IniFormat)
    shell = Shell()
    doc = QueueDocument(CapabilityIndex.stub(), prober=probe)
    c = Controller(shell, doc, CapabilityIndex.stub(), settings)
    c.add_paths([str(tmp_path / "good.mp4"), str(tmp_path / "bad.mp4")])
    shell.queue.selectRow(0)
    assert c.tabs["Container"].name.text() != ""
    shell.queue.selectRow(1)
    assert c.tabs["Container"].name.text() == ""
