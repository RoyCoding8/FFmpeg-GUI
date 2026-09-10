"""ffgui application entry: locator, capability probe, shell, controller, persistence."""

from __future__ import annotations

import contextlib
import sys

from PySide6.QtCore import QSettings, QThread, QTimer, Signal
from PySide6.QtWidgets import QApplication

from fftui.ffmpeg.capability_index import CapabilityIndex

from ffgui.controller import Controller, aim_ffmpeg, locate_ffmpeg
from ffgui.doc import QueueDocument
from ffgui.ui.shell import Shell
from ffgui.ui.theme import apply_theme, coerce_mode

_ORG = "ffgui"


class _Probe(QThread):
    done = Signal(object)
    failed = Signal(str)

    def run(self) -> None:
        try:
            self.done.emit(CapabilityIndex.load())
        except Exception as exc:
            self.failed.emit(str(exc))


def _save_state(settings: QSettings, shell: Shell) -> None:
    settings.setValue("geometry", shell.saveGeometry())
    settings.setValue("splitter", shell.splitter.sizes())
    settings.setValue("tab", shell.tabs.currentIndex())


def _restore_state(settings: QSettings, shell: Shell) -> None:
    """Re-apply persisted geometry/splitter/tab; corrupt values fall back."""
    if geometry := settings.value("geometry"):
        with contextlib.suppress(TypeError, ValueError):
            shell.restoreGeometry(geometry)
    try:
        sizes = [int(s) for s in settings.value("splitter")]
    except (TypeError, ValueError):
        sizes = []
    if sizes:
        shell.splitter.setSizes(sizes)
    try:
        tab = int(settings.value("tab", 0))
    except (TypeError, ValueError):
        tab = 0
    shell.tabs.setCurrentIndex(tab)


def _launch_probe(shell: Shell, on_done, on_failed) -> _Probe:
    """Start the capability probe parented to the shell (a parentless QThread
    held only by a local is GC'd mid-run → SIGABRT). Re-entrant rescans share
    the running probe."""
    for child in shell.findChildren(_Probe):
        if child.isRunning():
            return child
    probe = _Probe(shell)
    probe.done.connect(on_done)
    probe.failed.connect(on_failed)
    probe.finished.connect(probe.deleteLater)
    probe.start()
    return probe


def _boot(app: QApplication, shell: Shell, settings: QSettings, cap,
          job_files: list[str] | None = None) -> Controller:
    doc = QueueDocument(cap)
    controller = Controller(shell, doc, cap, settings)
    shell.set_state("workspace")
    for path in job_files or []:
        controller.open_job_file(path)
    return controller


def main(argv: list[str] | None = None) -> int:
    args = [sys.argv[0], *(argv if argv is not None else sys.argv[1:])]
    smoke = "--smoke" in args[1:]
    job_files = [a for a in args[1:] if a.endswith(".ffgui")]
    app = QApplication.instance() or QApplication(args)
    settings = QSettings(_ORG, _ORG)
    apply_theme(app, coerce_mode(settings.value("theme", "system")))
    shell = Shell()
    _restore_state(settings, shell)
    app.aboutToQuit.connect(lambda: _save_state(settings, shell))
    shell.show()
    if smoke:
        _boot(app, shell, settings, CapabilityIndex.stub(), job_files)
        QTimer.singleShot(0, app.quit)
        return app.exec()

    explicit, found = locate_ffmpeg(settings)

    def start_probe() -> None:
        shell.set_state("loading")
        _launch_probe(shell,
                      lambda cap: _boot(app, shell, settings, cap, job_files),
                      _probe_failed)

    def _probe_failed(message: str) -> None:
        shell.set_state("no-ffmpeg")
        shell.no_ffmpeg.status.setText(message)

    def locate(path: str) -> None:
        if not path:
            return
        if shell.no_ffmpeg.remember.isChecked():
            settings.setValue("ffmpeg_path", path)
        aim_ffmpeg(path)
        start_probe()

    def browse() -> None:
        from PySide6.QtWidgets import QFileDialog
        path, _ = QFileDialog.getOpenFileName(shell, "Locate ffmpeg", "",
                                              "ffmpeg executable (ffmpeg.exe ffmpeg)")
        locate(path)

    def rescan() -> None:
        explicit, found = locate_ffmpeg(settings)
        if found:
            aim_ffmpeg(explicit)
            start_probe()
        else:
            _probe_failed("ffmpeg still not found — rescan, browse, or paste a path")

    if found:
        aim_ffmpeg(explicit)
        start_probe()
    else:
        shell.set_state("no-ffmpeg")
        shell.no_ffmpeg.rescan.clicked.connect(rescan)
        shell.no_ffmpeg.browse.clicked.connect(browse)
        shell.no_ffmpeg.accepted.connect(locate)
    code = app.exec()
    return 0 if smoke else code


if __name__ == "__main__":
    raise SystemExit(main())
