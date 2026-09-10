"""Advanced tier: every catalogue row emits its documented flag through the document."""

import pytest
from fftui.ffmpeg.capability_index import CapabilityIndex
from fftui.model import Chapter, Input, Job, Output
from fftui.util.command_builder import build, build_two_pass

from ffgui.doc import QueueDocument


@pytest.fixture()
def doc(qapp):
    d = QueueDocument(CapabilityIndex.stub(), prober=lambda p: Input(path=p))
    d.items.append(_item())
    return d


def _item():
    from fftui.model import InputStream

    from ffgui.doc import QueueItem
    job = Job(inputs=[Input(path="a.mp4", streams=[
        InputStream(input_index=0, spec="v:0", codec_type="video", codec_name="h264"),
        InputStream(input_index=0, spec="a:0", codec_type="audio", codec_name="aac"),
        InputStream(input_index=0, spec="s:0", codec_type="subtitle", codec_name="subrip"),
    ])], outputs=[Output(path="o.mp4")])
    return QueueItem(job, {"name": "a", "enabled": True, "notes": "", "source_hash": ""})


def frag(doc, needle):
    argv = build(doc.items[0].job)
    assert any(needle in tok for tok in argv), (needle, argv)


@pytest.mark.parametrize(("scope", "key", "value", "flag"), [
    ("video", "crf", "23", "-crf"),
    ("video", "cq", "28", "-cq"),
    ("video", "b:v", "1500k", "-b:v"),
    ("video", "maxrate", "2M", "-maxrate"),
    ("video", "minrate", "500k", "-minrate"),
    ("video", "bufsize", "2M", "-bufsize"),
    ("video", "preset", "slow", "-preset"),
    ("video", "tune", "film", "-tune"),
    ("video", "profile:v", "high", "-profile:v"),
    ("video", "level", "4.1", "-level"),
    ("video", "g", "250", "-g"),
    ("video", "bf", "3", "-bf"),
    ("video", "refs", "4", "-refs"),
    ("video", "pix_fmt", "yuv420p10le", "-pix_fmt"),
    ("video", "r", "30", "-r"),
    ("video", "threads", "8", "-threads"),
    ("audio", "b:a", "192k", "-b:a"),
    ("audio", "ac", "2", "-ac"),
    ("audio", "ar", "48000", "-ar"),
])
def test_option_rows_emit_flags(doc, scope, key, value, flag):
    err = doc.set_option(0, scope, key, value)
    assert err is None, err
    frag(doc, flag)


def test_two_pass_builds_two_passes(doc):
    doc.set_option(0, "video", "b:v", "500k")
    doc.set_two_pass(0, True)
    argvs = build_two_pass(doc.items[0].job)
    assert len(argvs) == 2
    assert any("-pass" in tok for tok in argvs[0]) and any("-pass" in tok for tok in argvs[1])


def test_faststart_checkbox_reflects_mux_slot(doc, qapp):
    """One source of truth: the checkbox reads the mux slot, so an Expert-side
    movflags edit and the Video tab never disagree."""
    from ffgui.ui.tabs import VideoTab
    assert doc.set_option(0, "mux", "movflags", "+faststart") is None
    tab = VideoTab(CapabilityIndex.stub())
    tab.load(doc.items[0].job.outputs[0], doc.items[0].job)
    assert tab.faststart.isChecked()
    assert doc.set_option(0, "mux", "movflags", "") is None
    tab.load(doc.items[0].job.outputs[0], doc.items[0].job)
    assert not tab.faststart.isChecked()


@pytest.mark.parametrize(("expr", "needle"), [
    ("scale=640:360", "scale=640:360"),
    ("crop=640:480:10:10", "crop="),
    ("yadif", "yadif"),
    ("transpose=1", "transpose"),
    ("hqdn3d=4:3:6:4", "hqdn3d"),
    ("unsharp=5:5:1.0", "unsharp"),
    ("eq=brightness=0.06:saturation=1.5", "eq="),
    ("fade=t=in:st=0:d=1", "fade="),
])
def test_video_filters(doc, expr, needle):
    assert doc.set_filters(0, "video_filters", [expr]) is None
    argv = build(doc.items[0].job)
    assert "-vf" in argv and any(needle in tok for tok in argv)


