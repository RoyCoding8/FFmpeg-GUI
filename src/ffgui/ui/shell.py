"""The application shell: sidebar | queue / tabs / preview | bottom bar, plus the
six required states as real widgets. Feature wiring lands on this skeleton."""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QAction, QGuiApplication, QKeySequence
from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QFrame, QHBoxLayout, QLabel, QLineEdit, QListWidget,
    QMainWindow, QMessageBox, QPlainTextEdit, QProgressBar, QPushButton,
    QStackedWidget, QSplitter, QTableWidget, QTabWidget, QVBoxLayout, QWidget,
)

from ffgui.ui import icons
from ffgui.ui.tokens import (
    PREVIEW_MIN_HEIGHT, ROW_HEIGHT, SIDEBAR_WIDTH, SPACING, TAB_NAMES,
)

QUEUE_COLUMNS = ("Status", "Name", "Duration", "Resolution", "Codec", "Output")
FORMATS = (("bat", "Windows batch (.bat)"), ("sh", "Shell script (.sh)"))


def _btn(text: str, *, variant: str | None = None, icon_name: str | None = None,
         tip: str | None = None) -> QPushButton:
    b = QPushButton(text)
    if variant:
        b.setProperty("variant", variant)
    if icon_name:
        b.setIcon(icons.icon(icon_name))
    if tip:
        b.setToolTip(tip)
    return b


class EmptyState(QFrame):
    """The no-inputs state: illustrated drop target over the queue region."""

    add_files_requested = Signal()

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self.add_files_requested.emit()
        super().mousePressEvent(event)

    def __init__(self) -> None:
        super().__init__(objectName="card")
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        inner = QVBoxLayout(self)
        inner.setContentsMargins(SPACING[4], SPACING[5], SPACING[4], SPACING[5])
        pic = QLabel(pixmap=icons.icon("film", size=36).pixmap(36, 36))
        pic.setAlignment(Qt.AlignmentFlag.AlignCenter)
        head = QLabel("No media yet")
        head.setAlignment(Qt.AlignmentFlag.AlignCenter)
        head.setObjectName("title")
        sub = QLabel("Drop media files or folders here — or click to browse")
        sub.setAlignment(Qt.AlignmentFlag.AlignCenter)
        sub.setObjectName("secondary")
        inner.addWidget(pic)
        inner.addWidget(head)
        inner.addWidget(sub)


class NoFfmpegPage(QWidget):
    """The locator state: rescan PATH, browse, manual entry, remembered."""

    accepted = Signal(str)

    def __init__(self) -> None:
        super().__init__()
        col = QVBoxLayout(self)
        col.setContentsMargins(*(SPACING[6],) * 4)
        col.addStretch(1)
        head = QLabel("ffmpeg not found")
        head.setObjectName("windowTitle")
        col.addWidget(head)
        col.addWidget(QLabel("ffgui needs an ffmpeg binary. It is never bundled — "
                             "point at your build (PATH, FFMPEG_PATH, or a folder)."))
        row = QHBoxLayout()
        self.rescan = _btn("Rescan PATH", icon_name="circle-chevron-down")
        self.browse = _btn("Browse…", icon_name="folder")
        row.addWidget(self.rescan)
        row.addWidget(self.browse)
        col.addLayout(row)
        entry = QHBoxLayout()
        self.manual = QLineEdit()
        self.manual.setPlaceholderText("…or paste the full path to ffmpeg here")
        entry.addWidget(self.manual)
        self.remember = QCheckBox("Remember this location")
        self.remember.setChecked(True)
        entry.addWidget(self.remember)
        col.addLayout(entry)
        self.status = QLabel("")
        self.status.setObjectName("statusError")
        col.addWidget(self.status)
        col.addStretch(2)
        self.manual.editingFinished.connect(self._commit_manual)

    def _commit_manual(self) -> None:


        path = self.manual.text().strip()
        if path:
            self.accepted.emit(path)


