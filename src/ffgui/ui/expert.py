"""Expert tier: the full probed AVOption surface as a lazy, typed tree
(kind → component → option); nodes load on expansion, the delegate maps
Option.type to editors and gates commit through validate_option."""

from __future__ import annotations

import contextlib
import re
from collections.abc import Callable

from PySide6.QtCore import QAbstractItemModel, QModelIndex, Qt, Signal
from PySide6.QtWidgets import (
    QAbstractItemView, QCheckBox, QComboBox, QDoubleSpinBox, QHBoxLayout,
    QHeaderView, QLineEdit, QListWidget, QSpinBox, QStyledItemDelegate,
    QTreeView, QVBoxLayout, QWidget,
)

from fftui.model import Option
from fftui.util.validation import validate_option

from ffgui.curation import label_of, load
from ffgui.store import is_truthy
from ffgui.ui.tokens import SPACING

KINDS = ("encoder", "decoder", "muxer", "demuxer", "filter", "bsf", "protocol")
_PLURAL = {"encoder": "encoders", "decoder": "decoders", "muxer": "muxers",
           "demuxer": "demuxers", "filter": "filters", "bsf": "bsfs",
           "protocol": "protocols"}
_INT_SPIN = {"int", "int64", "integer", "long"}
_FLOAT_SPIN = {"float", "double"}
_INT_RE = re.compile(r"^-?\d+$")


def editor_kind(opt: Option) -> str:
    """The pure type→editor mapping the delegate implements."""
    t = (opt.type or "").strip().lower()
    if opt.choices:
        return "multicombo" if t == "flags" else "combo"
    if t in _INT_SPIN:
        return "spin"
    if t in _FLOAT_SPIN:
        return "dspin"
    if t == "boolean":
        return "check"
    if t == "pix_fmt":
        return "pixfmt"
    return "text"


class _Node:
    __slots__ = ("label", "opt", "parent", "children", "loaded", "value")

    def __init__(self, label: str, parent: _Node | None = None,
                 opt: Option | None = None):
        self.label = label
        self.parent = parent
        self.opt = opt
        self.children: list[_Node] = []
        self.loaded = False
        self.value = ""


