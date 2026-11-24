from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QFormLayout, QGroupBox, QHBoxLayout, QHeaderView,
    QLabel, QLineEdit, QListWidget, QListWidgetItem, QPushButton, QScrollArea,
    QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget,
)

from fftui.ffmpeg.capability_index import CapabilityIndex
from fftui.model import Job, Output

from ffgui.ui.expert import ExpertPanel
from ffgui.ui.tokens import SPACING

VIDEO_CONTAINERS = ("mp4", "mkv", "webm", "mov", "avi", "ts")
AUDIO_CONTAINERS = ("mp3", "m4a", "wav", "flac", "ogg")
SUBTITLE_CODECS = ("mov_text", "srt", "ass", "copy")
FORCE_MUXERS = ("matroska", "mp4", "mpegts", "webm", "avi", "wav")
X264_PRESETS = ("ultrafast", "superfast", "veryfast", "faster", "fast",
                "medium", "slow", "slower", "veryslow", "placebo")
X264_TUNES = ("film", "animation", "grain", "stillimage", "psnr", "ssim",
              "fastdecode", "zerolatency")
H264_PROFILES = ("baseline", "main", "high", "high10", "high422", "high444")
AUDIO_CHANNELS = ("1", "2", "6")
SAMPLE_RATES = ("44100", "48000", "96000", "192000")
_HW_SUFFIXES = ("cuvid", "qsv", "vaapi", "dxva2", "d3d11va", "vulkan", "mmal")


def _page_col(page: QWidget) -> QVBoxLayout:
    col = QVBoxLayout(page)
    col.setContentsMargins(*(SPACING[2],) * 4)
    col.setSpacing(SPACING[1])
    return col


def _row() -> QHBoxLayout:
    row = QHBoxLayout()
    row.setSpacing(SPACING[1])
    return row


def _form(title: str) -> tuple[QGroupBox, QFormLayout]:
    box = QGroupBox(title)
    form = QFormLayout(box)
    form.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow)
    form.setSpacing(SPACING[1])
    return box, form


def _combo(*items: str) -> QComboBox:
    box = QComboBox()
    box.addItem("")
    box.addItems(items)
    return box


def _gated(combo: QComboBox, names, tip: str, extra: tuple[str, ...] = ()) -> None:
    if not names:
        combo.setEnabled(False)
        combo.setToolTip(f"not present in this ffmpeg build — {tip}")
        return
    combo.setToolTip(tip)
    if extra:
        combo.addItems(extra)
    combo.addItems(names)


def _secondary_label(text: str) -> QLabel:
    label = QLabel(text)
    label.setObjectName("secondary")
    label.setWordWrap(True)
    return label


def _clean(text: str) -> str:
    return text.replace("\x1f", " ").replace("\n", " ").replace("\r", " ")


class TabPage(QWidget):
    edit = Signal(str, str, str)

    def __init__(self) -> None:
        super().__init__()
        self.widgets: dict[str, QWidget] = {}

    def populate(self, out: Output, job: Job) -> None:
        for w in self.widgets.values():
            w.blockSignals(True)
        try:
            self.load(out, job)
        finally:
            for w in self.widgets.values():
                w.blockSignals(False)

    def load(self, out: Output, job: Job) -> None:
        raise NotImplementedError

    @staticmethod
    def _set_text(w: QLineEdit, value: str) -> None:
        if w.text() != value:
            w.setText(value)

    def _watch(self, signal, kind: str, key: str, to_text=str) -> None:
        signal.connect(lambda *a: self.edit.emit(kind, key, to_text(a[0] if a else None)))

    def _group(self, col: QVBoxLayout, title: str, *, stretch: int = 0) -> QFormLayout:
        box, form = _form(title)
        col.addWidget(box, stretch=stretch)
        return form

    def _field(self, form: QFormLayout | None, name: str, label: str,
               kind: str, key: str = "", placeholder: str = "") -> QLineEdit:
        w = QLineEdit()
        w.setPlaceholderText(placeholder)
        if form is not None:
            form.addRow(label, w)
        self.widgets[name] = w
        self._watch(w.textEdited, kind, key)
        return w

    def _combo(self, form: QFormLayout, name: str, label: str,
               kind: str, key: str = "", *, items: tuple[str, ...] = (),
               gated: tuple | None = None, tip: str = "") -> QComboBox:
        w = _combo(*items)
        if gated is not None:
            _gated(w, *gated)
        elif tip:
            w.setToolTip(tip)
        form.addRow(label, w)
        self.widgets[name] = w
        self._watch(w.currentTextChanged, kind, key)
        return w

    def _check(self, target: QFormLayout | QVBoxLayout | QHBoxLayout, name: str,
               text: str, kind: str, key: str = "", *, tip: str = "",
               to_text=str) -> QCheckBox:
        w = QCheckBox(text)
        if tip:
            w.setToolTip(tip)
        target.addRow(w) if isinstance(target, QFormLayout) else target.addWidget(w)
        self.widgets[name] = w
        self._watch(w.toggled, kind, key, to_text)
        return w


