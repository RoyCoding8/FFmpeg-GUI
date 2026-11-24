from __future__ import annotations

import json
import os
import re
import shutil as shutil
import subprocess as subprocess
import sys
from pathlib import Path

from PySide6.QtCore import (QEvent, QItemSelection, QItemSelectionModel, QObject,
                            QSettings, Qt, Signal)
from PySide6.QtGui import QAction, QKeySequence
from PySide6.QtWidgets import (
    QApplication, QCheckBox, QDialog, QDialogButtonBox, QFileDialog,
    QInputDialog, QLineEdit, QMenu, QTableWidgetItem, QVBoxLayout,
)

from fftui.ffmpeg.capability_index import CapabilityIndex
from fftui.model import Job, Output
from fftui.util.command_builder import build, build_two_pass

from ffgui.doc import QueueDocument, QueueItem
from ffgui.export import script_for_queue
from ffgui.runtime import (
    MEDIA_SUFFIXES as MEDIA_SUFFIXES,
    TARGET_EXT as TARGET_EXT,
    _LINUX_TERMINALS as _LINUX_TERMINALS,
    aim_ffmpeg as aim_ffmpeg,
    launch_script as _launch_script,
    locate_ffmpeg as _locate_ffmpeg,
    terminal_argv as _terminal_argv,
)
from ffgui.store import delete_preset, fresh_meta, is_truthy, load_presets, save_preset
from ffgui.ui import icons
from ffgui.ui.shell import Shell, mark_invalid
from ffgui.ui.tabs import build_tabs, wrap_scroll

_BARE_NEGATIVE_RE = re.compile(r"-\d*\.?\d+")
_NO_OUTPUT_KINDS = ("codec", "option", "field", "flag", "hwdecode", "input")


def terminal_argv(path: str) -> list[str] | None:
    return _terminal_argv(path, platform=sys.platform)


def launch_script(path: str) -> None:
    _launch_script(path, platform=sys.platform, resolver=terminal_argv)


def locate_ffmpeg(settings: QSettings) -> tuple[str | None, bool]:
    return _locate_ffmpeg(settings, executable=sys.executable)


def _apply_volume(doc: QueueDocument, kind: str, row: int, value: str) -> str | None:
    keep = [f for f in doc.items[row].job.audio_filters.filters
            if f != kind and not f.startswith(f"{kind}=")]
    if kind == "loudnorm":
        if is_truthy(value):
            keep.append("loudnorm=I=-16:TP=-1.5:LRA=11")
    elif value:
        gain = f"{value}dB" if _BARE_NEGATIVE_RE.fullmatch(value) else value
        keep.append(f"volume={gain}")
    return doc.set_filters(row, "audio_filters", keep)


_EDITS = {
    "codec": lambda c, r, k, v: c.doc.set_codec(r, k, v),
    "option": lambda c, r, k, v: c._apply_option(r, k, v),
    "field": lambda c, r, k, v: c._apply_field(r, k, v),
    "flag": lambda c, r, k, v: c._apply_flag(r, k, v),
    "filters": lambda c, r, k, v: c.doc.set_filters(r, k, v.split("\n") if v else []),
    "chapters": lambda c, r, k, v: c.doc.set_chapters(r, v),
    "burn": lambda c, r, k, v: c.doc.set_output_field(r, "subtitle_burn_in", v),
    "faststart": lambda c, r, k, v: c.doc.set_faststart(r, v),
    "hwdecode": lambda c, r, k, v: c.doc.set_hw_decode(r, v),
    "hwdevice": lambda c, r, k, v: c.doc.set_option(r, "global", "filter_hw_device", v),
    "segment": lambda c, r, k, v: c.doc.set_segment_enabled(r, v),
    "segment_time": lambda c, r, k, v: c.doc.set_output_field(r, "segment_time", v),
    "bsf": lambda c, r, k, v: c.doc.set_bsf(r, v),
    "tee": lambda c, r, k, v: c.doc.set_tee(r, v),
    "input": lambda c, r, k, v: c.doc.set_input_arg(r, k, v),
    "volume": lambda c, r, k, v: _apply_volume(c.doc, "volume", r, v),
    "loudnorm": lambda c, r, k, v: _apply_volume(c.doc, "loudnorm", r, v),
    "streamtag": lambda c, r, k, v: c._apply_streamtag(r),
}