def test_audio_filter_volume(doc):
    assert doc.set_filters(0, "audio_filters", ["volume=0.5"]) is None
    argv = build(doc.items[0].job)
    assert "-af" in argv and any("volume=0.5" in tok for tok in argv)


def test_loudnorm(doc):
    assert doc.set_filters(0, "audio_filters", ["loudnorm=I=-16:TP=-1.5:LRA=11"]) is None
    frag(doc, "loudnorm")


def test_filter_rejects_quotes(doc):
    err = doc.set_filters(0, "video_filters", ['scale=640:360"'])
    assert err and "quotes" in err


def test_watermark_overlay_via_filter_complex():
    from fftui.model import FilterComplexGraph, FilterComplexNode
    job = Job(inputs=[Input(path="a.mp4"), Input(path="wm.png")],
              outputs=[Output(path="o.mp4")])
    graph = FilterComplexGraph()
    graph.nodes.append(FilterComplexNode(input_pads=["0:v", "1:v"],
                                         filter_expr="overlay=10:10",
                                         output_pads=["vout"]))
    job.filter_complex = graph
    job.outputs[0].mappings = ["[vout]"]
    argv = build(job)
    assert "-filter_complex" in argv and any("overlay=10:10" in tok for tok in argv)


def test_burn_in_via_video_filter(doc):
    assert doc.set_filters(0, "video_filters", ["subtitles=subs.srt"]) is None
    frag(doc, "subtitles=subs.srt")


def test_external_subtitle_and_delay(doc):
    doc.items[0].job.inputs.append(Input(path="subs.srt"))
    doc.items[0].job.inputs[1].input_args += ["-itsoffset", "1.5"]
    argv = build(doc.items[0].job)
    assert "subs.srt" in argv and "-itsoffset" in argv


def test_chapters(doc):
    doc.set_chapters(0, [Chapter(start="0", title="Intro"), Chapter(start="60", title="Act 1")])
    argv = build(doc.items[0].job)
    assert "-map_chapters" in argv
    from fftui.util.command_builder import parse_ffmetadata, render_ffmetadata
    chapters = doc.items[0].job.outputs[0].chapters
    assert parse_ffmetadata(render_ffmetadata(chapters))[1].title == "Act 1"


def test_stream_tags_and_dispositions(doc):
    assert doc.set_stream_meta(0, "v:0", "language", "eng") is None
    assert doc.set_disposition(0, "s:0", "0") is None
    argv = build(doc.items[0].job)
    flag = argv.index("-metadata:s:v:0")
    assert argv[flag + 1] == "language=eng"
    assert "-disposition:s:0" in argv


def test_hw_decode_gated(doc):
    assert doc.set_hw_decode(0, "h264_cuvid") is None
    argv = build(doc.items[0].job)
    assert "h264_cuvid" in argv
    assert doc.set_hw_decode(0, 'bad"dec') is not None


def test_hw_encode_from_capability_index():
    cap = CapabilityIndex.stub()
    encoders = [e.name for e in cap.encoders]
    assert not any("nvenc" in e for e in encoders), "stub must gate nvenc away"


def test_segment(doc):
    out = doc.items[0].job.outputs[0]
    out.segment_enabled = True
    out.segment_time = "60"
    out.segment_format = "mp4"
    argv = build(doc.items[0].job)
    assert any("segment" in tok for tok in argv) and "60" in argv


def test_bsf(doc):
    doc.items[0].job.outputs[0].bsf["v:0"] = "h264_mp4toannexb"
    frag(doc, "h264_mp4toannexb")