class ContainerTab(TabPage):
    def __init__(self) -> None:
        super().__init__()
        col = _page_col(self)
        f = self._group(col, "Output file")
        self.name = self._field(f, "path", "File name", "field", "path",
                                "output file name for this row")
        self.container = self._combo(f, "container", "Container", "field", "container",
                                     items=(*VIDEO_CONTAINERS, *AUDIO_CONTAINERS))
        self.muxer = self._combo(f, "muxer", "Muxer", "option", "mux:f", items=FORCE_MUXERS,
                                 tip="Force the muxer when several match (dst-muxer-choice)")
        r = self._group(col, "Range (trim)")
        self.start = self._field(r, "start", "Start", "field", "start",
                                 "e.g. 00:00:05 or 5.5")
        self.duration = self._field(r, "duration", "Duration", "field", "duration",
                                    "length, e.g. 90 or 00:01:30")
        self.overwrite = self._check(col, "overwrite", "Overwrite outputs without asking (-y)",
                                     "flag", "noconfirm", to_text=lambda on: str(not on))
        col.addStretch(1)

    def load(self, out: Output, job: Job) -> None:
        self._set_text(self.name, out.path)
        self.container.setCurrentText(out.container or "")
        self.muxer.setCurrentText(out.options.get("f", ""))
        self._set_text(self.start, out.start or "")
        self._set_text(self.duration, out.duration or "")
        self.overwrite.setChecked(not job.noconfirm)


class VideoTab(TabPage):
    def __init__(self, cap: CapabilityIndex) -> None:
        super().__init__()
        col = _page_col(self)
        f = self._group(col, "Video codec")
        self.codec = self._combo(f, "codec", "Codec", "codec", "video",
                                 gated=(cap.video_encoders(),
                                        "encoder list from your ffmpeg build", ("copy",)))
        f = self._group(col, "Rate control and tuning")
        self.crf = self._field(f, "crf", "CRF / CQ", "option", "video:crf",
                               "quality (e.g. 23) — or set bitrate below")
        self.bitrate = self._field(f, "bitrate", "Bitrate", "option", "video:b:v", "e.g. 1500k")
        self.maxrate = self._field(f, "maxrate", "Max rate", "option", "video:maxrate",
                                   "ceiling, e.g. 2M")
        self.preset = self._combo(f, "preset", "Preset", "option", "video:preset",
                                  items=X264_PRESETS)
        self.tune = self._combo(f, "tune", "Tune", "option", "video:tune", items=X264_TUNES)
        self.profile = self._combo(f, "profile", "Profile", "option", "video:profile:v",
                                   items=H264_PROFILES)
        self.pixfmt = self._combo(f, "pixfmt", "Pixel format", "option", "video:pix_fmt",
                                  gated=(cap.pixel_formats, "pixel formats from your build"))
        self.gop = self._field(f, "gop", "GOP", "option", "video:g",
                               "keyframe interval, e.g. 250")
        self.threads = self._field(f, "threads", "Threads", "option", "video:threads", "0 = auto")
        f = self._group(col, "Compatibility")
        self.faststart = self._check(f, "faststart", "Web fast start (movflags +faststart)",
                                     "faststart")
        col.addStretch(1)

    def load(self, out: Output, job: Job) -> None:
        v = out.video_options
        self.codec.setCurrentText(out.video_codec or "")
        self._set_text(self.crf, v.get("crf") or v.get("cq") or "")
        self._set_text(self.bitrate, v.get("b:v") or "")
        self._set_text(self.maxrate, v.get("maxrate") or "")
        self.preset.setCurrentText(v.get("preset") or "")
        self.tune.setCurrentText(v.get("tune") or "")
        self.profile.setCurrentText(v.get("profile:v") or "")
        self.pixfmt.setCurrentText(v.get("pix_fmt") or "")
        self._set_text(self.gop, v.get("g") or "")
        self._set_text(self.threads, v.get("threads") or "")
        self.faststart.setChecked("+faststart" in (out.options.get("movflags") or ""))


