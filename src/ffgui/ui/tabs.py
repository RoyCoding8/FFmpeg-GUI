"""The eight tab pages: widgets load from a QueueItem and emit typed edits into
QueueDocument. Every capability combo populates from the probed CapabilityIndex
and DISABLES with a tooltip when absent."""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QFormLayout, QGroupBox,
    QHBoxLayout, QHeaderView, QLineEdit, QListWidget, QListWidgetItem,
    QPushButton, QScrollArea, QTableWidget, QVBoxLayout, QWidget,
)

from fftui.ffmpeg.capability_index import CapabilityIndex
from fftui.model import Job, Output

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


def _page_col(page: QWidget) -> QVBoxLayout:
    col = QVBoxLayout(page)
    col.setContentsMargins(SPACING[2], SPACING[2], SPACING[2], SPACING[2])
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
    """Populate from the probed build, or disable visibly when absent."""
    if not names:
        combo.setEnabled(False)
        combo.setToolTip(f"not present in this ffmpeg build — {tip}")
        return
    combo.setToolTip(tip)
    if extra:
        combo.addItems(extra)
    combo.addItems(names)


class TabPage(QWidget):
    """Base: populate() fills widgets with signals blocked; user edits emit edit()."""

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


class ContainerTab(TabPage):
    def __init__(self) -> None:
        super().__init__()
        col = _page_col(self)
        file_box, file_form = _form("Output file")
        self.name = QLineEdit()
        self.name.setPlaceholderText("output file name for this row")
        file_form.addRow("File name", self.name)
        self.container = _combo(*VIDEO_CONTAINERS, *AUDIO_CONTAINERS)
        file_form.addRow("Container", self.container)
        self.muxer = _combo(*FORCE_MUXERS)
        self.muxer.setToolTip("Force the muxer when several match (dst-muxer-choice)")
        file_form.addRow("Muxer", self.muxer)
        col.addWidget(file_box)
        range_box, range_form = _form("Range (trim)")
        self.start = QLineEdit()
        self.start.setPlaceholderText("e.g. 00:00:05 or 5.5")
        self.duration = QLineEdit()
        self.duration.setPlaceholderText("length, e.g. 90 or 00:01:30")
        range_form.addRow("Start", self.start)
        range_form.addRow("Duration", self.duration)
        col.addWidget(range_box)
        self.overwrite = QCheckBox("Overwrite outputs without asking (-y)")
        col.addWidget(self.overwrite)
        col.addStretch(1)
        self.widgets = {"path": self.name, "container": self.container,
                        "muxer": self.muxer, "start": self.start,
                        "duration": self.duration, "overwrite": self.overwrite}
        self._watch(self.name.textEdited, "field", "path")
        self._watch(self.container.currentTextChanged, "field", "container")
        self._watch(self.muxer.currentTextChanged, "option", "mux:f")
        self._watch(self.start.textEdited, "field", "start")
        self._watch(self.duration.textEdited, "field", "duration")
        self._watch(self.overwrite.toggled, "flag", "noconfirm", lambda on: str(not on))

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
        codec_box, codec_form = _form("Video codec")
        self.codec = QComboBox()
        self.codec.addItem("")
        _gated(self.codec, cap.video_encoders(),
               "encoder list from your ffmpeg build", extra=("copy",))
        codec_form.addRow("Codec", self.codec)
        col.addWidget(codec_box)

        opt_box, opt_form = _form("Rate control and tuning")
        self.crf = QLineEdit()
        self.crf.setPlaceholderText("quality (e.g. 23) — or set bitrate below")
        opt_form.addRow("CRF / CQ", self.crf)
        self.bitrate = QLineEdit()
        self.bitrate.setPlaceholderText("e.g. 1500k")
        opt_form.addRow("Bitrate", self.bitrate)
        self.maxrate = QLineEdit()
        self.maxrate.setPlaceholderText("ceiling, e.g. 2M")
        opt_form.addRow("Max rate", self.maxrate)
        self.preset = _combo(*X264_PRESETS)
        opt_form.addRow("Preset", self.preset)
        self.tune = _combo(*X264_TUNES)
        opt_form.addRow("Tune", self.tune)
        self.profile = _combo(*H264_PROFILES)
        opt_form.addRow("Profile", self.profile)
        self.pixfmt = QComboBox()
        self.pixfmt.addItem("")
        _gated(self.pixfmt, cap.pixel_formats, "pixel formats from your build")
        opt_form.addRow("Pixel format", self.pixfmt)
        self.gop = QLineEdit()
        self.gop.setPlaceholderText("keyframe interval, e.g. 250")
        opt_form.addRow("GOP", self.gop)
        self.threads = QLineEdit()
        self.threads.setPlaceholderText("0 = auto")
        opt_form.addRow("Threads", self.threads)
        col.addWidget(opt_box)

        misc_box, misc_form = _form("Compatibility")
        self.faststart = QCheckBox("Web fast start (movflags +faststart)")
        misc_form.addRow(self.faststart)
        col.addWidget(misc_box)
        col.addStretch(1)
        self.widgets = {"codec": self.codec, "crf": self.crf, "bitrate": self.bitrate,
                        "maxrate": self.maxrate, "preset": self.preset,
                        "tune": self.tune, "profile": self.profile,
                        "pixfmt": self.pixfmt, "gop": self.gop,
                        "threads": self.threads, "faststart": self.faststart}
        self._watch(self.codec.currentTextChanged, "codec", "video")
        for key, widget in (("crf", self.crf), ("b:v", self.bitrate),
                            ("maxrate", self.maxrate), ("g", self.gop),
                            ("threads", self.threads)):
            self._watch(widget.textEdited, "option", f"video:{key}")
        for key, widget in (("preset", self.preset), ("tune", self.tune),
                            ("profile:v", self.profile), ("pix_fmt", self.pixfmt)):
            self._watch(widget.currentTextChanged, "option", f"video:{key}")
        self._watch(self.faststart.toggled, "faststart", "")

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
        codec_box, codec_form = _form("Audio codec")
        self.codec = QComboBox()
        self.codec.addItem("")
        _gated(self.codec, cap.audio_encoders(),
               "encoder list from your ffmpeg build", extra=("copy",))
        codec_form.addRow("Codec", self.codec)
        col.addWidget(codec_box)
        opt_box, opt_form = _form("Audio options")
        self.bitrate = QLineEdit()
        self.bitrate.setPlaceholderText("e.g. 192k")
        opt_form.addRow("Bitrate", self.bitrate)
        self.channels = _combo(*AUDIO_CHANNELS)
        opt_form.addRow("Channels", self.channels)
        self.samplerate = _combo(*SAMPLE_RATES)
        opt_form.addRow("Sample rate", self.samplerate)
        col.addWidget(opt_box)
        fx_box, fx_form = _form("Processing")
        self.volume = QLineEdit()
        self.volume.setPlaceholderText("dB (e.g. -6) or linear (e.g. 0.5)")
        fx_form.addRow("Volume", self.volume)
        self.loudnorm = QCheckBox("Loudness normalize (EBU R128)")
        self.loudnorm.setToolTip("Applies the EBU R128 loudnorm filter")
        fx_form.addRow(self.loudnorm)
        col.addWidget(fx_box)
        col.addStretch(1)
        self.widgets = {"codec": self.codec, "bitrate": self.bitrate,
                        "channels": self.channels, "samplerate": self.samplerate,
                        "volume": self.volume, "loudnorm": self.loudnorm}
        self._watch(self.codec.currentTextChanged, "codec", "audio")
        self._watch(self.bitrate.textEdited, "option", "audio:b:a")
        self._watch(self.channels.currentTextChanged, "option", "audio:ac")
        self._watch(self.samplerate.currentTextChanged, "option", "audio:ar")
        self._watch(self.volume.textEdited, "volume", "")
        self._watch(self.loudnorm.toggled, "loudnorm", "")

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
        box, form = _form("Subtitle tracks")
        self.codec = _combo(*SUBTITLE_CODECS)
        form.addRow("Codec", self.codec)
        self.drop = QCheckBox("Drop subtitle streams (-sn)")
        form.addRow(self.drop)
        col.addWidget(box)
        burn_box, burn_form = _form("Burn in")
        self.burn = QLineEdit()
        self.burn.setPlaceholderText("subtitle file to render into the pixels")
        burn_form.addRow("File", self.burn)
        col.addWidget(burn_box)
        col.addStretch(1)
        self.widgets = {"codec": self.codec, "drop": self.drop, "burn": self.burn}
        self._watch(self.codec.currentTextChanged, "codec", "subtitle")
        self.drop.toggled.connect(
            lambda on: self.edit.emit("codec", "subtitle", "none" if on else ""))
        self._watch(self.burn.textEdited, "burn", "")

    def load(self, out: Output, job: Job) -> None:
        self.codec.setCurrentText(out.subtitle_codec or "")
        self.drop.setChecked(bool(out.sn_explicit))
        burn = (out.subtitle_burn_in
                or next((f[len("subtitles="):] for f in job.video_filters.filters
                         if f.startswith("subtitles=")), ""))
        self._set_text(self.burn, burn)