class OptionTreeModel(QAbstractItemModel):
    """kind → component → option, with `options_for` deferred to fetchMore."""

    def __init__(self, cap, parent=None) -> None:
        super().__init__(parent)
        self.cap = cap
        self.curations = load()
        self.root = _Node("")
        self.root.children = [_Node(kind, self.root) for kind in KINDS]

    def _node(self, index: QModelIndex) -> _Node | None:
        return index.internalPointer() if index.isValid() else None

    def _depth(self, node: _Node) -> int:
        d = 0
        while node.parent is not None:
            d += 1
            node = node.parent
        return d

    def _components(self, kind: str) -> list[str]:
        return [getattr(e, "name", e) for e in getattr(self.cap, _PLURAL[kind], [])]

    def set_target(self, kind: str, component: str) -> QModelIndex:
        """Locate (and load) a component node, for tests and targeted expansion."""
        for k in self.root.children:
            if k.label == kind and not k.loaded:
                k.children = [_Node(name, k) for name in self._components(kind)]
                k.loaded = True
            if k.label == kind:
                for n, child in enumerate(k.children):
                    if child.label == component:
                        return self.createIndex(n, 0, child)
        return QModelIndex()


    def index(self, row: int, column: int, parent=QModelIndex()) -> QModelIndex:
        node = self._node(parent) or self.root
        if row < 0 or column not in (0, 1) or row >= len(node.children):
            return QModelIndex()
        return self.createIndex(row, column, node.children[row])

    def parent(self, index: QModelIndex) -> QModelIndex:
        node = self._node(index)
        if node is None or node.parent is None or node.parent is self.root:
            return QModelIndex()
        grandparent = node.parent.parent
        if grandparent is None:
            return QModelIndex()
        return self.createIndex(
            grandparent.children.index(node.parent), 0, node.parent)

    def rowCount(self, parent=QModelIndex()) -> int:
        return len(self._node(parent).children) if parent.isValid() else len(self.root.children)

    def columnCount(self, parent=QModelIndex()) -> int:
        return 2

    def data(self, index: QModelIndex, role=Qt.ItemDataRole.DisplayRole):
        node = self._node(index)
        if node is None or role != Qt.ItemDataRole.DisplayRole:
            return None
        if self._depth(node) == 3:
            if index.column() == 0:
                return label_of(self.curations, node.parent.parent.label,
                                node.parent.label, node.opt.name)
            if node.value:
                return node.value
            opt = node.opt
            return (f"{opt.type}"
                    f"{' — ' + opt.help if opt.help else ''}"
                    f"{' (default ' + opt.default + ')' if opt.default else ''}")
        if index.column() == 0:
            return node.label
        return None

    def setData(self, index: QModelIndex, value, role=Qt.ItemDataRole.EditRole) -> bool:
        node = self._node(index)
        if node is None or self._depth(node) != 3:
            return False
        node.value = str(value)
        self.dataChanged.emit(index, index)
        return True

    def headerData(self, section, orientation, role=Qt.ItemDataRole.DisplayRole):
        if (orientation == Qt.Orientation.Horizontal
                and role == Qt.ItemDataRole.DisplayRole and 0 <= section < 2):
            return ("Option", "Value")[section]
        return None

    def hasChildren(self, parent=QModelIndex()) -> bool:
        return not parent.isValid() or parent.column() == 0 and self._depth(
            self._node(parent)) < 3

    def canFetchMore(self, parent=QModelIndex()) -> bool:
        node = self._node(parent)
        return node is not None and self._depth(node) in (1, 2) and not node.loaded

    def fetchMore(self, parent=QModelIndex()) -> None:
        node = self._node(parent)
        if node is None:
            return
        if self._depth(node) == 1:
            node.children = [_Node(name, node) for name in self._components(node.label)]
        else:
            try:
                opts = self.cap.options_for(node.parent.label, node.label)
            except (RuntimeError, OSError):
                opts = []
            node.children = [_Node(opt.name, node, opt) for opt in opts]
        node.loaded = True


class OptionDelegate(QStyledItemDelegate):
    """Typed editors per Option.type; commit is gated by `validate`."""

    committed = Signal(str, str, object, str)
    rejected = Signal(str)

    def __init__(self, validate: Callable[[Option, str], str | None],
                 pixel_formats: list[str] | None = None, parent=None) -> None:
        super().__init__(parent)
        self.validate = validate
        self.pixel_formats = pixel_formats or []

    def createEditor(self, parent, option, index):
        node = index.internalPointer()
        if node is None or node.opt is None:
            return None
        kind_ = editor_kind(node.opt)
        if kind_ == "spin":
            editor = QSpinBox(parent)
            editor.setRange(*self._bounds(node.opt, -2**31, 2**31 - 1))
            return editor
        if kind_ == "dspin":
            editor = QDoubleSpinBox(parent)
            editor.setRange(*self._bounds(node.opt, -1e9, 1e9))
            editor.setDecimals(6)
            return editor
        if kind_ == "check":
            return QCheckBox(parent)
        if kind_ in ("combo", "pixfmt"):
            editor = QComboBox(parent)
            editor.addItem("")
            editor.addItems(self.pixel_formats if kind_ == "pixfmt"
                            else [c.name for c in node.opt.choices])
            return editor
        if kind_ == "multicombo":
            editor = QListWidget(parent)
            editor.setSelectionMode(QAbstractItemView.SelectionMode.MultiSelection)
            for c in node.opt.choices:
                editor.addItem(c.name)
            return editor
        return QLineEdit(parent)

    @staticmethod
    def _bounds(opt: Option, lo_default, hi_default) -> tuple[int, int]:
        def num(x, default):
            return int(float(x)) if x is not None and _INT_RE.match(str(x)) else default
        return num(opt.min, lo_default), num(opt.max, hi_default)

    def setEditorData(self, editor, index):
        node = index.internalPointer()
        if editor is None or node is None or node.opt is None:
            return
        current = node.value
        kind_ = editor_kind(node.opt)
        if kind_ == "spin" and _INT_RE.match(current or ""):
            editor.setValue(int(current))
        elif kind_ == "dspin":
            with contextlib.suppress(ValueError):
                editor.setValue(float(current))
        elif kind_ == "check":
            editor.setChecked(is_truthy(current))
        elif kind_ in ("combo", "pixfmt"):
            editor.setCurrentText(current)
        elif kind_ == "multicombo":
            for token in current.split("+"):
                for row in range(editor.count()):
                    editor.item(row).setSelected(editor.item(row).text() == token)
        elif isinstance(editor, QLineEdit):
            editor.setText(current)

    def setModelData(self, editor, model, index):
        node = index.internalPointer()
        if editor is None or node is None or node.opt is None:
            return
        opt, kind_ = node.opt, editor_kind(node.opt)
        if kind_ == "check":
            value = "true" if editor.isChecked() else ""
        elif kind_ == "multicombo":
            value = "".join("+" + editor.item(i).text()
                            for i in range(editor.count()) if editor.item(i).isSelected())
        elif isinstance(editor, (QSpinBox, QDoubleSpinBox)):
            value = str(editor.value())
        elif isinstance(editor, QComboBox):
            value = editor.currentText()
        else:
            value = editor.text()
        if error := self.validate(opt, value):
            self.rejected.emit(f"{opt.name}: {error}")
            return
        model.setData(index, value)
        kind = node.parent.parent.label
        self.committed.emit(kind, node.parent.label, opt, value)