class AudioTab(TabPage):
    def __init__(self, cap: CapabilityIndex) -> None:
        super().__init__()
        col = _page_col(self)
        f = self._group(col, "Audio codec")
        self.codec = self._combo(f, "codec", "Codec", "codec", "audio",
                                 gated=(cap.audio_encoders(),
                                        "encoder list from your ffmpeg build", ("copy",)))
        f = self._group(col, "Audio options")
        self.bitrate = self._field(f, "bitrate", "Bitrate", "option", "audio:b:a", "e.g. 192k")
        self.channels = self._combo(f, "channels", "Channels", "option", "audio:ac",
                                    items=AUDIO_CHANNELS)
        self.samplerate = self._combo(f, "samplerate", "Sample rate", "option", "audio:ar",
                                      items=SAMPLE_RATES)
        f = self._group(col, "Processing")
        self.volume = self._field(f, "volume", "Volume", "volume",
                                  placeholder="dB (e.g. -6) or linear (e.g. 0.5)")
        self.loudnorm = self._check(f, "loudnorm", "Loudness normalize (EBU R128)", "loudnorm",
                                    tip="Applies the EBU R128 loudnorm filter")
        col.addStretch(1)

    def load(self, out: Output, job: Job) -> None:
        a = out.audio_options
        self.codec.setCurrentText(out.audio_codec or "")
        self._set_text(self.bitrate, a.get("b:a") or "")
        self.channels.setCurrentText(a.get("ac") or "")
        self.samplerate.setCurrentText(a.get("ar") or "")
        self._set_text(self.volume, self._audio_expr(job, "volume"))
        self.loudnorm.setChecked(self._audio_expr(job, "loudnorm") != "")

    @staticmethod
    def _audio_expr(job: Job, name: str) -> str:
        for expr in job.audio_filters.filters:
            if expr == name or expr.startswith(name + "="):
                return expr[len(name) + 1:] if "=" in expr else ""
        return ""


class SubtitlesTab(TabPage):
    def __init__(self, cap: CapabilityIndex) -> None:
        super().__init__()
        col = _page_col(self)
        f = self._group(col, "Subtitle tracks")
        self.codec = self._combo(f, "codec", "Codec", "codec", "subtitle", items=SUBTITLE_CODECS)
        self.drop = self._check(f, "drop", "Drop subtitle streams (-sn)", "codec", "subtitle",
                                to_text=lambda on: "none" if on else "")
        f = self._group(col, "Burn in")
        self.burn = self._field(f, "burn", "File", "burn",
                                placeholder="subtitle file to render into the pixels")
        col.addStretch(1)

    def load(self, out: Output, job: Job) -> None:
        self.codec.setCurrentText(out.subtitle_codec or "")
        self.drop.setChecked(bool(out.sn_explicit))
        burn = (out.subtitle_burn_in
                or next((f[len("subtitles="):] for f in job.video_filters.filters
                         if f.startswith("subtitles=")), ""))
        self._set_text(self.burn, burn)