class LoadingPage(QWidget):
    """The capability-probe state: progress with a cancel affordance."""

    cancelled = Signal()

    def __init__(self) -> None:
        super().__init__()
        col = QVBoxLayout(self)
        col.addStretch(1)
        head = QLabel("Reading your ffmpeg build")
        head.setObjectName("windowTitle")
        head.setAlignment(Qt.AlignmentFlag.AlignCenter)
        col.addWidget(head)
        self.detail = QLabel(" ")
        self.detail.setObjectName("secondary")
        self.detail.setAlignment(Qt.AlignmentFlag.AlignCenter)
        col.addWidget(self.detail)
        self.bar = QProgressBar()
        self.bar.setRange(0, 0)
        col.addWidget(self.bar)
        cancel = _btn("Cancel", icon_name="x")
        cancel.clicked.connect(self.cancelled)
        col.addWidget(cancel, alignment=Qt.AlignmentFlag.AlignCenter)
        col.addStretch(2)


def error_modal(parent: QWidget, title: str, details: str) -> None:
    """error-modal state: message box with a Copy details affordance."""
    box = QMessageBox(parent)
    box.setIcon(QMessageBox.Icon.Critical)
    box.setWindowTitle(title)
    box.setText(next((line.strip() for line in details.splitlines() if line.strip()), title))
    box.setDetailedText(details)
    copy_btn = box.addButton("Copy details", QMessageBox.ButtonRole.ActionRole)
    copy_btn.setIcon(icons.icon("copy"))
    box.addButton(QMessageBox.StandardButton.Close)
    copy_btn.clicked.connect(lambda: QGuiApplication.clipboard().setText(details))
    try:
        box.exec()
    finally:


        box.setParent(None)
        box.deleteLater()


def mark_invalid(line_edit: QLineEdit, message: str | None) -> None:
    """invalid-option state: inline red + tooltip; commit is blocked by the caller."""
    line_edit.setProperty("invalid", message is not None)
    line_edit.setToolTip(message or "")
    line_edit.style().unpolish(line_edit)
    line_edit.style().polish(line_edit)