class ExpertPanel(QWidget):
    """Kind + component selector over the lazy tree, with the typed delegate."""

    optionCommitted = Signal(str, str, object, str)
    statusMessage = Signal(str)

    def __init__(self, cap, parent=None) -> None:
        super().__init__(parent)
        self.cap = cap
        col = QVBoxLayout(self)
        col.setContentsMargins(0, SPACING[1], 0, 0)
        col.setSpacing(SPACING[1])
        row = QHBoxLayout()
        row.setSpacing(SPACING[1])
        self.kind = QComboBox()
        self.kind.addItems(KINDS)
        self.component = QComboBox()
        self.component.setEditable(True)
        self.component.setInsertPolicy(QComboBox.InsertPolicy.NoInsert)
        row.addWidget(self.kind)
        row.addWidget(self.component, stretch=1)
        col.addLayout(row)
        self.tree = QTreeView()
        self.tree.setModel(OptionTreeModel(cap))
        self.delegate = OptionDelegate(self._validate, list(cap.pixel_formats), self.tree)
        self.delegate.committed.connect(self.optionCommitted)
        self.delegate.rejected.connect(self.statusMessage)
        self.tree.setItemDelegateForColumn(1, self.delegate)
        self.tree.header().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        col.addWidget(self.tree)
        self.model = self.tree.model()
        self.kind.currentTextChanged.connect(self._fill_components)
        self.component.currentTextChanged.connect(self._expand_target)
        self._fill_components(self.kind.currentText())

    def _validate(self, opt: Option, value: str) -> str | None:
        error = validate_option(opt, value)
        # validate_option promises a one-line reason, but hostile values echo
        # back raw — keep them off the status bar/tooltip as a single line.
        return " ".join(error.replace("\0", " ").split()) if error else None

    def _fill_components(self, kind: str) -> None:
        names = self.model._components(kind)
        self.component.blockSignals(True)
        self.component.clear()
        self.component.addItems(names)
        self.component.blockSignals(False)

    def _expand_target(self, name: str) -> None:
        if not name:
            return
        model = self.tree.model()
        index = model.set_target(self.kind.currentText(), name)
        if index.isValid():
            self.tree.expand(model.parent(index))
            self.tree.expand(index)