class FiltersTab(TabPage):
    def __init__(self, cap: CapabilityIndex) -> None:
        super().__init__()
        col = _page_col(self)
        self.lists: dict[str, QListWidget] = {}
        col.addWidget(_secondary_label(
            "Chain entries run left to right; add from your build's filters, "
            "edit inline, remove with the button."))
        for stream, label in (("video_filters", "Video filters (-vf)"),
                              ("audio_filters", "Audio filters (-af)")):
            form = self._group(col, label)
            listing = QListWidget()
            listing.setFrameShape(QListWidget.Shape.NoFrame)
            listing.setMinimumHeight(72)
            form.addRow(listing)
            row = _row()
            combo = _combo()
            names = [f.name for f in cap.filters
                     if ("A" in (f.signature or "")) == (stream == "audio_filters")]
            _gated(combo, names, f"filters from your build ({stream})")
            params = QLineEdit()
            params.setPlaceholderText("params, e.g. 640:360")
            add, remove = QPushButton("Add"), QPushButton("Remove")
            row.addWidget(combo, stretch=2)
            row.addWidget(params, stretch=3)
            row.addWidget(add)
            row.addWidget(remove)
            form.addRow(row)
            self.lists[stream] = listing
            add.clicked.connect(lambda _, s=stream, c=combo, p=params:
                                self._add(s, c.currentText(), p.text()))
            remove.clicked.connect(
                lambda _, s=stream, lst=listing: self._remove(s, lst.currentRow()))
            listing.itemChanged.connect(
                lambda _, s=stream, lst=listing: self._emit_chain(s, lst))
        col.addStretch(1)
        self.widgets = dict(self.lists)

    def _add(self, stream: str, name: str, params: str) -> None:
        if not name:
            return
        item = QListWidgetItem(f"{name}={params}" if params else name)
        item.setFlags(item.flags() | Qt.ItemFlag.ItemIsEditable)
        self.lists[stream].addItem(item)
        self._emit_chain(stream, self.lists[stream])

    def _remove(self, stream: str, row: int) -> None:
        if row >= 0:
            self.lists[stream].takeItem(row)
            self._emit_chain(stream, self.lists[stream])

    def _emit_chain(self, stream: str, listing: QListWidget) -> None:
        chain = [_clean(listing.item(i).text()) for i in range(listing.count())]
        self.edit.emit("filters", stream, "\n".join(chain))

    def load(self, out: Output, job: Job) -> None:
        for stream, listing in self.lists.items():
            listing.blockSignals(True)
            listing.clear()
            for expr in getattr(job, stream).filters:
                item = QListWidgetItem(expr)
                item.setFlags(item.flags() | Qt.ItemFlag.ItemIsEditable)
                listing.addItem(item)
            listing.blockSignals(False)


class ChaptersTab(TabPage):
    def __init__(self, cap: CapabilityIndex) -> None:
        super().__init__()
        col = _page_col(self)
        col.addWidget(_secondary_label("Chapter marks export as an ffmetadata sidecar."))
        self.table = QTableWidget(0, 3)
        self.table.setHorizontalHeaderLabels(("Start", "Title", "Language"))
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.table.verticalHeader().setVisible(False)
        self.table.setMinimumHeight(140)
        self.table.itemChanged.connect(lambda _: self._emit())
        col.addWidget(self.table)
        row = _row()
        add, remove = QPushButton("Add chapter"), QPushButton("Remove selected")
        row.addWidget(add)
        row.addWidget(remove)
        col.addLayout(row)
        col.addStretch(1)
        add.clicked.connect(lambda: self.table.insertRow(self.table.rowCount()))
        remove.clicked.connect(self._remove_row)
        self.widgets = {"table": self.table}

    def _remove_row(self) -> None:
        row = self.table.currentRow()
        if row >= 0:
            self.table.removeRow(row)
            self._emit()

    def _emit(self) -> None:
        rows = [
            "\x1f".join(_clean(c.text() if c is not None else "")
                        for c in (self.table.item(r, i) for i in range(3)))
            for r in range(self.table.rowCount())
        ]
        self.edit.emit("chapters", "", "\n".join(rows))

    def load(self, out: Output, job: Job) -> None:
        self.table.blockSignals(True)
        self.table.setRowCount(0)
        for ch in out.chapters:
            r = self.table.rowCount()
            self.table.insertRow(r)
            for c, value in enumerate((ch.start, ch.title, ch.lang)):
                self.table.setItem(r, c, QTableWidgetItem(value))
        self.table.blockSignals(False)