class FiltersTab(TabPage):
    """Filter chain editor: -vf/-af chains with capability-gated filter names."""

    def __init__(self, cap: CapabilityIndex) -> None:
        super().__init__()
        col = _page_col(self)
        self.lists: dict[str, QListWidget] = {}
        col.addWidget(QLabel_("Chain entries run left to right; add from your "
                              "build's filters, edit inline, remove with the button."))
        for stream, label in (("video_filters", "Video filters (-vf)"),
                              ("audio_filters", "Audio filters (-af)")):
            box, form = _form(label)
            listing = QListWidget()
            listing.setFrameShape(QListWidget.Shape.NoFrame)
            listing.setMinimumHeight(72)
            form.addRow(listing)
            row = _row()
            combo = QComboBox()
            combo.addItem("")
            sig = lambda f: (f.signature or "")
            names = ([f.name for f in cap.filters if "A" in sig(f)]
                     if stream == "audio_filters"
                     else [f.name for f in cap.filters if "A" not in sig(f)])
            _gated(combo, names, f"filters from your build ({stream})")
            params = QLineEdit()
            params.setPlaceholderText("params, e.g. 640:360")
            add = QPushButton("Add")
            remove = QPushButton("Remove")
            row.addWidget(combo, stretch=2)
            row.addWidget(params, stretch=3)
            row.addWidget(add)
            row.addWidget(remove)
            form.addRow(row)
            col.addWidget(box)
            self.lists[stream] = listing
            add.clicked.connect(lambda _, s=stream, c=combo, p=params:
                                self._add(s, c.currentText(), p.text()))
            remove.clicked.connect(
                lambda _, s=stream, l=listing: self._remove(s, l.currentRow()))
            listing.itemChanged.connect(
                lambda _, s=stream, l=listing: self._emit_chain(s, l))
        col.addStretch(1)
        self.widgets = dict(self.lists)

    def _add(self, stream, name, params):
        if not name:
            return


        item = QListWidgetItem(f"{name}={params}" if params else name)
        item.setFlags(item.flags() | Qt.ItemFlag.ItemIsEditable)
        self.lists[stream].addItem(item)
        self._emit_chain(stream, self.lists[stream])

    def _remove(self, stream, row):
        if row >= 0:
            self.lists[stream].takeItem(row)
            self._emit_chain(stream, self.lists[stream])

    def _emit_chain(self, stream, listing):


        def clean(item):
            return (item.text().replace("\x1f", " ").replace("\n", " ")
                    .replace("\r", " "))
        chain = [clean(listing.item(i)) for i in range(listing.count())]
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


