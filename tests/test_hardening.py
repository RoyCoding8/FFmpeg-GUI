"""Phase 7 hardening: store fuzz, six states, keyboard map, persistence, Expert perf."""

import json
import time

from PySide6.QtCore import QSettings, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QLineEdit

from ffgui.store import GUI_SCHEMA, import_tui_queue, load_queue
from ffgui.ui import theme
from ffgui.ui.shell import Shell


def _write(tmp_path, text):
    (tmp_path / "queue.json").write_text(text, encoding="utf-8")


def test_fuzz_bare_array_quarantines(tmp_path):
    _write(tmp_path, "[1, 2]")
    assert load_queue(tmp_path) == []
    assert list(tmp_path.glob("queue.json.bad*"))


def test_fuzz_extra_top_level_keys_quarantine(tmp_path):
    _write(tmp_path, json.dumps({"schema": GUI_SCHEMA, "rows": [], "extra": 1}))
    assert load_queue(tmp_path) == []
    assert list(tmp_path.glob("queue.json.bad*"))


def test_fuzz_future_schema_quarantines(tmp_path):
    _write(tmp_path, json.dumps({"schema": GUI_SCHEMA + 1, "rows": []}))
    assert load_queue(tmp_path) == []
    assert list(tmp_path.glob("queue.json.bad*"))


def test_fuzz_tui_jobs_with_extra_keys_import(tmp_path):
    tui = tmp_path / "tui"
    tui.mkdir()
    (tui / "queue.json").write_text(json.dumps(
        {"schema": 1, "jobs": [{"inputs": [{"path": "a.mp4", "future": 1}],
                                "outputs": [{"path": "o.mp4"}], "bonus": True}]}),
        encoding="utf-8")
    imported, _, errors = import_tui_queue(tui, tmp_path / "gui")
    assert imported == 1 and not errors


def test_six_states_reachable(qapp):
    shell = Shell()
    shell.show()
    for state in ("workspace", "loading", "no-ffmpeg"):
        shell.set_state(state)
        assert shell.state_stack.currentWidget() is not None
        assert shell.grab().toImage().bits() is not None
    shell.set_state("workspace")
    assert shell.empty.isVisible() and shell.queue_stack.currentIndex() == 0
    shell.show_empty(False)
    assert shell.queue_stack.currentIndex() == 1


def test_invalid_option_inline_state(qapp):
    edit = QLineEdit()
    from ffgui.ui.shell import mark_invalid
    mark_invalid(edit, "crf wants an integer")
    assert edit.property("invalid") is True
    assert "integer" in edit.toolTip()
    mark_invalid(edit, None)
    assert edit.property("invalid") is False


def test_keyboard_map(wired):
    shell, doc, c, tmp = wired
    queue_actions = {a.text(): a.shortcut().toString() for a in shell.queue.actions()}
    file_menu = shell.menuBar().actions()[0].menu()
    menu_actions = {a.text(): a.shortcut().toString() for a in file_menu.actions()}
    assert menu_actions.get("&Add files…") == "Ctrl+O"
    assert queue_actions.get("Remove selected") in ("Del", "Backspace, Del", "Del, Backspace")
    assert shell.export_btn.text().startswith("&Export")
    assert shell.run_btn.text().startswith("&Run")


def test_arrow_keys_navigate_queue(qapp, tmp_path, monkeypatch):
    from fftui.ffmpeg.capability_index import CapabilityIndex
    from fftui.model import Input, InputStream

    from ffgui.controller import Controller
    from ffgui.doc import QueueDocument

    monkeypatch.setenv("FFGUI_CACHE_DIR", str(tmp_path / "c"))
    shell = Shell()
    shell.show()

    def probe(path):
        return Input(path=path, streams=[InputStream(
            input_index=0, spec="v:0", codec_type="video", codec_name="h264",
            width=8, height=8)])

    doc = QueueDocument(CapabilityIndex.stub(), prober=probe)
    controller = Controller(shell, doc, CapabilityIndex.stub(),
                            QSettings(str(tmp_path / "s.ini"), QSettings.Format.IniFormat))
    controller.add_paths([str(tmp_path / "a.mp4"), str(tmp_path / "b.mp4")])
    for p in (tmp_path / "a.mp4", tmp_path / "b.mp4"):
        p.write_bytes(b"0")
    controller.add_paths([str(tmp_path / "a.mp4"), str(tmp_path / "b.mp4")])
    shell.queue.selectRow(0)
    QTest.keyClick(shell.queue, Qt.Key.Key_Down)
    assert controller.selected_rows() == [1]