def test_fast_input_seek_and_loop(doc):
    assert doc.set_input_arg(0, "-ss", "10") is None
    assert doc.set_input_arg(0, "-loop", "1") is None
    argv = build(doc.items[0].job)
    assert "-ss" in argv and "10" in argv and "-loop" in argv
    assert doc.set_input_arg(0, "-ss", "") is None
    argv = build(doc.items[0].job)
    assert "-ss" not in argv


def test_input_dash_value_round_trips_through_tab(doc):
    """Regression: _flag must not drop values starting with '-' on load."""
    from ffgui.ui.tabs import AdvancedTab, _flag

    assert _flag(["-ss", "-5"], "-ss") == "-5"
    assert _flag(["-loop", "-1"], "-loop") == "-1"
    assert doc.set_input_arg(0, "-loop", "-1") is None
    assert doc.set_input_arg(0, "-ss", "-5") is None
    tab = AdvancedTab(CapabilityIndex.stub())
    tab.populate(doc.items[0].job.outputs[0], doc.items[0].job)
    assert tab.loop.text() == "-1"
    assert tab.seek.text() == "-5"


def test_clearing_dash_valued_input_removes_whole_pair(doc):
    """Clearing -loop/-ss whose value starts with '-' must leave no orphan."""
    assert doc.set_input_arg(0, "-loop", "-1") is None
    assert doc.set_input_arg(0, "-loop", "") is None
    assert doc.items[0].job.inputs[0].input_args == []
    argv = build(doc.items[0].job)
    assert "-loop" not in argv and "-1" not in argv


def test_add_chapter_keeps_row_for_editing_without_premature_emit(qapp):
    """Add inserts an intentionally blank row without emitting: emitting an empty
    start would route through the controller, which rejects it and refreshes
    the row away."""
    from PySide6.QtWidgets import QPushButton

    from ffgui.ui.tabs import ChaptersTab
    tab = ChaptersTab(CapabilityIndex.stub())
    emitted = []
    tab.edit.connect(lambda kind, key, value: emitted.append(value))
    add = next(b for b in tab.findChildren(QPushButton) if b.text() == "Add chapter")
    add.click()
    assert tab.table.rowCount() == 1
    assert emitted == []
    from PySide6.QtWidgets import QTableWidgetItem
    tab.table.setItem(0, 0, QTableWidgetItem("12.5"))
    assert len(emitted) == 1
    (start, title, lang), = (r.split("\x1f") for r in emitted[0].split("\n") if r)
    assert start == "12.5" and title == ""

    from fftui.model import Chapter
    assert Chapter(start=start, title=title, lang=lang or "eng").lang == "eng"


def test_chapter_cells_sanitize_wire_separators(qapp):
    """A pasted newline/unit-separator in a cell must not corrupt the
    \\n-joined / \\x1f-separated encoding (phantom rows / unpack crash)."""
    from PySide6.QtWidgets import QTableWidgetItem

    from ffgui.ui.tabs import ChaptersTab
    tab = ChaptersTab(CapabilityIndex.stub())
    emitted = []
    tab.edit.connect(lambda kind, key, value: emitted.append(value))
    tab.table.insertRow(0)
    for c, text in enumerate(("0", "a\nb\x1fc", "")):
        tab.table.setItem(0, c, QTableWidgetItem(text))
    assert emitted
    rows = [r.split("\x1f") for r in emitted[-1].split("\n") if r]
    assert len(rows) == 1
    (start, title, lang), = rows
    assert start == "0" and "\n" not in title and "\x1f" not in title


def test_filter_chain_sanitizes_wire_breaks(qapp):
    """A pasted newline/CR inside one filter entry must not decode back as phantom
    filters (the controller splits the wire value on newline), so the tab
    sanitizes like ChaptersTab._emit."""
    from ffgui.ui.tabs import FiltersTab
    tab = FiltersTab(CapabilityIndex.stub())
    emitted = []
    tab.edit.connect(lambda kind, key, value: emitted.append(value))
    listing = tab.lists["video_filters"]
    listing.addItem("scale=640:360\ncrop=10:10\ryadif")
    tab._emit_chain("video_filters", listing)
    assert emitted
    assert "\n" not in emitted[-1] and "\r" not in emitted[-1]
    assert emitted[-1].split("\n") == ["scale=640:360 crop=10:10 yadif"]