def QLabel_(text: str):
    from PySide6.QtWidgets import QLabel
    label = QLabel(text)
    label.setObjectName("secondary")
    label.setWordWrap(True)
    return label


class ChaptersTab(TabPage):
    def __init__(self, cap: CapabilityIndex) -> None:
        super().__init__()
        col = _page_col(self)
        col.addWidget(QLabel_("Chapter marks export as an ffmetadata sidecar."))
        self.table = QTableWidget(0, 3)
        self.table.setHorizontalHeaderLabels(("Start", "Title", "Language"))
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.table.verticalHeader().setVisible(False)
        self.table.setMinimumHeight(140)
        self.table.itemChanged.connect(lambda _: self._emit())
        col.addWidget(self.table)
        row = _row()
        add = QPushButton("Add chapter")
        remove = QPushButton("Remove selected")
        row.addWidget(add)
        row.addWidget(remove)
        col.addLayout(row)
        col.addStretch(1)


        add.clicked.connect(lambda: self.table.insertRow(self.table.rowCount()))
        remove.clicked.connect(self._remove_row)
        self.widgets = {"table": self.table}

    def _remove_row(self):
        row = self.table.currentRow()
        if row >= 0:
            self.table.removeRow(row)
            self._emit()

    def _emit(self):


        def clean(cell):
            text = cell.text() if cell is not None else ""
            return text.replace("\x1f", " ").replace("\n", " ").replace("\r", " ")
        rows = ["\x1f".join(clean(self.table.item(r, c)) for c in range(3))
                for r in range(self.table.rowCount())]
        self.edit.emit("chapters", "", "\n".join(rows))

    def load(self, out: Output, job: Job) -> None:
        from PySide6.QtWidgets import QTableWidgetItem
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
        box, form = _form("Tags")
        self.title = QLineEdit()
        self.comment = QLineEdit()
        form.addRow("Title", self.title)
        form.addRow("Comment", self.comment)
        cover_box, cover_form = _form("Cover art")
        self.cover = QLineEdit()
        self.cover.setPlaceholderText("image file to attach as cover")
        cover_form.addRow("Image", self.cover)
        streams_box, streams_form = _form("Per-stream tags")
        self.stream_spec = QLineEdit()
        self.stream_spec.setPlaceholderText("stream spec, e.g. v:0")
        self.stream_lang = QLineEdit()
        self.stream_lang.setPlaceholderText("language, e.g. eng")
        streams_form.addRow("Stream", self.stream_spec)
        streams_form.addRow("Language tag", self.stream_lang)
        self.widgets = {"title": self.title, "comment": self.comment,
                        "cover": self.cover, "stream_spec": self.stream_spec,
                        "stream_lang": self.stream_lang}
        outer = _page_col(self)
        outer.addWidget(box)
        outer.addWidget(cover_box)
        outer.addWidget(streams_box)
        outer.addStretch(1)
        self._watch(self.title.textEdited, "field", "title")
        self._watch(self.comment.textEdited, "field", "comment")
        self._watch(self.cover.textEdited, "field", "cover_art")
        self._watch(self.stream_lang.textEdited, "streamtag", "language")
        self._watch(self.stream_spec.textEdited, "streamtag", "spec")

    def load(self, out: Output, job: Job) -> None:
        self._set_text(self.title, out.metadata.get("title", ""))
        self._set_text(self.comment, out.metadata.get("comment", ""))
        self._set_text(self.cover, out.cover_art or "")