def test_window_state_persistence(qapp, tmp_path):
    from ffgui.app import _save_state
    settings = QSettings(str(tmp_path / "w.ini"), QSettings.Format.IniFormat)
    shell = Shell()
    shell.resize(1000, 700)
    shell.tabs.setCurrentIndex(3)
    shell.splitter.setSizes([220, 780])
    _save_state(settings, shell)
    settings.sync()

    other = Shell()
    other.resize(1280, 800)
    other.show()
    if geometry := settings.value("geometry"):
        other.restoreGeometry(geometry)
    if sizes := settings.value("splitter"):
        other.splitter.setSizes([int(s) for s in sizes])
    other.tabs.setCurrentIndex(int(settings.value("tab", 0)))
    assert other.tabs.currentIndex() == 3
    assert abs(other.splitter.sizes()[0] - 220) <= 8


def test_expert_tree_expands_7000_options_under_200ms(qapp):
    from types import SimpleNamespace

    from ffgui.ui.expert import OptionTreeModel
    from fftui.model import Option

    opts = [Option(name=f"opt{i}", type="string") for i in range(7000)]
    cap = SimpleNamespace(
        encoders=[SimpleNamespace(name=f"enc{i}") for i in range(7000)],
        decoders=[], muxers=[], demuxers=[], filters=[], bsfs=[], protocols=[],
        pixel_formats=["yuv420p"],
        options_for=lambda kind, name: opts if name == "enc0" else [])
    model = OptionTreeModel(cap)
    enc = model.index(0, 0)
    model.fetchMore(enc)
    component = model.index(0, 0, enc)
    start = time.perf_counter()
    model.fetchMore(component)
    elapsed = time.perf_counter() - start
    assert model.rowCount(component) == 7000
    assert elapsed < 0.2, f"expansion took {elapsed * 1000:.0f} ms"


def test_theme_system_mode_reapplies_on_scheme_change(qapp):
    theme.apply_theme(qapp, "system")
    qapp.styleHints().colorSchemeChanged.emit(Qt.ColorScheme.Light)
    assert qapp.styleSheet()


def test_curation_skips_non_utf8_file(tmp_path):
    from ffgui.curation import load
    (tmp_path / "good.yaml").write_text(
        '"encoder:libx264:crf":\n  label: Quality\n', encoding="utf-8")
    (tmp_path / "bin.yaml").write_bytes(b"\xff\xfe\x00key: val\n")
    assert load(tmp_path) == {"encoder:libx264:crf": {"label": "Quality"}}


def test_curation_skips_non_mapping_documents(tmp_path):
    from ffgui.curation import load
    (tmp_path / "list.yaml").write_text("- just\n- a\n- list\n", encoding="utf-8")
    (tmp_path / "empty.yaml").write_text("", encoding="utf-8")
    (tmp_path / "good.yaml").write_text(
        '"encoder:libx264:crf":\n  label: Quality\n  group: G\n  extra: dropped\n',
        encoding="utf-8")
    assert load(tmp_path) == {"encoder:libx264:crf": {"label": "Quality",
                                                      "group": "G"}}


def test_curation_duplicate_keys_last_sorted_file_wins(tmp_path):
    from ffgui.curation import load
    (tmp_path / "a.yaml").write_text(
        '"enc:x264:crf":\n  label: First\n', encoding="utf-8")
    (tmp_path / "b.yaml").write_text(
        '"enc:x264:crf":\n  label: Second\n', encoding="utf-8")
    assert load(tmp_path) == {"enc:x264:crf": {"label": "Second"}}
    assert load(tmp_path / "missing-dir") == {}


def test_curation_drops_non_string_values(tmp_path):
    from ffgui.curation import label_of, load
    (tmp_path / "odd.yaml").write_text(
        '"encoder:x264:crf":\n  label:\n  group: Rate\n'
        '"encoder:x264:preset":\n  label: 123\n  group: 456\n',
        encoding="utf-8")
    cur = load(tmp_path)
    assert cur == {"encoder:x264:crf": {"group": "Rate"},
                   "encoder:x264:preset": {}}
    assert label_of(cur, "encoder", "x264", "crf") == "crf"
    assert label_of(cur, "encoder", "x264", "preset") == "preset"


