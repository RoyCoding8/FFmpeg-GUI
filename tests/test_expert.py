"""Expert tier: lazy option tree, editor dispatch, flags commit, routing gate."""

import pytest
from PySide6.QtWidgets import QAbstractItemView
from fftui.ffmpeg.capability_index import CapabilityIndex
from fftui.model import Input, Job, Option, OptionChoice, Output
from fftui.util.command_builder import build
from fftui.util.validation import validate_option

from ffgui.ui.expert import OptionTreeModel, editor_kind


_movflags = Option(name="movflags", type="flags",
                   choices=[OptionChoice("faststart", 1),
                            OptionChoice("use_metadata_tags", 2)])


def test_numeric_bitmask_accepted():
    assert validate_option(_movflags, "1") is None
    assert validate_option(_movflags, "+faststart") is None


def stub_cap():
    return CapabilityIndex.stub()


def test_model_kind_rows(qapp):
    model = OptionTreeModel(stub_cap())
    assert model.rowCount() == 7


def test_model_component_rows_and_lazy_load(qapp):
    calls = []
    cap = stub_cap()
    real = cap.options_for

    def counting(kind, name):
        calls.append((kind, name))
        return real(kind, name)

    cap.options_for = counting
    model = OptionTreeModel(cap)
    enc = model.index(0, 0)
    assert enc.data() == "encoder"
    assert not calls, "options_for must not run before expansion"
    assert model.rowCount(enc) == 0
    assert model.canFetchMore(enc)
    model.fetchMore(enc)
    assert model.rowCount(enc) == len([e.name for e in cap.encoders])
    assert not calls, "component listing must not probe per-component docs"

    component = model.index(0, 0, enc)
    model.fetchMore(component)
    assert calls, "options_for runs on component expansion"


def test_set_target_locates_component(qapp):
    model = OptionTreeModel(stub_cap())
    index = model.set_target("encoder", "libx264")
    assert index.isValid() and index.data() == "libx264"
    assert model.parent(index).data() == "encoder"


def test_set_target_rejects_unknown_kind_and_component(qapp):
    model = OptionTreeModel(stub_cap())
    assert not model.set_target("bogus", "libx264").isValid()
    assert not model.set_target("encoder", "no_such_component_xyz").isValid()
    assert not model.index(-1, 0).isValid()


@pytest.mark.parametrize(("opt", "want"), [
    (Option(name="a", type="int"), "spin"),
    (Option(name="a", type="int64"), "spin"),
    (Option(name="a", type="float"), "dspin"),
    (Option(name="a", type="double"), "dspin"),
    (Option(name="a", type="boolean"), "check"),
    (Option(name="a", type="string", choices=[OptionChoice("x", 0)]), "combo"),
    (Option(name="a", type="flags", choices=[OptionChoice("x", 1)]), "multicombo"),
    (Option(name="a", type="pix_fmt"), "pixfmt"),
    (Option(name="a", type="rational"), "text"),
    (Option(name="a", type="dictionary"), "text"),
    (Option(name="a", type="binary"), "text"),
    (Option(name="a", type="uint64"), "text"),
    (Option(name="a", type="channel_layout"), "text"),
    (Option(name="a", type="color"), "text"),
    (Option(name="a", type="duration"), "text"),
    (Option(name="a", type="video_rate"), "text"),
    (Option(name="a", type="image_size"), "text"),
    (Option(name="a", type="sample_fmt"), "text"),
    (Option(name="a", type=""), "text"),
])
def test_editor_dispatch_table(qapp, opt, want):
    assert editor_kind(opt) == want


def _commit(qapp, opt, value):
    """Drive delegate.setModelData over a three-node model path."""
    from PySide6.QtWidgets import QLineEdit
    from ffgui.ui.expert import OptionDelegate, _Node

    captured = {}
    delegate = OptionDelegate(lambda o, v: validate_option(o, v))
    delegate.committed.connect(lambda k, c, o, v: captured.update(kind=k, comp=c, opt=o, val=v))
    delegate.rejected.connect(lambda msg: captured.update(error=msg))
    component = _Node("libx264", _Node("encoder"))
    node = _Node(opt.name, component, opt)
    component.children = [node]
    index = type("FakeIndex", (), {"internalPointer": lambda self: node})()

    class FakeModel:
        def setData(self, index, value):
            captured["stored"] = value
            return True

    editor = QLineEdit()
    editor.setText(value)
    delegate.setModelData(editor, FakeModel(), index)
    return captured


def test_text_commit(qapp):
    captured = _commit(qapp, Option(name="crf", type="int"), "23")
    assert captured["val"] == "23" and captured["stored"] == "23"
    assert captured["kind"] == "encoder" and captured["comp"] == "libx264"


def test_text_commit_rejected(qapp):
    captured = _commit(qapp, Option(name="threads", type="int"), "abc")
    assert "error" in captured and "stored" not in captured


def test_flags_multiselect_commit(qapp):
    from PySide6.QtWidgets import QListWidget
    from ffgui.ui.expert import OptionDelegate, _Node

    captured = {}
    delegate = OptionDelegate(lambda o, v: validate_option(o, v))
    delegate.committed.connect(lambda k, c, o, v: captured.update(val=v))
    component = _Node("libx264", _Node("encoder"))
    node = _Node("movflags", component, _movflags)
    component.children = [node]

    editor = QListWidget()
    editor.setSelectionMode(QAbstractItemView.SelectionMode.MultiSelection)
    editor.addItem("faststart")
    editor.addItem("use_metadata_tags")
    editor.item(0).setSelected(True)
    editor.item(1).setSelected(True)

    index = type("FakeIndex", (), {"internalPointer": lambda self: node})()

    class FakeModel:
        def setData(self, index, value):
            return True

    delegate.setModelData(editor, FakeModel(), index)
    assert captured["val"] == "+faststart+use_metadata_tags"


