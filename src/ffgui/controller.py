"""The controller: binds QueueDocument ↔ Shell, owns the Basic-tier actions,
the ffmpeg locator chain, and the export/run flows."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

from PySide6.QtCore import (QItemSelection, QItemSelectionModel, QObject,
                           QSettings, Qt, Signal)
from PySide6.QtGui import QAction, QKeySequence
from PySide6.QtWidgets import (
    QApplication, QCheckBox, QDialog, QDialogButtonBox, QFileDialog,
    QInputDialog, QLineEdit, QMenu, QTableWidgetItem, QVBoxLayout,
)

from fftui.ffmpeg.capability_index import CapabilityIndex
from fftui.model import Job
from fftui.util.command_builder import build, build_two_pass

from ffgui.doc import QueueDocument, QueueItem
from ffgui.export import script_for_queue
from ffgui.store import delete_preset, fresh_meta, is_truthy, load_presets, save_preset
from ffgui.ui import icons
from ffgui.ui.shell import Shell, mark_invalid
from ffgui.ui.tabs import build_tabs, wrap_scroll

MEDIA_SUFFIXES = {".mp4", ".mkv", ".webm", ".mov", ".avi", ".ts", ".m4v", ".mpg",
                  ".mp3", ".m4a", ".wav", ".flac", ".ogg", ".opus", ".aac", ".wma",
                  ".srt", ".ass", ".vtt", ".jpg", ".png"}
TARGET_EXT = {"bat": ".bat", "sh": ".sh"}
_LINUX_TERMINALS = (("x-terminal-emulator", "-e"), ("gnome-terminal", "--"),
                    ("konsole", "-e"), ("xfce4-terminal", "-x"),
                    ("mate-terminal", "--"), ("xterm", "-e"))


def terminal_argv(path: str) -> list[str] | None:
    """Argv opening *path* in a fresh terminal. *path* never enters a parsed
    string: Linux execs it after ``--``; macOS passes it via ``quoted form of``."""
    if sys.platform == "darwin":
        return ["osascript", "-e",
                'on run argv\ntell app "Terminal" to do script '
                '"sh -- " & quoted form of (item 1 of argv)\nend run',
                path]
    for exe, flag in _LINUX_TERMINALS:
        if shutil.which(exe):
            return [exe, flag, "sh", "--", path]
    return None


def launch_script(path: str) -> None:
    """Run *path* detached, exactly as if the user double-clicked it. The
    format follows the file suffix (run_terminal lets the suffix override the
    OS default), never the platform: a .bat fed to ``sh --`` parses cmd.exe
    syntax line by line, so refuse it off Windows instead."""
    if Path(path).suffix.lower() == ".bat":
        if sys.platform != "win32":
            raise RuntimeError(
                f"cannot launch {path} here — .bat scripts need Windows "
                "(export a .sh script to run on this machine)")
        os.startfile(path)
        return
    if sys.platform == "win32":
        os.startfile(path)
        return
    argv = terminal_argv(path)
    if argv is None:
        raise RuntimeError("no terminal emulator found — run the saved script manually")
    subprocess.Popen(argv, start_new_session=True, stdin=subprocess.DEVNULL,
                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


class _DropFilter(QObject):
    """External file/folder drops onto the queue or the empty-state card."""

    pathsDropped = Signal(list)

    def eventFilter(self, obj, event) -> bool:
        from PySide6.QtCore import QEvent
        if event.type() in (QEvent.Type.DragEnter, QEvent.Type.DragMove) \
                and event.mimeData().hasUrls():
            event.acceptProposedAction()
            return True
        if event.type() == QEvent.Type.Drop and event.mimeData().hasUrls():
            paths = [url.toLocalFile() for url in event.mimeData().urls()
                     if url.isLocalFile()]
            if paths:
                self.pathsDropped.emit(paths)
            event.acceptProposedAction()
            return True
        return False


def locate_ffmpeg(settings: QSettings) -> tuple[str | None, bool]:
    """Locator chain FFMPEG_PATH → last-good setting → PATH → sibling ffmpeg.exe;
    returns (explicit_path, found)."""
    for candidate in (os.environ.get("FFMPEG_PATH", "").strip(),
                      str(settings.value("ffmpeg_path", "") or "").strip()):
        if candidate:
            resolved = Path(os.path.expanduser(os.path.expandvars(candidate)))
            if resolved.is_file():
                return str(resolved), True
    if shutil.which("ffmpeg"):
        return None, True
    sibling_dir = Path(sys.executable).parent
    for name in ("ffmpeg.exe", "ffmpeg"):
        sibling = sibling_dir / name
        if sibling.is_file():
            return str(sibling), True
    return None, False


def aim_ffmpeg(path: str | None) -> None:
    """Route fftui's runtime at a located binary that is not on PATH."""
    if path:
        os.environ["FFTUI_FFMPEG_DIR"] = str(Path(path).parent)