class MetadataTab(TabPage):
    def __init__(self) -> None:
        super().__init__()
        col = _page_col(self)
        f = self._group(col, "Tags")
        self.title = self._field(f, "title", "Title", "field", "title")
        self.comment = self._field(f, "comment", "Comment", "field", "comment")
        f = self._group(col, "Cover art")
        self.cover = self._field(f, "cover", "Image", "field", "cover_art",
                                 "image file to attach as cover")
        f = self._group(col, "Per-stream tags")
        self.stream_spec = self._field(f, "stream_spec", "Stream", "streamtag", "spec",
                                       "stream spec, e.g. v:0")
        self.stream_lang = self._field(f, "stream_lang", "Language tag", "streamtag", "language",
                                       "language, e.g. eng")
        col.addStretch(1)

    def load(self, out: Output, job: Job) -> None:
        self._set_text(self.title, out.metadata.get("title", ""))
        self._set_text(self.comment, out.metadata.get("comment", ""))
        self._set_text(self.cover, out.cover_art or "")


class AdvancedTab(TabPage):
    def __init__(self, cap: CapabilityIndex) -> None:
        super().__init__()
        col = _page_col(self)
        f = self._group(col, "Performance")
        hw_decoders = [d.name for d in cap.decoders if d.name.endswith(_HW_SUFFIXES)]
        self.hwdecode = self._combo(f, "hwdecode", "HW decode (decoder)", "hwdecode",
                                    gated=(hw_decoders, "hardware decoders from your build"))
        self.hwdevice = self._field(f, "hwdevice", "HW device", "hwdevice",
                                    placeholder="-filter_hw_device name, e.g. gpu0")
        f = self._group(col, "Output modes")
        self.twopass = self._check(f, "twopass", "Two-pass encode", "flag", "two_pass",
                                   tip="Runs the encode twice, error-checked between passes")
        seg_row = _row()
        self.segment = self._check(seg_row, "segment", "Segment", "segment")
        self.segment_time = self._field(None, "segment_time", "", "segment_time",
                                        placeholder="seconds per part, e.g. 600")
        seg_row.addWidget(self.segment_time, stretch=1)
        f.addRow(seg_row)
        self.bsf = self._field(f, "bsf", "Bitstream filter", "bsf",
                               placeholder="stream=filter, e.g. v:0=h264_mp4toannexb")
        self.tee = self._field(f, "tee", "Tee outputs", "tee",
                               placeholder="[f=mp4]a.mp4|[f=webm]b.webm")
        f = self._group(col, "Input options")
        self.seek = self._field(f, "seek", "Seek to", "input", "-ss",
                                "fast input seek (-ss before -i), e.g. 10")
        self.loop = self._field(f, "loop", "Loop", "input", "-loop",
                                "-loop count (stills/short inputs)")
        f = self._group(col, "Expert — every AVOption in your build", stretch=1)
        self.expert = ExpertPanel(cap)
        self.expert.setMinimumHeight(220)
        f.addRow(self.expert)

    def load(self, out: Output, job: Job) -> None:
        self._set_text(self.hwdevice, job.filter_hw_device or "")
        self.twopass.setChecked(job.two_pass)
        self.segment.setChecked(bool(out.segment_enabled))
        self._set_text(self.segment_time, out.segment_time or "")
        self._set_text(self.bsf, next((f"{k}={v}" for k, v in out.bsf.items()), ""))
        self._set_text(self.tee, out.tee_spec or "")
        inp = job.inputs[0] if job.inputs else None
        self.hwdecode.setCurrentText(inp.decoder_v if inp and inp.decoder_v else "")
        args = inp.input_args if inp else []
        self._set_text(self.seek, _flag(args, "-ss"))
        self._set_text(self.loop, _flag(args, "-loop"))


def _flag(args: list[str], flag: str) -> str:
    if flag in args:
        i = args.index(flag)
        if i + 1 < len(args):
            return args[i + 1]
    return ""


def wrap_scroll(page: TabPage) -> QScrollArea:
    area = QScrollArea()
    area.setWidgetResizable(True)
    area.setWidget(page)
    return area


def build_tabs(cap: CapabilityIndex) -> dict[str, TabPage]:
    return {
        "Container": ContainerTab(),
        "Video": VideoTab(cap),
        "Audio": AudioTab(cap),
        "Subtitles": SubtitlesTab(cap),
        "Filters": FiltersTab(cap),
        "Chapters": ChaptersTab(cap),
        "Metadata": MetadataTab(),
        "Advanced": AdvancedTab(cap),
    }
