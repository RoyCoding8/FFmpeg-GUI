"""Shell-chrome regressions: empty-state click, File-menu reuse, error headline."""

import pytest
from PySide6.QtCore import QSettings, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QMessageBox, QPushButton
from fftui.ffmpeg.capability_index import CapabilityIndex

import ffgui.app as app_mod
from ffgui.controller import Controller
from ffgui.doc import QueueDocument
from ffgui.ui.shell import EmptyState, NoFfmpegPage, Shell, error_modal


def test_empty_state_click_requests_add_files(qapp):
    """The empty card promises 'click to browse' — a click must emit the signal
    the controller wires to the add-files dialog."""
    state = EmptyState()
    state.show()
    hits = []
    state.add_files_requested.connect(lambda: hits.append(1))
    QTest.mouseClick(state, Qt.MouseButton.LeftButton)
    assert hits == [1]


def test_add_actions_rewires_into_one_file_menu(qapp):
    """Re-wiring actions (second controller, re-wire) must not pile up File
    menus or duplicate the action pair inside it."""
    shell = Shell()
    shell.add_actions(lambda: None, lambda: None)
    shell.add_actions(lambda: None, lambda: None)
    file_menus = [a.menu() for a in shell.menuBar().actions()
                  if a.menu() is not None and a.menu().title() == "&File"]
    assert len(file_menus) == 1
    assert [a.text() for a in file_menus[0].actions()] == [
        "&Add files…", "Add fol&der…"]


def _capture_modal(monkeypatch):
    captured = {}
    monkeypatch.setattr(
        QMessageBox, "exec",
        lambda self: captured.update(text=self.text(),
                                     detail=self.detailedText()) or 0)
    return captured


def test_error_modal_skips_blank_headline_lines(qapp, monkeypatch):
    captured = _capture_modal(monkeypatch)
    error_modal(Shell(), "Title here", "\n\nboom went wrong\nline2")
    assert captured["text"] == "boom went wrong"
    assert captured["detail"] == "\n\nboom went wrong\nline2"


def test_empty_state_ignores_non_left_click(qapp):
    """Right/middle press must not open the file dialog."""
    state = EmptyState()
    state.show()
    hits = []
    state.add_files_requested.connect(lambda: hits.append(1))
    QTest.mouseClick(state, Qt.MouseButton.RightButton)
    QTest.mouseClick(state, Qt.MouseButton.MiddleButton)
    assert hits == []


def test_add_actions_does_not_duplicate_actions(qapp):
    """Re-wiring must leave exactly one pair of actions in the one File menu."""
    shell = Shell()
    shell.add_actions(lambda: None, lambda: None)
    shell.add_actions(lambda: None, lambda: None)
    file_menus = [a.menu() for a in shell.menuBar().actions()
                  if a.menu() is not None and a.menu().title() == "&File"]
    assert len(file_menus) == 1
    assert [a.text() for a in file_menus[0].actions()] == [
        "&Add files…", "Add fol&der…"]




def test_add_actions_rewire_connects_latest(qapp):
    """After re-wire, the returned actions fire the newest callbacks."""
    shell = Shell()
    old, _ = shell.add_actions(lambda: None, lambda: None)
    fired = []
    new_files, new_folder = shell.add_actions(
        lambda: fired.append("files"), lambda: fired.append("folder"))
    assert new_files is not old
    new_files.trigger()
    new_folder.trigger()
    assert fired == ["files", "folder"]


def test_error_modal_strips_headline_whitespace(qapp, monkeypatch):
    captured = _capture_modal(monkeypatch)
    error_modal(Shell(), "Title here", "   padded headline   \nline2")
    assert captured["text"] == "padded headline"


def test_error_modal_empty_details_falls_back_to_title(qapp, monkeypatch):
    captured = _capture_modal(monkeypatch)
    error_modal(Shell(), "Title here", "")
    assert captured["text"] == "Title here"
    captured.clear()
    error_modal(Shell(), "Title here", "   \n  ")
    assert captured["text"] == "Title here"


def test_error_modal_does_not_leak_message_boxes(qapp, monkeypatch):
    """The modal is parented for modality — it must not outlive the call as an
    invisible child on every error."""
    monkeypatch.setattr(QMessageBox, "exec", lambda self: 0)
    parent = Shell()
    before = len(parent.children())
    error_modal(parent, "Title here", "boom")
    error_modal(parent, "Title here", "boom")
    qapp.processEvents()
    assert len(parent.children()) == before