def test_filter_option_never_bare_argv(qapp):
    """filter:* component options route into the filter expression, not argv."""
    import os
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from ffgui.controller import Controller
    from ffgui.doc import QueueDocument, QueueItem
    from ffgui.ui.shell import Shell
    from PySide6.QtCore import QSettings

    shell = Shell()
    doc = QueueDocument(stub_cap(), prober=lambda p: Input(path=p))
    c = Controller(shell, doc, stub_cap(), QSettings("t2", "t2"))
    doc.items.append(QueueItem(
        Job(inputs=[Input(path="a.mp4")], outputs=[Output(path="o.mp4")]),
        {"name": "a", "enabled": True, "notes": "", "source_hash": ""}))
    c.refresh()
    shell.queue.selectRow(0)
    opt = Option(name="width", type="int")
    c._apply_expert("filter", "scale", opt, "640")
    argv = build(doc.items[0].job)
    assert not any(tok == "-w" or tok == "640" for tok in argv)
    assert any("scale=width=640" in tok or "scale=640" in tok for tok in argv)


def test_fetchmore_offline_shows_empty_component(qapp):
    """A raising runner (no ffmpeg on PATH) must yield an empty, loaded node."""
    from PySide6.QtCore import QModelIndex

    for exc in (RuntimeError("ffmpeg not found on PATH"), OSError("denied")):
        cap = stub_cap()

        def raiser(kind, name, _e=exc):
            raise _e

        cap.options_for = raiser
        model = OptionTreeModel(cap)
        enc = model.index(0, 0)
        model.fetchMore(enc)
        comp = model.index(0, 0, enc)
        model.fetchMore(comp)
        node = comp.internalPointer()
        assert node.loaded and node.children == []
        assert model.rowCount(comp) == 0
    model = OptionTreeModel(stub_cap())
    model.fetchMore(QModelIndex())
    assert not model.canFetchMore(QModelIndex())


def test_unknown_component_returns_empty_without_raising(qapp):
    """With ffmpeg present, an unknown component yields '' -> [] (no raise)."""
    cap = CapabilityIndex(version_hash="x", _runner=lambda a: "")
    assert cap.options_for("encoder", "no_such_encoder_xyz") == []


def test_parent_rows_follow_qt_contract(qapp):
    """parent() must return the parent's own row, not the child's — labels alone
    mask this (data() reads the internal pointer) while upward navigation lands
    on the wrong sibling."""
    from ffgui.ui.expert import KINDS
    cap = stub_cap()
    cap.options_for = lambda kind, name: [Option(name=f"opt{i}", type="string")
                                          for i in range(5)]
    model = OptionTreeModel(cap)
    kind = model.index(0, 0)
    model.fetchMore(kind)
    comp_name = cap.encoders[2].name
    comp = model.set_target("encoder", comp_name)
    assert comp.row() != 0

    assert model.parent(comp).row() == kind.row() == KINDS.index("encoder")
    assert model.parent(comp).internalPointer() is kind.internalPointer()
    model.fetchMore(comp)
    opt = model.index(3, 0, comp)
    parent = model.parent(opt)
    assert parent.row() == comp.row()
    assert parent.internalPointer() is comp.internalPointer()
    assert model.index(parent.row(), 0, kind).internalPointer() \
        is comp.internalPointer()
    assert not model.parent(kind).isValid()


def test_curation_overlays_labels_without_removing(qapp, tmp_path):
    from ffgui.curation import label_of, load
    yaml_text = '"encoder:libx264:crf":\n  label: Quality (CRF)\n'
    (tmp_path / "c.yaml").write_text(yaml_text, encoding="utf-8")
    cur = load(tmp_path)
    assert label_of(cur, "encoder", "libx264", "crf") == "Quality (CRF)"
    assert label_of(cur, "encoder", "libx264", "unheard-of") == "unheard-of"
    model = OptionTreeModel(stub_cap())
    kind = model.index(0, 0)
    model.fetchMore(kind)
    model.fetchMore(model.set_target("encoder", "libx264"))
    assert model.rowCount() == 7, "curation must not change row counts"


def test_index_rejects_out_of_range_column(qapp):
    """index() must be invalid past columnCount(): data()/hasChildren() only
    know columns 0 and 1, so a valid column-99 index breaks the contract."""
    model = OptionTreeModel(stub_cap())
    assert not model.index(0, 2).isValid()
    assert not model.index(0, 99).isValid()
    assert model.index(0, 1).isValid()
    assert not model.index(-1, 0).isValid()


def test_header_rejects_out_of_range_section(qapp):
    """headerData() must return None past columnCount(), not raise IndexError
    on the ("Option", "Value") tuple (same class as the index() column-99 fix)."""
    from PySide6.QtCore import Qt
    model = OptionTreeModel(stub_cap())
    assert model.headerData(0, Qt.Orientation.Horizontal, Qt.ItemDataRole.DisplayRole) == "Option"
    assert model.headerData(1, Qt.Orientation.Horizontal, Qt.ItemDataRole.DisplayRole) == "Value"
    assert model.headerData(2, Qt.Orientation.Horizontal, Qt.ItemDataRole.DisplayRole) is None
    assert model.headerData(99, Qt.Orientation.Horizontal, Qt.ItemDataRole.DisplayRole) is None
    assert model.headerData(-1, Qt.Orientation.Horizontal, Qt.ItemDataRole.DisplayRole) is None