class _DropFilter(QObject):
    pathsDropped = Signal(list)

    def eventFilter(self, obj, event) -> bool:
        t = event.type()
        if t in (QEvent.Type.DragEnter, QEvent.Type.DragMove) and event.mimeData().hasUrls():
            event.acceptProposedAction()
            return True
        if t == QEvent.Type.Drop and event.mimeData().hasUrls():
            paths = [u.toLocalFile() for u in event.mimeData().urls() if u.isLocalFile()]
            if paths:
                self.pathsDropped.emit(paths)
            event.acceptProposedAction()
            return True
        return False


class Controller(QObject):
    status = Signal(str)

    def __init__(self, shell: Shell, doc: QueueDocument, cap: CapabilityIndex,
                 settings: QSettings) -> None:
        super().__init__(shell)
        self.shell, self.doc, self.cap, self.settings = shell, doc, cap, settings
        self.tabs = build_tabs(cap)
        self._last_rows: list[int] | None = None
        self._basic_edits = {"codec": doc.set_codec, "option": self._apply_option,
                             "field": self._apply_field, "flag": self._apply_flag}
        current_tab = shell.tabs.currentIndex()
        while shell.tabs.count():
            shell.tabs.widget(0).deleteLater()
            shell.tabs.removeTab(0)
        for name, page in self.tabs.items():
            if (expert := getattr(page, "expert", None)) is not None:
                expert.optionCommitted.connect(self._on_expert_committed)
                expert.statusMessage.connect(lambda m: shell.statusBar().showMessage(m, 8000))
            page.edit.connect(self._on_tab_edit)
            shell.tabs.addTab(wrap_scroll(page), name)
        shell.tabs.setCurrentIndex(current_tab)

        shell.add_actions(self.add_files_dialog, self.add_folder_dialog)
        shell.export_btn.clicked.connect(lambda _=False: self.export_dialog())
        shell.run_btn.clicked.connect(self.run_terminal)
        shell.empty.add_files_requested.connect(self.add_files_dialog)
        shell.queue.itemSelectionChanged.connect(self._on_selection)
        shell.queue.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        shell.queue.customContextMenuRequested.connect(self._context_menu)
        dropper = _DropFilter(self)
        dropper.pathsDropped.connect(self.add_paths)
        shell.queue.setAcceptDrops(True)
        shell.queue.viewport().installEventFilter(dropper)
        shell.empty.installEventFilter(dropper)
        shell.preset_list.itemDoubleClicked.connect(lambda it: self.apply_preset(it.text()))
        shell.preset_save.clicked.connect(self.save_preset_dialog)
        shell.preset_delete.clicked.connect(self.delete_preset)
        remove = QAction("Remove selected", shell.queue)
        remove.setShortcuts([QKeySequence(Qt.Key.Key_Delete),
                             QKeySequence(Qt.Key.Key_Backspace)])
        remove.setShortcutContext(Qt.ShortcutContext.WidgetWithChildrenShortcut)
        remove.triggered.connect(self.remove_selected)
        shell.queue.addAction(remove)
        self.status.connect(lambda text: shell.statusBar().showMessage(text, 8000))
        self.refresh_presets()
        self.refresh()

    def refresh(self) -> None:
        table = self.shell.queue
        table.setRowCount(len(self.doc))
        for row, item in enumerate(self.doc.items):
            status = QTableWidgetItem(
                icons.icon("triangle-alert" if item.error else "check"), "")
            status.setToolTip(item.error or item.meta["notes"] or "ready")
            table.setItem(row, 0, status)
            inp = item.job.inputs[0] if item.job.inputs else None
            video = next((s for s in (inp.streams if inp else ()) if s.codec_type == "video"), None)
            out = item.job.outputs[0] if item.job.outputs else None
            codecs = (" · ".join(p for p in (out.video_codec, out.audio_codec, out.subtitle_codec) if p)
                      if out else "")
            cells = (
                item.meta["name"],
                f"{video.width}×{video.height}" if video and video.width else "",
                codecs,
                Path(out.path).name if out else "",
            )
            for col, text in enumerate(cells, start=1):
                cell = QTableWidgetItem(text)
                if col == 4 and out:
                    cell.setToolTip(out.path)
                table.setItem(row, col, cell)
        self.shell.show_empty(len(self.doc) == 0)
        self._on_selection()

    def selected_rows(self) -> list[int]:
        return sorted({i.row() for i in self.shell.queue.selectedIndexes()})

    def _select_rows(self, rows) -> None:
        model = self.shell.queue.model()
        selection = QItemSelection()
        for row in rows:
            idx = model.index(row, 0)
            selection.select(idx, idx)
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
        out, job = ((item.job.outputs[0], item.job) if item.job.outputs else (Output(path=""), Job()))
        for page in self.tabs.values():
            page.populate(out, job)
        self._update_preview()

    def _update_preview(self) -> None:
        rows = self.selected_rows()
        if not rows:
            self.shell.preview.setPlainText("")
            return
        chunks = []
        for row in rows:
            job = self.doc.items[row].job
            try:
                if job.two_pass:
                    chunks.append(" &&\n".join(" ".join(a) for a in build_two_pass(job)))
                else:
                    chunks.append(" ".join(build(job)))
            except Exception as exc:
                chunks.append(f"# {type(exc).__name__}: {exc}")
        self.shell.preview.setPlainText("\n\n".join(chunks))

    def _on_tab_edit(self, kind: str, key: str, value: str) -> None:
        rows = self.selected_rows()
        if not rows:
            return
        error = None
        for row in rows:
            error = self._apply_edit(kind, key, value, row) or error
        if error:
            widget = self.shell.tabs.currentWidget().focusWidget()
            if isinstance(widget, QLineEdit):
                mark_invalid(widget, error)
            self.status.emit(error)
        self.refresh()

    def _on_expert_committed(self, kind: str, component: str, opt, value: str) -> None:
        if error := self._apply_expert(kind, component, opt, value):
            self.status.emit(error)
        self.refresh()

    def _apply_expert(self, kind: str, component: str, opt, value: str) -> str | None:
        rows = self.selected_rows()
        if not rows:
            return "select a queue row first"
        row = rows[0]
        if kind == "filter":
            stream = "audio_filters" if self.cap.filter_kind(component) == "audio" else "video_filters"
            prefix = f"{component}={opt.name}"
            chain = [f for f in getattr(self.doc.items[row].job, stream).filters
                     if f != prefix and not f.startswith(f"{prefix}=")]
            if value:
                chain.append(f"{prefix}={value}")
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

    def _apply_edit(self, kind: str, key: str, value: str, row: int) -> str | None:
        if not isinstance(row, int) or not 0 <= row < len(self.doc):
            return f"no such row {row}"
        if not self.doc.items[row].job.outputs and kind not in _NO_OUTPUT_KINDS:
            return "row has no output yet"
        edit = _EDITS.get(kind) if isinstance(kind, str) else None
        if edit is None:
            return f"unknown edit {kind!r}"
        return edit(self, row, key, value if isinstance(value, str) else "")

    _apply_advanced = _apply_edit

    def _apply_streamtag(self, row: int) -> str | None:
        meta = self.tabs["Metadata"]
        spec, lang = meta.stream_spec.text().strip(), meta.stream_lang.text().strip()
        if not spec:
            return "enter a stream spec (e.g. v:0) and a language tag"
        return self.doc.set_stream_meta(row, spec, "language", lang)

    def _apply_option(self, row: int, key: str, value: str) -> str | None:
        scope, _, real = (key if isinstance(key, str) else "").partition(":")
        return self.doc.set_option(row, scope, real, value)

    def _apply_field(self, row: int, key: str, value: str) -> str | None:
        if key in ("title", "comment"):
            return self.doc.set_metadata(row, key, value)
        return self.doc.set_output_field(row, key, value)

    def _apply_flag(self, row: int, key: str, value: str) -> str | None:
        if not isinstance(row, int) or not 0 <= row < len(self.doc):
            return f"no such row {row}"
        return self.doc.set_flag(row, key, value if isinstance(value, str) else "")

    def add_paths(self, paths: list[str] | None, recursive: bool = False) -> None:
        if QApplication.activeModalWidget():
            self.status.emit("close the dialog first — drop refused")
            return
        if isinstance(paths, (str, os.PathLike)):
            paths = [paths]
        try:
            items = list(paths) if paths else []
        except TypeError:
            return
        files: list[str] = []
        for raw in items:
            if not isinstance(raw, (str, os.PathLike)):
                continue
            p = Path(raw)
            if p.is_dir():
                found = sorted(
                    str(f) for f in (p.rglob("*") if recursive else p.iterdir())
                    if f.suffix.lower() in MEDIA_SUFFIXES
                )
                files.extend(found)
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
        if folder := QFileDialog.getExistingDirectory(self.shell, "Add folder"):
            self.add_paths([folder], recursive=True)

    def remove_selected(self) -> None:
        if rows := self.selected_rows():
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
        if error := self.doc.join_parts(rows[0], paths):
            self.status.emit(error)
            return
        self.refresh()
        self._update_preview()

    def _context_menu(self, pos) -> None:
        menu = QMenu(self.shell)
        for label, fn in (
            ("&Duplicate", self.duplicate_selected), ("&Remove", self.remove_selected),
            ("Move &up", lambda: self.move_selected(-1)), ("Move &down", lambda: self.move_selected(1)),
            ("&Join parts…", self.concat_parts_dialog), ("Stream &info…", self.stream_info_dialog),
        ):
            menu.addAction(label, fn)
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
        for s in inp.streams:
            box = QCheckBox(
                f"{s.spec}  {s.codec_name or '?'}  "
                f"{s.width or s.sample_rate or ''}{f'×{s.height}' if s.height else ''}")
            box.setChecked(s.mapped)
            box.toggled.connect(lambda on, st=s: setattr(st, "mapped", on))
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
        item = self.doc.items[rows[0]]
        if item.error or not item.job.inputs or not item.job.outputs:
            self.status.emit("select a healthy row to save as a preset")
            return
        name, ok = QInputDialog.getText(self.shell, "Save preset", "Preset name")
        if ok and name:
            save_preset(name, item.job, item.meta)
            self.refresh_presets()

    def apply_preset(self, name: str) -> None:
        rows = self.selected_rows()
        if not rows:
            self.status.emit("select a row to apply the preset to")
            return
        preset = load_presets().get(name)
        if preset is None:
            return
        preset_dict = preset[0].to_dict()
        for row in rows:
            item = self.doc.items[row]
            job = Job.from_dict(preset_dict)
            if item.job.inputs and job.inputs:
                job.inputs[0] = item.job.inputs[0]
            for out, target in zip(job.outputs, item.job.outputs):
                out.path = target.path
            job.outputs = job.outputs[:len(item.job.outputs)]
            item.job = job
            self.doc.touch(row)
        self.refresh()

    def delete_preset(self) -> None:
        if item := self.shell.preset_list.currentItem():
            delete_preset(item.text())
            self.refresh_presets()

    def open_job_file(self, path: str) -> None:
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

    def _save_script(self, title: str, message: str, target: str, one_file: bool) -> str:
        jobs = self._enabled_jobs()
        if not jobs:
            self.status.emit(message)
            return ""
        stem = Path(jobs[0].outputs[0].path).stem if len(jobs) == 1 else "convert"
        path, _ = QFileDialog.getSaveFileName(self.shell, title, f"{stem}{TARGET_EXT[target]}")
        if not path:
            return ""
        suffix = Path(path).suffix.lower()
        if suffix in TARGET_EXT.values():
            target = "bat" if suffix == ".bat" else "sh"
        try:
            return self.export_to(path, target, one_file)
        except Exception as exc:
            self.status.emit(f"export failed — {exc}")
            return ""

    def export_dialog(self, one_file: bool = True) -> None:
        target = "bat" if self.shell.format.currentIndex() == 0 else "sh"
        if written := self._save_script(
            "Export script", "nothing to export — add media first", target, one_file
        ):
            self.status.emit(f"Exported {written}")

    def run_terminal(self) -> None:
        path = self._save_script("Save and run script", "nothing to run — add media first",
                                 "bat" if os.name == "nt" else "sh", True)
        if not path:
            return
        try:
            launch_script(path)
        except (RuntimeError, OSError) as exc:
            self.status.emit(f"saved {path} — {exc}")
            return
        self.status.emit(f"saved and launched {path}")

    def export_to(self, path: str, target: str, one_file: bool = True) -> str:
        if not isinstance(path, (str, os.PathLike)):
            raise ValueError(f"export path must be a string, got {type(path).__name__}")
        jobs = self._enabled_jobs()
        result = script_for_queue(jobs, target, self.cap.version_hash, one_file=one_file)
        if one_file:
            Path(path).write_bytes(result.encode("utf-8"))
            return path
        root, ext, written, used = Path(path), TARGET_EXT[target], "", set()
        for job, text in zip(jobs, result):
            if len(jobs) == 1:
                out = root
            else:
                base = f"{root.stem}_{Path(job.outputs[0].path).stem}"
                out, n = root.with_name(f"{base}{ext}"), 2
                while str(out).lower() in used:
                    out, n = root.with_name(f"{base}_{n}{ext}"), n + 1
            used.add(str(out).lower())
            out.write_bytes(text.encode("utf-8"))
            written = str(out)
        return written