def _hardening_doc(qapp):
    from fftui.ffmpeg.capability_index import CapabilityIndex
    from fftui.model import Input, Job, Output

    from ffgui.doc import QueueDocument, QueueItem
    doc = QueueDocument(CapabilityIndex.stub(), prober=lambda p: Input(path=p))
    doc.items.append(QueueItem(
        Job(inputs=[Input(path="a.mp4")], outputs=[Output(path="o.mp4")]),
        {"name": "a", "enabled": True, "notes": "", "source_hash": ""}))
    return doc


def test_doc_setters_tolerate_none(qapp):
    """None wire values must clear or report — never raise TypeError in a slot."""
    doc = _hardening_doc(qapp)
    assert doc.set_codec(0, "video", None) is None
    assert doc.set_option(0, "video", "crf", None) is None
    assert doc.set_output_field(0, "path", None) is not None
    assert doc.set_output_field(0, "container", None) is None
    assert doc.set_metadata(0, "title", None) is None
    assert doc.set_filters(0, "video_filters", None) is None
    assert doc.set_chapters(0, None) is None
    assert doc.set_hw_decode(0, None) is None
    assert doc.set_input_arg(0, "-ss", None) is None
    assert doc.set_stream_meta(0, "v:0", "language", None) is None
    assert doc.set_disposition(0, "v:0", None) is None
    assert doc.set_codec(0, "video", 123) is None
    assert doc.items[0].job.outputs[0].video_codec is None
    assert isinstance(doc.set_option(0, "video", 123, "1"), str)
    assert isinstance(doc.set_filters(0, "video_filters", [None]), str)
    assert isinstance(doc.set_stream_meta(0, None, "language", "eng"), str)
    assert isinstance(doc.set_disposition(0, None, "0"), str)
    assert isinstance(doc.set_input_arg(0, None, "5"), str)
    assert doc.bulk_set_option([0], "video", "crf", None) == {0: None}


def test_doc_add_files_tolerates_none_and_singles(qapp):
    from fftui.ffmpeg.capability_index import CapabilityIndex
    from fftui.model import Input

    from ffgui.doc import QueueDocument
    doc = QueueDocument(CapabilityIndex.stub(), prober=lambda p: Input(path=p))
    assert doc.add_files(None) == []
    assert doc.add_files([]) == []
    assert doc.add_files([None]) == []
    assert doc.add_files("solo.mp4") == [0]
    assert len(doc) == 1


def _hardening_controller(qapp, tmp_path, monkeypatch, doc=None):
    from fftui.ffmpeg.capability_index import CapabilityIndex

    from ffgui.controller import Controller
    from ffgui.ui.shell import Shell

    monkeypatch.setenv("FFGUI_CACHE_DIR", str(tmp_path / "c"))
    shell = Shell()
    controller = Controller(shell, doc or _hardening_doc(qapp),
                            CapabilityIndex.stub(),
                            QSettings(str(tmp_path / "s.ini"),
                                      QSettings.Format.IniFormat))
    return shell, controller


def test_bsf_empty_spec_defaults_to_v0(qapp, tmp_path, monkeypatch):
    """'=flt' carries no stream: like the Expert path it lands on v:0 instead
    of an empty-string spec key."""
    _, c = _hardening_controller(qapp, tmp_path, monkeypatch)
    doc = c.doc
    c._apply_advanced("bsf", "", "=flt", 0)
    assert doc.items[0].job.outputs[0].bsf == {"v:0": "flt"}
    c._apply_advanced("bsf", "", "", 0)
    assert doc.items[0].job.outputs[0].bsf == {}


def test_curation_load_accepts_str_dir(tmp_path):
    from ffgui.curation import load
    (tmp_path / "good.yaml").write_text(
        '"encoder:libx264:crf":\n  label: Quality\n', encoding="utf-8")
    assert load(str(tmp_path)) == {"encoder:libx264:crf": {"label": "Quality"}}