class Controller(QObject):
    status = Signal(str)

    def __init__(self, shell: Shell, doc: QueueDocument, cap: CapabilityIndex,
                 settings: QSettings) -> None:
        super().__init__(shell)
        self.shell = shell
        self.doc = doc
        self.cap = cap
        self.settings = settings
        self.tabs = build_tabs(cap)
        self._last_rows: list[int] | None = None
        for page in self.tabs.values():
            expert = getattr(page, "expert", None)
            if expert is not None:
                expert.optionCommitted.connect(self._on_expert_committed)
                expert.statusMessage.connect(
                    lambda msg: shell.statusBar().showMessage(msg, 8000))
        while shell.tabs.count():
            shell.tabs.widget(0).deleteLater()
            shell.tabs.removeTab(0)
        for name, page in self.tabs.items():
            page.edit.connect(self._on_tab_edit)
            shell.tabs.addTab(wrap_scroll(page), name)
        self._wire()


    def _wire(self) -> None:
        s = self.shell
        s.add_actions(self.add_files_dialog, self.add_folder_dialog)
        s.export_btn.clicked.connect(self.export_dialog)
        s.run_btn.clicked.connect(self.run_terminal)
        s.empty.add_files_requested.connect(self.add_files_dialog)
        s.queue.itemSelectionChanged.connect(self._on_selection)
        s.queue.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        s.queue.customContextMenuRequested.connect(self._context_menu)
        dropper = _DropFilter(self)
        dropper.pathsDropped.connect(self.add_paths)
        s.queue.setAcceptDrops(True)
        s.queue.viewport().installEventFilter(dropper)
        s.empty.installEventFilter(dropper)
        s.preset_list.itemDoubleClicked.connect(
            lambda item: self.apply_preset(item.text()))
        s.preset_save.clicked.connect(self.save_preset_dialog)
        s.preset_delete.clicked.connect(self.delete_preset)
        remove = QAction("Remove selected", s)
        remove.setShortcuts([QKeySequence(Qt.Key.Key_Delete),
                             QKeySequence(Qt.Key.Key_Backspace)])
        remove.triggered.connect(self.remove_selected)
        s.addAction(remove)
        self.status.connect(lambda text: s.statusBar().showMessage(text, 8000))
        self.refresh_presets()
        self.refresh()


    def refresh(self) -> None:
        table = self.shell.queue
        table.setRowCount(len(self.doc))
        for row, item in enumerate(self.doc.items):
            table.setItem(row, 0, self._status_cell(item))
            inp = item.job.inputs[0] if item.job.inputs else None
            video = next((s for s in (inp.streams if inp else [])
                          if s.codec_type == "video"), None)
            cells = (
                item.meta["name"],
                "",
                f"{video.width}×{video.height}" if video and video.width else "",
                self._codec_summary(item),
                Path(item.job.outputs[0].path).name if item.job.outputs else "",
            )
            for col, text in enumerate(cells, start=1):
                cell = QTableWidgetItem(text)
                if col == 5 and item.job.outputs:
                    cell.setToolTip(item.job.outputs[0].path)
                table.setItem(row, col, cell)
        self.shell.show_empty(len(self.doc) == 0)
        self._on_selection()

    def _status_cell(self, item) -> QTableWidgetItem:
        if item.error:
            cell = QTableWidgetItem(icons.icon("triangle-alert"), "")
            cell.setToolTip(item.error)
        else:
            cell = QTableWidgetItem(icons.icon("check"), "")
            cell.setToolTip(item.meta["notes"] or "ready")
        return cell

    def _codec_summary(self, item) -> str:
        out = item.job.outputs[0] if item.job.outputs else None
        if out is None:
            return ""
        parts = [out.video_codec or "", out.audio_codec or "", out.subtitle_codec or ""]
        return " · ".join(p for p in parts if p)

    def selected_rows(self) -> list[int]:
        return sorted({i.row() for i in self.shell.queue.selectedIndexes()})

    def _select_rows(self, rows) -> None:
        """Select exactly these rows in one selection: selectRow replaces the
        current selection, so looping it keeps only the last row."""
        model = self.shell.queue.model()
        selection = QItemSelection()
        for row in rows:
            index = model.index(row, 0)
            selection.select(index, index)
        self.shell.queue.selectionModel().select(
            selection, QItemSelectionModel.ClearAndSelect | QItemSelectionModel.Rows)


    def _on_selection(self) -> None:
        rows = self.selected_rows()
        if rows != self._last_rows:
            self._last_rows = rows
            meta = self.tabs["Metadata"]
            meta.stream_spec.setText("")
            meta.stream_lang.setText("")
        if not rows:
            self.shell.preview.setPlainText("")
            return
        item = self.doc.items[rows[0]]
        if item.job.outputs:
            out = item.job.outputs[0]
            for page in self.tabs.values():
                page.populate(out, item.job)
        self._update_preview()

    def _update_preview(self) -> None:
        rows = self.selected_rows()
        if not rows:
            self.shell.preview.setPlainText("")
            return
        chunks = []
        for row in rows:
            item = self.doc.items[row]
            try:
                if item.job.two_pass:
                    argvs = build_two_pass(item.job)
                    chunks.append(" &&\n".join(" ".join(a) for a in argvs))
                else:
                    chunks.append(" ".join(build(item.job)))
            except Exception as exc:
                chunks.append(f"# {type(exc).__name__}: {exc}")
        self.shell.preview.setPlainText("\n\n".join(chunks))


    def _on_tab_edit(self, kind: str, key: str, value: str) -> None:
        rows = self.selected_rows()
        if not rows:
            return
        basic = {"codec": self._apply_codec, "option": self._apply_option,
                 "field": self._apply_field, "flag": self._apply_flag}
        error = None
        for row in rows:
            error = (basic[kind](row, key, value)
                     if isinstance(kind, str) and kind in basic
                     else self._apply_advanced(kind, key, value, row)) or error
        if error:
            widget = self.shell.tabs.currentWidget().focusWidget()
            if isinstance(widget, QLineEdit):
                mark_invalid(widget, error)
            self.status.emit(error)
        self.refresh()

    def _on_expert_committed(self, kind: str, component: str, opt, value: str) -> None:
        """Expert commits refresh the queue/preview like tab edits; errors go loud."""
        error = self._apply_expert(kind, component, opt, value)
        if error:
            self.status.emit(error)
        self.refresh()

    def _apply_expert(self, kind: str, component: str, opt, value: str) -> str | None:
        """Route a committed Expert AVOption to its Job slot (never bare filter argv)."""
        rows = self.selected_rows()
        if not rows:
            return "select a queue row first"
        row = rows[0]
        if kind == "filter":
            stream = ("audio_filters" if self.cap.filter_kind(component) == "audio"
                      else "video_filters")
            chain = list(getattr(self.doc.items[row].job, stream).filters)
            chain.append(f"{component}={opt.name}={value}" if value else f"{component}={opt.name}")
            return self.doc.set_filters(row, stream, chain)
        if kind == "encoder":
            entry = self.cap.entry("encoder", component)
            scope = "audio" if "A" in (entry.flags if entry else "") else "video"
            return self.doc.set_option(row, scope, opt.name, value)
        if kind == "muxer":
            return self.doc.set_option(row, "mux", opt.name, value)
        if kind == "bsf":
            return self.doc.set_bsf(row, value or component or "")
        return self.doc.set_input_arg(row, f"-{opt.name}", value)

    def _apply_advanced(self, kind: str, key: str, value: str, row: int) -> str | None:
        if not isinstance(row, int) or not 0 <= row < len(self.doc):
            return f"no such row {row}"
        value = value if isinstance(value, str) else ""
        job = self.doc.items[row].job
        out = job.outputs[0] if job.outputs else None
        if out is None and kind not in ("hwdecode", "input"):
            return "row has no output yet"
        if kind == "filters":
            return self.doc.set_filters(row, key, value.split("\n") if value else [])
        if kind == "chapters":
            return self.doc.set_chapters(row, value)
        if kind in ("burn", "volume", "loudnorm"):
            stream = "video_filters" if kind == "burn" else "audio_filters"
            head = "subtitles" if kind == "burn" else kind
            keep = [f for f in getattr(job, stream).filters if not f.startswith(head)]


            on = is_truthy(value) if kind == "loudnorm" else value
            if on:
                keep.append(f"{head}={value}" if kind != "loudnorm"
                            else "loudnorm=I=-16:TP=-1.5:LRA=11")
            return self.doc.set_filters(row, stream, keep)
        if kind == "faststart":
            return self.doc.set_faststart(row, value)
        elif kind == "hwdecode":
            return self.doc.set_hw_decode(row, value)
        elif kind == "hwdevice":
            return self.doc.set_option(row, "global", "filter_hw_device", value)
        elif kind == "segment":
            return self.doc.set_segment_enabled(row, value)
        elif kind == "segment_time":
            return self.doc.set_output_field(row, "segment_time", value)
        elif kind == "bsf":
            return self.doc.set_bsf(row, value)
        elif kind == "tee":
            return self.doc.set_tee(row, value)
        elif kind == "input":
            return self.doc.set_input_arg(row, key, value)
        elif kind == "streamtag":
            meta = self.tabs["Metadata"]
            spec, lang = meta.stream_spec.text().strip(), meta.stream_lang.text().strip()
            if not spec or not lang:
                return "enter a stream spec (e.g. v:0) and a language tag"
            return self.doc.set_stream_meta(row, spec, "language", lang)
        return f"unknown edit {kind!r}"

    def _apply_codec(self, row, stream, value):


        return self.doc.set_codec(row, stream, value)

    def _apply_option(self, row, key, value):
        scope, _, real = (key if isinstance(key, str) else "").partition(":")
        return self.doc.set_option(row, scope, real, value)

    def _apply_field(self, row, key, value):
        if key in ("title", "comment"):
            return self.doc.set_metadata(row, key, value)


        return self.doc.set_output_field(row, key, value)

    def _apply_flag(self, row, key, value):
        if not isinstance(row, int) or not 0 <= row < len(self.doc):
            return f"no such row {row}"
        return self.doc.set_flag(row, key, value if isinstance(value, str) else "")


    def add_paths(self, paths: list[str] | None, recursive: bool = False) -> None:
        if QApplication.activeModalWidget():
            self.status.emit("close the dialog first — drop refused")
            return
        if isinstance(paths, (str, os.PathLike)):
            paths = [paths]
        if not paths:
            return
        try:
            paths = list(paths)
        except TypeError:
            return
        files: list[str] = []
        for raw in paths:
            if not isinstance(raw, (str, os.PathLike)):
                continue
            p = Path(raw)
            if p.is_dir():
                source = p.rglob("*") if recursive else p.iterdir()
                found = sorted(str(f) for f in source if f.suffix.lower() in MEDIA_SUFFIXES)
                files += found
                if recursive:
                    self.status.emit(f"{p.name}: {len(found)} media files")
            else:
                files.append(str(p))
        if files:
            self.doc.add_files(files)
            self.refresh()

    def add_files_dialog(self) -> None:
        paths, _ = QFileDialog.getOpenFileNames(self.shell, "Add media files")
        self.add_paths(paths)

    def add_folder_dialog(self) -> None:
        folder = QFileDialog.getExistingDirectory(self.shell, "Add folder")
        if folder:
            self.add_paths([folder], recursive=True)

    def remove_selected(self) -> None:
        rows = self.selected_rows()
        if rows:
            self.doc.remove_rows(rows)
            self.refresh()

    def duplicate_selected(self) -> None:
        rows = sorted(self.selected_rows())
        if not rows:
            return
        for row in reversed(rows):
            self.doc.duplicate_row(row)
        self.refresh()
        self._select_rows(row + 1 + i for i, row in enumerate(rows))

    def move_selected(self, delta: int) -> None:
        rows = self.selected_rows()
        if not rows:
            return
        new_start = max(0, min(rows[0] + delta, len(self.doc) - len(rows)))
        self.doc.move_rows(rows, new_start if delta < 0 else new_start + len(rows))
        self.refresh()
        self._select_rows(range(new_start, new_start + len(rows)))

    def concat_parts_dialog(self) -> None:
        rows = self.selected_rows()
        if len(rows) != 1:
            self.status.emit("select exactly one row to join parts onto")
            return
        paths, _ = QFileDialog.getOpenFileNames(self.shell, "Join parts (lossless concat)")
        if not paths:
            return
        error = self.doc.join_parts(rows[0], paths)
        if error:
            self.status.emit(error)
            return
        self.refresh()
        self._update_preview()


    def _context_menu(self, pos) -> None:
        menu = QMenu(self.shell)
        for label, handler in (
            ("&Duplicate", self.duplicate_selected),
            ("&Remove", self.remove_selected),
            ("Move &up", lambda: self.move_selected(-1)),
            ("Move &down", lambda: self.move_selected(1)),
            ("&Join parts…", self.concat_parts_dialog),
            ("Stream &info…", self.stream_info_dialog),
        ):
            menu.addAction(label, handler)
        menu.exec(self.shell.queue.viewport().mapToGlobal(pos))

    def stream_info_dialog(self) -> None:
        rows = self.selected_rows()
        if len(rows) != 1:
            self.status.emit("select exactly one row for stream info")
            return
        inputs = self.doc.items[rows[0]].job.inputs
        if not inputs:
            self.status.emit("row has no input yet")
            return
        inp = inputs[0]
        dialog = QDialog(self.shell)
        dialog.setWindowTitle(f"Streams — {Path(inp.path).name}")
        col = QVBoxLayout(dialog)
        for stream in inp.streams:
            box = QCheckBox(f"{stream.spec}  {stream.codec_name or '?'}  "
                            f"{stream.width or stream.sample_rate or ''}"
                            f"{'×' + str(stream.height) if stream.height else ''}")
            box.setChecked(stream.mapped)
            box.toggled.connect(lambda on, s=stream: setattr(s, "mapped", on))
            col.addWidget(box)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.rejected.connect(dialog.reject)
        buttons.accepted.connect(dialog.accept)
        col.addWidget(buttons)
        dialog.finished.connect(self._update_preview)
        dialog.exec()


    def refresh_presets(self) -> None:
        self.shell.preset_list.clear()
        for name in sorted(load_presets()):
            self.shell.preset_list.addItem(name)

    def save_preset_dialog(self) -> None:
        rows = self.selected_rows()
        if not rows:
            self.status.emit("select a row to save as a preset")
            return
        name, ok = QInputDialog.getText(self.shell, "Save preset", "Preset name")
        if ok and name:
            item = self.doc.items[rows[0]]
            save_preset(name, item.job, item.meta)
            self.refresh_presets()

    def apply_preset(self, name: str) -> None:
        rows = self.selected_rows()
        if not rows:
            self.status.emit("select a row to apply the preset to")
            return
        presets = load_presets()
        if name not in presets:
            return
        preset_job, _ = presets[name]
        for row in rows:
            item = self.doc.items[row]
            job = Job.from_dict(preset_job.to_dict())
            if item.job.inputs and job.inputs:
                job.inputs[0] = item.job.inputs[0]
            item.job = job
            self.doc.touch(row)
        self.refresh()

    def delete_preset(self) -> None:
        item = self.shell.preset_list.currentItem()
        if item:
            delete_preset(item.text())
            self.refresh_presets()


    def open_job_file(self, path: str) -> None:
        """Open a double-clicked .ffgui exchange file into the queue."""
        try:
            data = json.loads(Path(path).read_text(encoding="utf-8"))
            job = Job.from_dict(data["job"])
        except (OSError, ValueError, KeyError, TypeError, RecursionError) as exc:
            self.status.emit(f"invalid job file: {exc}")
            return


        meta_raw = data.get("meta", {})
        meta_name = meta_raw.get("name") if isinstance(meta_raw, dict) else None
        self.doc.items.append(QueueItem(job, fresh_meta(meta_name or Path(path).stem)))
        self.refresh()
        self.status.emit(f"opened {path}")


    def _enabled_jobs(self) -> list:
        return [it.job for it in self.doc.items
                if it.meta["enabled"] and it.job.inputs and it.job.outputs]

    def _target(self) -> str:
        return "bat" if self.shell.format.currentIndex() == 0 else "sh"

    def export_dialog(self, one_file: bool = True) -> None:
        jobs = self._enabled_jobs()
        if not jobs:
            self.status.emit("nothing to export — add media first")
            return
        target = self._target()
        stem = Path(jobs[0].outputs[0].path).stem if len(jobs) == 1 else "convert"
        path, _ = QFileDialog.getSaveFileName(self.shell, "Export script",
                                              f"{stem}{TARGET_EXT[target]}")
        if not path:
            return
        try:
            written = self.export_to(path, target, one_file)
        except Exception as exc:
            self.status.emit(f"export failed — {exc}")
            return
        self.status.emit(f"Exported {written}")

    def export_to(self, path: str, target: str, one_file: bool = True) -> str:
        if not isinstance(path, (str, os.PathLike)):
            raise ValueError(f"export path must be a string, got {type(path).__name__}")
        jobs = self._enabled_jobs()
        hash_ = self.cap.version_hash
        if one_file:
            text = script_for_queue(jobs, target, hash_, one_file=True)
            Path(path).write_bytes(text.encode("utf-8"))
            return path
        scripts = script_for_queue(jobs, target, hash_, one_file=False)
        written = ""
        used: set[str] = set()
        for job, text in zip(jobs, scripts):
            out = Path(path)
            if len(jobs) > 1:
                root = Path(path)
                out = root.with_suffix("").with_name(
                    f"{root.stem}_{Path(job.outputs[0].path).stem}{TARGET_EXT[target]}")
                n = 2
                while str(out).lower() in used:
                    out = root.with_suffix("").with_name(
                        f"{root.stem}_{Path(job.outputs[0].path).stem}_{n}"
                        f"{TARGET_EXT[target]}")
                    n += 1
            used.add(str(out).lower())
            out.write_bytes(text.encode("utf-8"))
            written = str(out)
        return written

    def run_terminal(self) -> None:
        """Save the script where the user chooses, then launch it natively, detached."""
        jobs = self._enabled_jobs()
        if not jobs:
            self.status.emit("nothing to run — add media first")
            return
        target = "bat" if os.name == "nt" else "sh"
        stem = Path(jobs[0].outputs[0].path).stem if len(jobs) == 1 else "convert"
        path, _ = QFileDialog.getSaveFileName(self.shell, "Save and run script",
                                              f"{stem}{TARGET_EXT[target]}")
        if not path:
            return
        suffix = Path(path).suffix.lower()
        if suffix in TARGET_EXT.values():
            target = "bat" if suffix == ".bat" else "sh"
        try:
            self.export_to(path, target)
        except Exception as exc:
            self.status.emit(f"export failed — {exc}")
            return
        try:
            launch_script(path)
        except (RuntimeError, OSError) as exc:
            self.status.emit(f"saved {path} — {exc}")
            return
        self.status.emit(f"saved and launched {path}")