class AdvancedTab(TabPage):
    def __init__(self, cap: CapabilityIndex) -> None:
        super().__init__()
        col = _page_col(self)
        perf_box, perf_form = _form("Performance")
        self.hwdecode = QComboBox()
        self.hwdecode.addItem("")
        hw_decoders = [d.name for d in cap.decoders
                       if d.name.endswith(("cuvid", "qsv", "vaapi", "dxva2",
                                           "d3d11va", "vulkan", "mmal"))]
        _gated(self.hwdecode, hw_decoders, "hardware decoders from your build")
        self.hwdecode.setToolTip("hardware decoders from your build")
        perf_form.addRow("HW decode (decoder)", self.hwdecode)
        self.hwdevice = QLineEdit()
        self.hwdevice.setPlaceholderText("-filter_hw_device name, e.g. gpu0")
        perf_form.addRow("HW device", self.hwdevice)
        col.addWidget(perf_box)

        out_box, out_form = _form("Output modes")
        self.twopass = QCheckBox("Two-pass encode")
        self.twopass.setToolTip("Runs the encode twice, error-checked between passes")
        out_form.addRow(self.twopass)
        seg_row = _row()
        self.segment = QCheckBox("Segment")
        self.segment_time = QLineEdit()
        self.segment_time.setPlaceholderText("seconds per part, e.g. 600")
        seg_row.addWidget(self.segment)
        seg_row.addWidget(self.segment_time, stretch=1)
        out_form.addRow(seg_row)
        self.bsf = QLineEdit()
        self.bsf.setPlaceholderText("stream=filter, e.g. v:0=h264_mp4toannexb")
        out_form.addRow("Bitstream filter", self.bsf)
        self.tee = QLineEdit()
        self.tee.setPlaceholderText("[f=mp4]a.mp4|[f=webm]b.webm")
        out_form.addRow("Tee outputs", self.tee)
        col.addWidget(out_box)

        in_box, in_form = _form("Input options")
        self.seek = QLineEdit()
        self.seek.setPlaceholderText("fast input seek (-ss before -i), e.g. 10")
        in_form.addRow("Seek to", self.seek)
        self.loop = QLineEdit()
        self.loop.setPlaceholderText("-loop count (stills/short inputs)")
        in_form.addRow("Loop", self.loop)
        col.addWidget(in_box)
        expert_box, _ = _form("Expert — every AVOption in your build")
        from ffgui.ui.expert import ExpertPanel
        self.expert = ExpertPanel(cap)
        self.expert.setMinimumHeight(220)
        expert_box.layout().addRow(self.expert)
        col.addWidget(expert_box, stretch=1)
        self.widgets = {"hwdecode": self.hwdecode, "hwdevice": self.hwdevice,
                        "twopass": self.twopass, "segment": self.segment,
                        "segment_time": self.segment_time, "bsf": self.bsf,
                        "tee": self.tee, "seek": self.seek, "loop": self.loop}
        self._watch(self.hwdecode.currentTextChanged, "hwdecode", "")
        self._watch(self.hwdevice.textEdited, "hwdevice", "")
        self._watch(self.twopass.toggled, "flag", "two_pass")
        self._watch(self.segment.toggled, "segment", "")
        self._watch(self.segment_time.textEdited, "segment_time", "")
        self._watch(self.bsf.textEdited, "bsf", "")
        self._watch(self.tee.textEdited, "tee", "")
        self._watch(self.seek.textEdited, "input", "-ss")
        self._watch(self.loop.textEdited, "input", "-loop")

    def load(self, out: Output, job: Job) -> None:
        self._set_text(self.hwdevice, job.filter_hw_device or "")
        self.twopass.setChecked(job.two_pass)
        self.segment.setChecked(bool(out.segment_enabled))
        self._set_text(self.segment_time, out.segment_time or "")


        self._set_text(self.bsf, next((f"{k}={v}" for k, v in out.bsf.items()), "")
                       if out.bsf else "")
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