def test_concurrent_save_load_keeps_valid_store(tmp_path):
    import threading

    from fftui.model import Job

    from ffgui.store import load_queue, save_queue
    errs: list = []

    def writer(n):
        try:
            for i in range(20):
                save_queue([(Job.from_dict(
                    {"inputs": [{"path": f"{n}-{i}.mp4"}],
                     "outputs": [{"path": "o.mp4"}]}),
                    {"name": "r", "enabled": True, "notes": "",
                     "source_hash": f"{n}-{i}"})], tmp_path)
        except Exception as exc:  # noqa: BLE001
            errs.append(exc)

    def reader():
        try:
            for _ in range(40):
                assert isinstance(load_queue(tmp_path), list)
        except Exception as exc:  # noqa: BLE001
            errs.append(exc)

    threads = [threading.Thread(target=writer, args=(n,)) for n in range(4)]
    threads += [threading.Thread(target=reader) for _ in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert not errs
    assert isinstance(load_queue(tmp_path), list)


def test_chapters_malformed_line_rejected_not_raised(qapp, tmp_path, monkeypatch):
    """A chapter row without exactly start/title/lang must fail closed;
    None clears the list instead of raising out of the Qt slot."""
    _, controller = _hardening_controller(qapp, tmp_path, monkeypatch)
    for bad in ("just-a-title", "noseparators", "a\x1fb", "a\x1fb\x1fc\x1fd",
                "\x1f\x1fextra\x1fparts"):
        assert controller._apply_advanced("chapters", "k", bad, 0) is not None
    good = "00:00:01\x1fIntro\x1feng"
    assert controller._apply_advanced("chapters", "k", good, 0) is None
    assert controller._apply_advanced("chapters", "k", None, 0) is None
    assert controller.doc.items[0].job.outputs[0].chapters == []


def test_unknown_edit_kind_reports_loudly(qapp, tmp_path, monkeypatch):
    """An unrecognized edit kind must report (and touch nothing), never fall
    through to a silent no-op or a raw setattr."""
    _, controller = _hardening_controller(qapp, tmp_path, monkeypatch)
    assert controller._apply_advanced("bogus", "x", "v", 0) == "unknown edit 'bogus'"


def test_direct_writes_reject_hostile_value(qapp, tmp_path, monkeypatch):
    """Newline/quote hostile values through routed slots must report, never
    reach the model: the doc setters gate them before storing."""
    _, controller = _hardening_controller(qapp, tmp_path, monkeypatch)
    cases = (("hwdevice", "cuda\nINJECT"), ("segment_time", "1\n2"),
             ("bsf", "v:0=h264\nINJECT"), ("tee", 'x"\nevil'))
    for kind, bad in cases:
        assert controller._apply_advanced(kind, "k", bad, 0) is not None
    assert controller._apply_advanced("tee", "k", "[f=mp4]out.mp4", 0) is None


def test_expert_bsf_rejects_hostile_value(qapp, tmp_path, monkeypatch):
    from fftui.model import Option

    shell, controller = _hardening_controller(qapp, tmp_path, monkeypatch)
    shell.queue.selectRow(0)
    opt = Option(name="filter", type="string")
    assert controller._apply_expert("bsf", "v:0", opt, "h264\nINJECT") is not None


def test_expert_validate_keeps_reason_single_line(qapp):
    """Upstream echoes hostile values raw; the panel enforces the one-line
    contract before the message reaches the status bar/tooltip."""
    from fftui.ffmpeg.capability_index import CapabilityIndex
    from fftui.model import Option, OptionChoice

    from ffgui.ui.expert import ExpertPanel

    panel = ExpertPanel(CapabilityIndex.stub())
    flags = Option(name="movflags", type="flags",
                   choices=[OptionChoice("faststart", 1)])
    reason = panel._validate(flags, "bogus\nINJECT\x00")
    assert reason is not None
    assert "\n" not in reason and "\r" not in reason and "\x00" not in reason
    assert panel._validate(flags, "+faststart") is None


def test_faststart_segment_without_output_report(qapp):
    """Toggling faststart/segment on an output-less row must report, not
    raise AttributeError out of the Qt slot (the old code dereferenced None)."""
    from fftui.ffmpeg.capability_index import CapabilityIndex
    from fftui.model import Input, Job

    from ffgui.doc import QueueDocument, QueueItem
    doc = QueueDocument(CapabilityIndex.stub(), prober=lambda p: Input(path=p))
    doc.items.append(QueueItem(
        Job(inputs=[Input(path="a.mp4")], outputs=[]),
        {"name": "a", "enabled": True, "notes": "", "source_hash": ""}))
    assert doc.set_faststart(0, "True") == "row has no output yet"
    assert doc.set_segment_enabled(0, "True") == "row has no output yet"


def test_expert_check_editor_uses_shared_truthy(qapp):
    """The expert check editor shares store.is_truthy: 'yes' counts as on.
    Its own tuple once read ("1", "true", "on") and dropped 'yes'."""
    from PySide6.QtWidgets import QCheckBox
    from fftui.model import Option

    from ffgui.store import is_truthy
    from ffgui.ui.expert import OptionDelegate, _Node
    delegate = OptionDelegate(lambda o, v: None)
    component = _Node("libx264", _Node("encoder"))
    for text, want in (("yes", True), ("1", True), ("true", True),
                       ("on", True), ("", False), ("0", False)):
        node = _Node("somebool", component,
                     Option(name="somebool", type="boolean"))
        node.value = text
        editor = QCheckBox()
        index = type("FakeIndex", (),
                     {"internalPointer": lambda self, n=node: n})()
        delegate.setEditorData(editor, index)
        assert editor.isChecked() is want, text
        assert editor.isChecked() == is_truthy(text)


def test_hostile_set_single_sourced_true_contracts():
    """HOSTILE_CHARS is the one script-splice set — and `"` is deliberately
    NOT a member: shlex.quote renders it safely for `.sh`, only cmd.exe
    cannot quote it, so only `.bat` refuses it. An earlier convergence put
    `"` in the shared set and silently broke quoted POSIX filenames."""
    import shlex

    from ffgui.export.bat import bat_token
    from ffgui.export.model import HOSTILE_CHARS, contains_hostile
    from ffgui.export.sh import sh_token
    assert tuple(HOSTILE_CHARS) == ("\n", "\r", "\0")
    for bad in HOSTILE_CHARS:
        assert contains_hostile(f"a{bad}b")
    assert not contains_hostile("plain")
    assert sh_token('has"quote') == shlex.quote('has"quote')
    import pytest
    with pytest.raises(ValueError, match="cannot be represented"):
        bat_token('has"quote')


def test_faststart_and_unparsed_survive_save_reload(qapp, tmp_path):
    """The unified movflags slot and parked UnparsedRow state must survive a
    save/load cycle: toggling faststart then saving must reload one -movflags
    in argv, and an unparsed row must ride through untouched."""
    from fftui.model import Input, Job, Output
    from fftui.util.command_builder import build

    from ffgui.doc import QueueDocument, QueueItem
    from ffgui.store import UnparsedRow
    doc = _hardening_doc(qapp)
    assert doc.set_faststart(0, "True") is None
    doc.items.append(QueueItem(
        Job(inputs=[Input(path="b.mp4")], outputs=[Output(path="o2.mp4")]),
        {"name": "b", "enabled": True, "notes": "", "source_hash": ""},
        unparsed=UnparsedRow({"outputs": 7, "gui": {"name": "old"}})))
    doc.save(tmp_path / "q")
    fresh = QueueDocument.load(doc.cap, tmp_path / "q", prober=doc.prober)
    assert fresh.items[0].job.outputs[0].options.get("movflags") == "+faststart"
    assert build(fresh.items[0].job).count("-movflags") == 1
    assert isinstance(fresh.items[1].unparsed, UnparsedRow)


def test_unknown_edit_kind_reports_and_preserves_unparsed(qapp, tmp_path, monkeypatch):
    """An unrecognized edit kind must fail loudly — the old fallthrough
    returned None (fake success) AND marked the row edited, destroying
    parked UnparsedRow state. Every kind tabs.py emits is handled, so the
    fallthrough is reachable only by programmer error."""
    from ffgui.store import UnparsedRow
    _, c = _hardening_controller(qapp, tmp_path, monkeypatch)
    c.doc.items[0].unparsed = UnparsedRow({"outputs": 7})
    err = c._apply_advanced("bogus_kind", "k", "v", 0)
    assert err is not None and "bogus_kind" in err
    assert isinstance(c.doc.items[0].unparsed, UnparsedRow)


def test_label_of_tolerates_hostile_overlay_entries():
    """label_of must never crash or leak non-strings, even when the overlay
    dict is hand-built rather than load()-sanitized."""
    from ffgui.curation import label_of
    assert label_of({"encoder:x:y": "oops"}, "encoder", "x", "y") == "y"
    assert label_of({"encoder:x:y": None}, "encoder", "x", "y") == "y"
    assert label_of({"encoder:x:y": {"label": 7}}, "encoder", "x", "y") == "y"
    assert label_of({"encoder:x:y": {"label": "Nice"}}, "encoder", "x", "y") == "Nice"
    assert label_of({}, "encoder", "x", "missing") == "missing"