class Shell(QMainWindow):
    """Region map: sidebar | queue / tabs / preview | bottomBar, in a state stack."""

    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("ffgui — FFmpeg command compiler")
        self.setMinimumSize(640, 480)

        self.sidebar = self._sidebar()
        self.queue_stack, self.queue, self.empty = self._queue()
        self.tabs = self._tabs()
        self.preview = self._preview()
        self.bottom_bar = self._bottom_bar()

        center = QWidget()
        col = QVBoxLayout(center)
        col.setContentsMargins(SPACING[2], SPACING[2], SPACING[2], 0)
        col.setSpacing(SPACING[1])
        col.addWidget(self.queue_stack)
        col.addWidget(self.tabs)
        col.addWidget(self.preview, stretch=1)
        col.addWidget(self.bottom_bar)

        self.sidebar.setMinimumWidth(0)
        center.setMinimumWidth(420)
        self.splitter = QSplitter(Qt.Orientation.Horizontal)
        self.splitter.addWidget(self.sidebar)
        self.splitter.addWidget(center)
        self.splitter.setStretchFactor(0, 0)
        self.splitter.setStretchFactor(1, 1)
        self.splitter.setSizes([SIDEBAR_WIDTH, 900])

        self.workspace = self.splitter
        self.loading = LoadingPage()
        self.no_ffmpeg = NoFfmpegPage()
        self.state_stack = QStackedWidget()
        self.state_stack.addWidget(self.workspace)
        self.state_stack.addWidget(self.loading)
        self.state_stack.addWidget(self.no_ffmpeg)
        self.setCentralWidget(self.state_stack)

        self.regions = {"sidebar": self.sidebar, "queue": self.queue_stack,
                        "tabs": self.tabs, "preview": self.preview,
                        "bottomBar": self.bottom_bar}


    def _sidebar(self) -> QWidget:
        box = QFrame(objectName="sidebar")
        col = QVBoxLayout(box)
        col.setContentsMargins(*(SPACING[1],) * 4)
        head = QLabel("Presets")
        head.setObjectName("title")
        col.addWidget(head)
        self.preset_list = QListWidget()
        self.preset_list.setFrameShape(QListWidget.Shape.NoFrame)
        col.addWidget(self.preset_list, stretch=1)
        row = QHBoxLayout()
        self.preset_save = _btn("Save", icon_name="check", tip="Save current settings as a preset")
        self.preset_delete = _btn("", icon_name="trash-2", tip="Delete selected preset")
        row.addWidget(self.preset_save, stretch=1)
        row.addWidget(self.preset_delete)
        col.addLayout(row)
        return box

    def _queue(self) -> tuple[QStackedWidget, QTableWidget, EmptyState]:
        self.queue = QTableWidget(0, len(QUEUE_COLUMNS))
        self.queue.setHorizontalHeaderLabels(QUEUE_COLUMNS)
        self.queue.verticalHeader().setVisible(False)
        self.queue.verticalHeader().setDefaultSectionSize(ROW_HEIGHT)
        self.queue.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.queue.setAlternatingRowColors(False)
        self.queue.setFrameShape(QTableWidget.Shape.NoFrame)
        self.queue.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.empty = EmptyState()
        stack = QStackedWidget()
        stack.addWidget(self.empty)
        stack.addWidget(self.queue)
        return stack, self.queue, self.empty

    def _tabs(self) -> QTabWidget:
        self.tabs = QTabWidget()
        self.tabs.setDocumentMode(True)
        self.tab_pages: dict[str, QWidget] = {}
        for name in TAB_NAMES:
            page = QWidget()
            QVBoxLayout(page).setContentsMargins(*(SPACING[2],) * 4)
            self.tabs.addTab(page, name)
            self.tab_pages[name] = page
        return self.tabs

    def _preview(self) -> QPlainTextEdit:
        self.preview = QPlainTextEdit(objectName="preview")
        self.preview.setReadOnly(True)
        self.preview.setMinimumHeight(PREVIEW_MIN_HEIGHT)
        self.preview.setPlaceholderText("ffmpeg command preview — select a queue row")
        self.preview.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
        return self.preview

    def _bottom_bar(self) -> QWidget:
        self.bottom_bar = QFrame(objectName="bottomBar")
        row = QHBoxLayout(self.bottom_bar)
        row.setContentsMargins(*(SPACING[1],) * 4)
        row.setSpacing(SPACING[1])
        self.format = QComboBox()
        for _target, label in FORMATS:
            self.format.addItem(label)
        row.addWidget(self.format)
        self.export_btn = _btn("&Export script", variant="primary", icon_name="file-text")
        row.addWidget(self.export_btn)
        self.run_btn = _btn("&Run script", icon_name="play",
                            tip="Save the script, then launch it in a new terminal window")
        row.addWidget(self.run_btn)
        return self.bottom_bar


    def set_state(self, name: str) -> None:
        self.state_stack.setCurrentIndex(("workspace", "loading", "no-ffmpeg").index(name))

    def show_empty(self, empty: bool) -> None:
        self.queue_stack.setCurrentIndex(0 if empty else 1)


    def add_actions(self, add_files, add_folder) -> tuple[QAction, QAction]:
        open_files = QAction(icons.icon("plus"), "&Add files…", self)
        open_files.setShortcut(QKeySequence("Ctrl+O"))
        open_files.triggered.connect(add_files)
        open_folder = QAction(icons.icon("folder"), "Add fol&der…", self)
        open_folder.triggered.connect(add_folder)
        menu = next((a.menu() for a in self.menuBar().actions()
                     if a.menu() is not None and a.menu().title() == "&File"), None)
        if menu is None:
            menu = self.menuBar().addMenu("&File")
        for stale in [a for a in menu.actions()
                      if a.text() in ("&Add files…", "Add fol&der…")]:
            menu.removeAction(stale)
            stale.deleteLater()
        menu.addActions([open_files, open_folder])
        return open_files, open_folder

    def toggle_sidebar(self) -> None:
        sizes = self.splitter.sizes()
        self.splitter.setSizes([0, sum(sizes)] if sizes[0] > 0
                               else [SIDEBAR_WIDTH, sum(sizes) - SIDEBAR_WIDTH])
