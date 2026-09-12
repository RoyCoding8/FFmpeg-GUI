"""Gate: the overwrite checkbox promises -y, so noconfirm must carry the opposite."""
from fftui.model import Job, Output

from ffgui.ui.tabs import ContainerTab


def _tab(qapp):
    tab = ContainerTab()
    seen = []
    tab.edit.connect(lambda kind, key, value: seen.append((kind, key, value)))
    return tab, seen


def test_checked_means_overwrite_y(qapp):
    tab, seen = _tab(qapp)
    tab.overwrite.setChecked(True)
    assert ("flag", "noconfirm", "False") in seen


def test_unchecked_means_ask_n(qapp):
    tab, seen = _tab(qapp)
    tab.overwrite.setChecked(True)
    seen.clear()
    tab.overwrite.setChecked(False)
    assert ("flag", "noconfirm", "True") in seen


def test_load_restores_overwrite_from_noconfirm(qapp):
    tab, _ = _tab(qapp)
    tab.load(Output(path="o.mp4"), Job())
    assert tab.overwrite.isChecked()
