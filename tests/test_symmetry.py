"""Write/load symmetry: every tab widget's edit path and load() resolve to the
same document slot. Each test drives the controller edit path, repopulates the
tab, and asserts equality — red on any forked slot."""

import pytest
from PySide6.QtCore import QSettings
from fftui.ffmpeg.capability_index import CapabilityIndex
from fftui.model import Input, Job, Output
from fftui.util.command_builder import build

from ffgui.controller import Controller
from ffgui.doc import QueueDocument, QueueItem
from ffgui.ui.shell import Shell


def _item():
    job = Job(inputs=[Input(path="a.mp4")], outputs=[Output(path="o.mp4")])
    return QueueItem(job, {"name": "a", "enabled": True, "notes": "",
                           "source_hash": ""})


@pytest.fixture()
def ctl(qapp, tmp_path):
    shell = Shell()
    doc = QueueDocument(CapabilityIndex.stub(), prober=lambda p: Input(path=p))
    doc.items.append(_item())
    c = Controller(shell, doc, CapabilityIndex.stub(),
                   QSettings(str(tmp_path / "s.ini"),
                             QSettings.Format.IniFormat))
    c.refresh()
    shell.queue.selectRow(0)
    return shell, doc, c


def out_of(doc):
    return doc.items[0].job.outputs[0]


def test_faststart_round_trips_through_tab(ctl):
    shell, doc, c = ctl
    tab = c.tabs["Video"]
    c._on_tab_edit("faststart", "", "True")
    tab.populate(out_of(doc), doc.items[0].job)
    assert tab.faststart.isChecked()
    assert out_of(doc).options.get("movflags") == "+faststart"
    assert "movflags" not in out_of(doc).video_options
    c._on_tab_edit("faststart", "", "False")
    tab.populate(out_of(doc), doc.items[0].job)
    assert not tab.faststart.isChecked()
    assert "movflags" not in out_of(doc).options


def test_faststart_shares_mux_slot_with_expert(ctl):
    """Checkbox and expert muxer movflags are one slot: no duplicate flag."""
    shell, doc, c = ctl
    assert doc.set_option(0, "mux", "movflags", "+frag_keyframe") is None
    c._on_tab_edit("faststart", "", "True")
    tab = c.tabs["Video"]
    tab.populate(out_of(doc), doc.items[0].job)
    assert tab.faststart.isChecked()
    assert out_of(doc).options["movflags"] == "+faststart+frag_keyframe"
    argv = build(doc.items[0].job)
    assert argv.count("-movflags") == 1
    c._on_tab_edit("faststart", "", "False")
    assert out_of(doc).options["movflags"] == "+frag_keyframe"


def test_faststart_off_pops_key_without_sentinel(ctl):
    shell, doc, c = ctl
    c._on_tab_edit("faststart", "", "True")
    c._on_tab_edit("faststart", "", "False")
    assert "movflags" not in out_of(doc).options
    assert "movflags" not in out_of(doc).video_options


@pytest.mark.parametrize("kind,key,value", [
    ("hwdevice", "", 'x"y'),
    ("tee", "", 'x"y'),
    ("segment_time", "", "1\n2"),
    ("bsf", "", 'v:0=x"y'),
])
def test_routed_slots_reject_hostile(ctl, kind, key, value):
    shell, doc, c = ctl
    err = c._apply_advanced(kind, key, value, 0)
    assert err and "quotes or newlines" in err
    job = doc.items[0].job
    assert job.filter_hw_device is None
    assert job.outputs[0].tee_spec == ""
    assert job.outputs[0].segment_time is None
    assert job.outputs[0].bsf == {}


def test_expert_bsf_commit_is_validated(ctl):
    from fftui.model import Option
    shell, doc, c = ctl
    opt = Option(name="h264_mp4toannexb", type="string")
    err = c._apply_expert("bsf", "h264_mp4toannexb", opt, 'v"')
    assert err and "quotes" in err
    assert out_of(doc).bsf == {}
    assert c._apply_expert("bsf", "v:0=h264_mp4toannexb", None, "") is None
    assert out_of(doc).bsf == {"v:0": "h264_mp4toannexb"}


def test_routed_edits_drop_parked_unparsed(ctl):
    """Every routed commit converts the row: bypass-era itemChanged.emit left
    the parked payload so save() resurrected the stale row."""
    from ffgui.store import UnparsedRow
    shell, doc, c = ctl
    meta = c.tabs["Metadata"]
    meta.stream_spec.setText("v:0")
    meta.stream_lang.setText("eng")
    edits = [
        lambda: c._apply_advanced("hwdevice", "", "gpu0", 0),
        lambda: c._apply_advanced("tee", "", "[f=mp4]a.mp4", 0),
        lambda: c._apply_advanced("bsf", "", "v:0=h264_mp4toannexb", 0),
        lambda: c._apply_advanced("segment", "", "True", 0),
        lambda: c._apply_advanced("segment_time", "", "60", 0),
        lambda: c._apply_advanced("faststart", "", "True", 0),
        lambda: c._apply_advanced("filters", "video_filters", "scale=1:1", 0),
        lambda: c._apply_advanced("chapters", "", "0\x1fIntro\x1feng", 0),
        lambda: c._apply_advanced("streamtag", "", "", 0),
        lambda: c._apply_advanced("input", "-ss", "5", 0),
        lambda: c._apply_advanced("burn", "", "subs.srt", 0),
        lambda: c._apply_advanced("volume", "", "-6", 0),
        lambda: c._apply_advanced("loudnorm", "", "True", 0),
        lambda: c._apply_advanced("hwdecode", "", "", 0),
        lambda: c._apply_codec(0, "video", "libx264"),
        lambda: c._apply_option(0, "video:crf", "23"),
        lambda: c._apply_field(0, "title", "hi"),
        lambda: c._apply_flag(0, "two_pass", "True"),
        lambda: c._apply_flag(0, "noconfirm", "True"),
        lambda: c._apply_expert("bsf", "v:0=h264_mp4toannexb", None, ""),
    ]
    for edit in edits:
        doc.items[0].unparsed = UnparsedRow({"outputs": 7})
        assert edit() is None
        assert doc.items[0].unparsed is None
        doc.items[0].job.outputs[0].tee_spec = ""
        doc.items[0].job.outputs[0].bsf.clear()


def test_subtitle_combo_round_trips_without_none(ctl, qapp):
    """'none' is the Drop checkbox's wire value, not a codec: offering it in
    the combo wrote a value load() could never restore."""
    from ffgui.ui.tabs import SUBTITLE_CODECS
    assert "none" not in SUBTITLE_CODECS
    shell, doc, c = ctl
    tab = c.tabs["Subtitles"]
    for codec in SUBTITLE_CODECS:
        assert doc.set_codec(0, "subtitle", codec) is None
        tab.populate(out_of(doc), doc.items[0].job)
        assert tab.codec.currentText() == codec, codec
        assert not tab.drop.isChecked(), codec
    assert doc.set_codec(0, "subtitle", "none") is None
    tab.populate(out_of(doc), doc.items[0].job)
    assert tab.drop.isChecked()
    assert doc.set_codec(0, "subtitle", "") is None
    tab.populate(out_of(doc), doc.items[0].job)
    assert not tab.drop.isChecked()