def test_no_ffmpeg_manual_blank_does_not_emit_accepted(qapp):
    """editingFinished also fires on focus loss with untouched text — a blank
    entry must not propose an empty ffmpeg path to the locator."""
    page = NoFfmpegPage()
    got = []
    page.accepted.connect(got.append)
    page.manual.setText("")
    page.manual.editingFinished.emit()
    page.manual.setText("   ")
    page.manual.editingFinished.emit()
    assert got == []


def test_no_ffmpeg_manual_emits_stripped_path(qapp):
    page = NoFfmpegPage()
    got = []
    page.accepted.connect(got.append)
    page.manual.setText("  /usr/bin/ffmpeg  ")
    page.manual.editingFinished.emit()
    assert got == ["/usr/bin/ffmpeg"]


def test_toggle_sidebar_collapses_and_restores(qapp):
    """The sidebar toggle is the only collapse path: first call hides the
    sidebar, the second restores its token width."""
    from ffgui.ui.tokens import SIDEBAR_WIDTH
    shell = Shell()
    shell.splitter.setSizes([SIDEBAR_WIDTH, 900])
    open_sizes = shell.splitter.sizes()
    assert open_sizes[0] > 0
    shell.toggle_sidebar()
    sizes = shell.splitter.sizes()
    assert sizes[0] == 0 and sum(sizes) == sum(open_sizes)
    shell.toggle_sidebar()
    sizes = shell.splitter.sizes()
    assert sizes[0] > 0 and sum(sizes) == sum(open_sizes)


def test_widget_cleanup_destroys_leaked_top_levels(qapp):
    """Leaked windows must not pile up across tests: tens of thousands of
    live widgets slow the suite and make app-wide repolish hang."""
    from PySide6.QtWidgets import QApplication, QWidget

    from conftest import close_top_level_widgets

    leaked = QWidget()
    leaked.show()
    assert leaked in QApplication.topLevelWidgets()
    close_top_level_widgets()
    assert QApplication.topLevelWidgets() == []


def test_saved_tab_survives_controller_boot(qapp, tmp_path, monkeypatch):
    monkeypatch.setenv("FFGUI_CACHE_DIR", str(tmp_path / "cache"))
    settings = QSettings(str(tmp_path / "settings.ini"), QSettings.Format.IniFormat)
    settings.setValue("tab", 6)
    shell = Shell()
    app_mod._restore_state(settings, shell)
    assert shell.tabs.currentIndex() == 6
    cap = CapabilityIndex.stub()
    Controller(shell, QueueDocument(cap), cap, settings)
    assert shell.tabs.currentIndex() == 6
    assert shell.tabs.tabText(shell.tabs.currentIndex()) == "Metadata"


@pytest.mark.parametrize("outcome", ["failed", "cancelled"])
def test_initial_probe_can_recover_with_rescan(qapp, tmp_path, monkeypatch, outcome):
    settings = QSettings(str(tmp_path / "settings.ini"), QSettings.Format.IniFormat)
    shell = Shell()
    attempts = []

    def launch(shell, done, failed):
        attempts.append((done, failed))

    monkeypatch.setattr(app_mod, "locate_ffmpeg", lambda settings: (None, True))
    monkeypatch.setattr(app_mod, "_launch_probe", launch)
    app_mod._wire_probe(qapp, shell, settings, [])
    shell.show()
    qapp.processEvents()
    assert shell.state_stack.currentWidget() is shell.loading
    if outcome == "failed":
        attempts[0][1]("probe failed")
    else:
        cancel = next(b for b in shell.loading.findChildren(QPushButton)
                      if b.text() == "Cancel")
        QTest.mouseClick(cancel, Qt.MouseButton.LeftButton)
    qapp.processEvents()
    assert shell.no_ffmpeg.rescan.isVisible()
    QTest.mouseClick(shell.no_ffmpeg.rescan, Qt.MouseButton.LeftButton)
    qapp.processEvents()
    assert shell.state_stack.currentWidget() is shell.loading
    assert len(attempts) == 2