def test_bsf_widget_shows_spec_filter_pair(qapp, doc):
    """The widget documents 'stream=filter' and the commit path partitions on
    '=' — showing only the filter value made re-commit silently drop the bsf."""
    from ffgui.ui.tabs import AdvancedTab
    tab = AdvancedTab(CapabilityIndex.stub())
    tab.populate(doc.items[0].job.outputs[0], doc.items[0].job)
    assert tab.bsf.text() == ""
    doc.items[0].job.outputs[0].bsf["v:0"] = "h264_mp4toannexb"
    tab.populate(doc.items[0].job.outputs[0], doc.items[0].job)
    assert tab.bsf.text() == "v:0=h264_mp4toannexb"
    spec, _, flt = tab.bsf.text().partition("=")
    assert (spec, flt) == ("v:0", "h264_mp4toannexb")


def test_tee_spec_rejected_with_segment(doc):
    out = doc.items[0].job.outputs[0]
    out.segment_enabled = True
    out.tee_spec = "[f=mp4]o.mp4"
    from fftui.errors import FFtuiError
    with pytest.raises(FFtuiError):
        build(doc.items[0].job)


def test_run_terminal_saves_then_launches(tmp_path, monkeypatch, qapp):
    from unittest.mock import patch
    from PySide6.QtCore import QSettings
    from ffgui.controller import Controller
    from ffgui.doc import QueueDocument
    from ffgui.ui.shell import Shell
    shell = Shell()
    doc = QueueDocument(CapabilityIndex.stub(), prober=lambda p: Input(path=p))
    c = Controller(shell, doc, CapabilityIndex.stub(), QSettings("t", "t"))
    doc.items.append(_item())
    c.refresh()
    launched = []
    monkeypatch.setattr("ffgui.controller.launch_script", lambda p: launched.append(p))
    target = tmp_path / "run.bat"
    with patch("ffgui.controller.QFileDialog.getSaveFileName",
               return_value=(str(target), "")):
        c.run_terminal()
    assert launched == [str(target)]
    assert target.is_file()
    assert target.read_bytes().decode("utf-8").endswith("pause\r\nendlocal & exit /b 1\r\n")


def test_hwdecode_restored_on_reselect(qapp):
    from fftui.ffmpeg.capability_index import CapabilityEntry

    from ffgui.ui.tabs import AdvancedTab
    cap = CapabilityIndex.stub()
    cap.decoders.append(CapabilityEntry("h264_cuvid", "D", "NVDEC H.264"))
    d = QueueDocument(cap, prober=lambda p: Input(path=p))
    d.items.append(_item())
    assert d.set_hw_decode(0, "h264_cuvid") is None
    tab = AdvancedTab(cap)
    tab.populate(d.items[0].job.outputs[0], d.items[0].job)
    assert tab.hwdecode.currentText() == "h264_cuvid"


def test_filter_entries_are_inline_editable(qapp):
    """FiltersTab promises inline editing, but default QListWidgetItem flags are
    not editable — double-click edits silently did nothing."""
    from PySide6.QtCore import Qt
    from ffgui.ui.tabs import FiltersTab
    tab = FiltersTab(CapabilityIndex.stub())
    tab._add("video_filters", "scale", "640:360")
    assert tab.lists["video_filters"].item(0).flags() & Qt.ItemFlag.ItemIsEditable
    from fftui.model import Job, Output
    from fftui.model import FilterChain
    job = Job(inputs=[], outputs=[Output(path="o.mp4")],
              video_filters=FilterChain(filters=["yadif"]))
    tab.load(job.outputs[0], job)
    assert tab.lists["video_filters"].item(0).flags() & Qt.ItemFlag.ItemIsEditable
