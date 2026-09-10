"""Layout regression tests at the widget-properties seam: instantiate real pages
under offscreen qapp and assert token margins/spacing, scroll areas, stretch."""

import contextlib

import pytest
from PySide6.QtWidgets import (
    QComboBox, QDoubleSpinBox, QLabel, QLineEdit, QScrollArea, QSpinBox,
    QVBoxLayout, QWidget,
)

from ffgui.ui import theme
from ffgui.ui.shell import Shell
from ffgui.ui.tabs import build_tabs, wrap_scroll
from ffgui.ui.tokens import SPACING
from fftui.ffmpeg.capability_index import CapabilityIndex

_CONTROLS = (QLineEdit, QComboBox, QSpinBox, QDoubleSpinBox)
_BOX_CHROME = 2 * SPACING[0] + 2  # vertical QSS padding + 1px borders


def _pages():
    cap = CapabilityIndex(version_hash="x", _runner=lambda a: "")
    return build_tabs(cap)


def test_tab_pages_have_token_margins_and_spacing(qapp):
    for name, page in _pages().items():
        lay = page.layout()
        assert isinstance(lay, QVBoxLayout), name
        m = lay.contentsMargins()
        assert (m.left(), m.top(), m.right(), m.bottom()) == (SPACING[2],) * 4, name
        assert lay.spacing() == SPACING[1], name


def test_tab_pages_wrap_in_resizable_scroll_areas(qapp):
    for name, page in _pages().items():
        area = wrap_scroll(page)
        assert isinstance(area, QScrollArea), name
        assert area.widget() is page, name
        assert area.widgetResizable(), name


def test_shell_minimum_size_and_token_margins(qapp):
    s = Shell()
    assert (s.minimumWidth(), s.minimumHeight()) == (640, 480)
    center = s.splitter.widget(1)
    m = center.layout().contentsMargins()
    assert (m.left(), m.top(), m.right(), m.bottom()) == (SPACING[2],) * 3 + (0,)
    assert center.layout().spacing() == SPACING[1]


def test_expert_panel_stretches_and_uses_tokens(qapp):
    from PySide6.QtWidgets import QHeaderView

    from ffgui.ui.expert import ExpertPanel
    cap = CapabilityIndex(version_hash="x", _runner=lambda a: "")
    p = ExpertPanel(cap)
    m = p.layout().contentsMargins()
    assert (m.left(), m.top(), m.right(), m.bottom()) == (0, SPACING[1], 0, 0)
    assert p.tree.header().sectionResizeMode(0) == QHeaderView.ResizeMode.Stretch


@contextlib.contextmanager
def _polished(qapp):
    """Real stylesheet applied (restored after); widgets shown so QSS polishes."""
    theme.apply_theme(qapp, "light")
    try:
        yield
    finally:
        theme.apply_theme(qapp, "system")


def test_controls_minimum_fits_font(qapp):
    """No textbox/combo/spinbox may clip its own font at minimum size."""
    with _polished(qapp):
        cap = CapabilityIndex(version_hash="x", _runner=lambda a: "")
        pages = build_tabs(cap)
        for page in pages.values():
            page.show()
        qapp.processEvents()
        for name, page in pages.items():
            for kind in _CONTROLS:
                for w in page.findChildren(kind):
                    if isinstance(w, QLineEdit) and isinstance(w.parent(), QComboBox):
                        continue  # editable combo's internal editor
                    need = w.fontMetrics().height() + _BOX_CHROME
                    assert w.minimumSize().height() >= need, (
                        f"{name}.{type(w).__name__}: "
                        f"min {w.minimumSize().height()}px < font+chrome {need}px")


def test_expert_editors_minimum_fits_font(qapp):
    """Every delegate editor kind clears the same no-clipping bar."""
    from fftui.model import Option, OptionChoice

    from ffgui.ui.expert import OptionDelegate, _Node

    opts = [
        Option(name="a", type="string"),
        Option(name="a", type="int"),
        Option(name="a", type="double"),
        Option(name="a", type="boolean"),
        Option(name="a", type="string", choices=[OptionChoice("x", 0)]),
        Option(name="a", type="flags", choices=[OptionChoice("x", 1)]),
        Option(name="a", type="pix_fmt"),
    ]
    with _polished(qapp):
        host = QWidget()
        host.show()
        delegate = OptionDelegate(lambda o, v: None, [], host)
        editors = []
        for opt in opts:
            node = _Node(opt.name, _Node("c", _Node("k")), opt)
            index = type("I", (), {"internalPointer": lambda self: node})()
            editor = delegate.createEditor(host, None, index)
            assert editor is not None, opt.type
            editor.show()  # QSS polish (and its minimum size) lands on show
            editors.append((opt.type, editor))
        qapp.processEvents()
        for type_, editor in editors:
            if isinstance(editor, _CONTROLS):
                need = editor.fontMetrics().height() + _BOX_CHROME
                assert editor.minimumSize().height() >= need, type_


def _min_tree(widget, depth=0):
    """Minimum-width tree for diagnosing which child blows a page budget.

    Uses minimumSizeHint: plain minimumSize is the *explicit* minimum
    (unset → 0) on children without layouts, which hides the culprit.
    """
    hint = widget.minimumSizeHint()
    lines = [f"{'  ' * depth}{type(widget).__name__} "
             f"hint={hint.width()}x{hint.height()}"]
    layout = widget.layout()
    if layout is not None and depth < 4:
        for i in range(layout.count()):
            child = layout.itemAt(i).widget()
            if child is not None:
                lines.extend(_min_tree(child, depth + 1))
    return lines


def test_pages_fit_shell_center(qapp):
    """No page may force horizontal squeeze at the minimum window size."""
    # The assertion measures with ambient widget metrics. A broken headless
    # platform (every advance inflated — seen on Windows offscreen) is a
    # broken ruler, not a layout bug: calibrate with a real QLabel — the
    # same instrument the layout uses — and skip when one char averages
    # more than 0.85 of the line height. No proportional UI font does that.
    ruler = QLabel("File name")
    ruler.ensurePolished()
    ruler.show()
    avg = ruler.sizeHint().width() / len("File name")
    if avg > 0.85 * ruler.sizeHint().height():
        pytest.skip(f"widget advances are absurd ({avg:.1f}px/char): "
                    f"layout cannot be measured here")
    cap = CapabilityIndex(version_hash="x", _runner=lambda a: "")
    shell = Shell()  # held: an unreferenced Shell dies with its splitter
    center_min = shell.splitter.widget(1).minimumWidth()
    pages = build_tabs(cap)
    for page in pages.values():
        page.show()  # layout minima are only computed once shown
        # show()+events alone does not activate layouts on every
        # platform (Windows offscreen reports preferred sizes instead
        # of minima); activate explicitly so minimumSize() is real.
        page.ensurePolished()
        page.layout().activate()
    qapp.processEvents()
    bad = {name: page for name, page in pages.items()
           if page.minimumSize().width() > center_min}
    assert not bad, "\n".join(
        f"{name}: min-width {page.minimumSize().width()}px "
        f"> center {center_min}px\n" + "\n".join(_min_tree(page))
        for name, page in bad.items())
