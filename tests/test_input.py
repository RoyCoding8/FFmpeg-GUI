"""Input-side seams: structural flags, value shapes, concat join, preset input.

None values coerce to clear per the tree-wide `_s` wire contract (locked in
test_hardening); what this file pins is everything around it: flags the queue
places itself, per-flag value grammars, join staging `-f concat -safe 0`, and
preset apply preserving the row's whole input.
"""

import pytest
from fftui.ffmpeg.capability_index import CapabilityIndex
from fftui.model import Input, Job, Output
from fftui.util.command_builder import build, sidecars_for

from ffgui.doc import QueueDocument, QueueItem


def _item(path="a.mp4"):
    return QueueItem(Job(inputs=[Input(path=path)], outputs=[Output(path="o.mp4")]),
                     {"name": "a", "enabled": True, "notes": "", "source_hash": ""})


@pytest.fixture()
def doc():
    d = QueueDocument(CapabilityIndex.stub(), prober=lambda p: Input(path=p))
    d.items.append(_item())
    return d


@pytest.fixture()
def ctrl(qapp, tmp_path, monkeypatch):
    from PySide6.QtCore import QSettings

    from ffgui.controller import Controller
    from ffgui.ui.shell import Shell

    monkeypatch.setenv("FFGUI_CACHE_DIR", str(tmp_path / "cache"))
    shell = Shell()
    d = QueueDocument(CapabilityIndex.stub(), prober=lambda p: Input(path=p))
    c = Controller(shell, d, CapabilityIndex.stub(),
                   QSettings(str(tmp_path / "s.ini"),
                             QSettings.Format.IniFormat))
    return shell, d, c, tmp_path


def test_set_input_arg_refuses_structural_flags(doc):
    for flag in ("-i", "-map", "-filter_complex"):
        assert doc.set_input_arg(0, flag, "x") is not None
    assert "invalid" in (doc.set_input_arg(0, None, "5") or "")
    assert doc.items[0].job.inputs[0].input_args == []
    assert doc.argv(0).count("-i") == 1


def test_set_input_arg_validates_known_value_shapes(doc):
    assert doc.set_input_arg(0, "-loop", "lots") is not None
    assert doc.set_input_arg(0, "-ss", "not-a-time") is not None
    assert doc.items[0].job.inputs[0].input_args == []
    assert doc.set_input_arg(0, "-loop", "-1") is None
    assert doc.set_input_arg(0, "-ss", "00:01:00") is None
    assert doc.set_input_arg(0, "-itsoffset", "1.5") is None
    argv = doc.argv(0)
    assert "-loop" in argv and "-itsoffset" in argv


def test_set_input_arg_set_twice_replaces_without_duplicating(doc):
    assert doc.set_input_arg(0, "-ss", "10") is None
    assert doc.set_input_arg(0, "-loop", "1") is None
    assert doc.set_input_arg(0, "-ss", "20") is None
    assert doc.items[0].job.inputs[0].input_args == ["-loop", "1", "-ss", "20"]


def test_set_input_arg_none_clears_per_wire_contract(doc):
    """None coerces to clear (tree-wide `_s`); clearing is not an error."""
    assert doc.set_input_arg(0, "-ss", "10") is None
    assert doc.set_input_arg(0, "-ss", None) is None
    assert doc.items[0].job.inputs[0].input_args == []


def test_join_parts_seeds_base_input_and_demuxer_flags(doc, tmp_path):
    pa, pb = tmp_path / "a.mp4", tmp_path / "b.mp4"
    pa.write_bytes(b"0")
    pb.write_bytes(b"0")
    doc.items[0].job.inputs[0].path = str(pa)
    assert doc.join_parts(0, [str(pb)]) is None
    inp = doc.items[0].job.inputs[0]
    assert inp.concat_paths == [str(pa), str(pb)]
    assert "-f" in inp.input_args and "concat" in inp.input_args
    argv = doc.argv(0)
    assert "-f" in argv and "concat" in argv
    (car,) = [c for c in sidecars_for(doc.items[0].job) if c.path.endswith(".txt")]
    assert pa.name in car.content and pb.name in car.content


def test_join_parts_rejects_bad_merges(doc, tmp_path):
    pa, pb = tmp_path / "a.mp4", tmp_path / "b.mp4"
    pa.write_bytes(b"0")
    pb.write_bytes(b"0")
    doc.items[0].job.inputs[0].path = str(pa)
    assert doc.join_parts(0, []) is not None
    assert doc.join_parts(0, None) is not None
    assert doc.join_parts(0, "ab") is not None
    assert doc.join_parts(0, [str(tmp_path / "nope.mp4")]) is not None
    assert doc.join_parts(0, ["has\nnewline.mp4"]) is not None
    assert doc.items[0].job.inputs[0].concat_paths == []
    assert doc.join_parts(0, [str(pa), str(pb), str(pb)]) is None
    assert doc.items[0].job.inputs[0].concat_paths == [str(pa), str(pb)]
    assert doc.join_parts(0, ["https://example.com/v.mp4"]) is None
    assert len(doc.items[0].job.inputs[0].concat_paths) == 3


def test_concat_dialog_joins_through_doc(ctrl, tmp_path):
    from unittest.mock import patch

    shell, doc, c, _ = ctrl
    pa, pb = tmp_path / "a.mp4", tmp_path / "b.mp4"
    pa.write_bytes(b"0")
    pb.write_bytes(b"0")
    c.add_paths([str(pa)])
    shell.queue.selectRow(0)
    with patch("ffgui.controller.QFileDialog.getOpenFileNames",
               return_value=([str(pb)], "")):
        c.concat_parts_dialog()
    inp = doc.items[0].job.inputs[0]
    assert inp.concat_paths == [str(pa), str(pb)]
    assert "concat" in shell.preview.toPlainText()


def test_apply_preset_keeps_row_input_extras(ctrl, tmp_path):
    from ffgui.store import save_preset

    shell, doc, c, _ = ctrl
    pa = tmp_path / "row.mp4"
    pa.write_bytes(b"0")
    c.add_paths([str(pa)])
    preset = Job(inputs=[Input(path="preset.mp4", decoder_v="h264_cuvid",
                               input_args=["-ss", "5"], concat_paths=["x.mp4"])],
                 outputs=[Output(path="p_out.mp4", video_options={"crf": "22"})])
    save_preset("p", preset, {"name": "p", "enabled": True, "notes": "",
                              "source_hash": ""})
    shell.queue.selectRow(0)
    c.apply_preset("p")
    inp = doc.items[0].job.inputs[0]
    assert inp.path == str(pa)
    assert (inp.decoder_v, inp.input_args, inp.concat_paths) == ("", [], [])
    assert doc.items[0].job.outputs[0].video_options["crf"] == "22"
    assert build(doc.items[0].job)
