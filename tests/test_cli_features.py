"""Behavioral and real-FFmpeg end-to-end tests for everyday CLI features."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest
from PySide6.QtCore import QSettings
from PySide6.QtWidgets import QComboBox, QLineEdit
from fftui.ffmpeg.capability_index import CapabilityIndex
from fftui.model import Input, InputStream
from fftui.util.command_builder import build

from ffgui import curation
from ffgui.controller import Controller
from ffgui.doc import QueueDocument
from ffgui.ui.shell import Shell


def _av_probe(path: str) -> Input:
    return Input(
        path=str(path),
        streams=[
            InputStream(
                input_index=0,
                spec="v:0",
                codec_type="video",
                codec_name="h264",
                width=320,
                height=240,
            ),
            InputStream(
                input_index=0,
                spec="a:0",
                codec_type="audio",
                codec_name="aac",
                sample_rate=44100,
            ),
        ],
    )


@pytest.fixture()
def cli_env(qapp, tmp_path, monkeypatch):
    monkeypatch.setenv("FFGUI_CACHE_DIR", str(tmp_path / "cache"))
    settings = QSettings(str(tmp_path / "settings.ini"), QSettings.Format.IniFormat)
    shell = Shell()
    cap = CapabilityIndex.stub()
    doc = QueueDocument(cap, prober=_av_probe)
    controller = Controller(shell, doc, cap, settings)
    controller.add_paths([str(tmp_path / "alpha.mp4"), str(tmp_path / "beta.mp4")])
    shell.queue.selectRow(0)
    return shell, doc, controller, tmp_path


def _edit_line(widget: QLineEdit, text: str) -> None:
    widget.setText(text)
    widget.textEdited.emit(text)


def _select_data(combo: QComboBox, data: str) -> None:
    idx = combo.findData(data)
    assert idx >= 0, f"value {data!r} not found in combo"
    combo.setCurrentIndex(idx)


def _reload_row(shell: Shell, row: int = 0, other: int = 1) -> None:
    shell.queue.selectRow(other)
    shell.queue.selectRow(row)


def _flag_value(argv: list[str], flag: str) -> str:
    assert flag in argv, f"expected {flag!r} in {argv}"
    idx = argv.index(flag)
    assert idx + 1 < len(argv), f"flag {flag!r} missing value in {argv}"
    return argv[idx + 1]


def _argv(doc: QueueDocument, row: int = 0) -> list[str]:
    return build(doc.items[row].job)


# --- ContainerTab ---


def test_container_end_time_round_trip(cli_env):
    shell, doc, c, _ = cli_env
    tab = c.tabs["Container"]

    _edit_line(tab.end, "00:01:30")
    assert doc.items[0].job.outputs[0].end == "00:01:30"
    assert _flag_value(_argv(doc), "-to") == "00:01:30"

    _reload_row(shell)
    assert tab.end.text() == "00:01:30"

    _edit_line(tab.end, "")
    assert "-to" not in _argv(doc)


def test_container_max_size_round_trip(cli_env):
    shell, doc, c, _ = cli_env
    tab = c.tabs["Container"]

    _edit_line(tab.max_size, "25M")
    assert doc.items[0].job.outputs[0].options["fs"] == "25M"
    assert _flag_value(_argv(doc), "-fs") == "25M"

    _reload_row(shell)
    assert tab.max_size.text() == "25M"

    _edit_line(tab.max_size, "")
    assert "-fs" not in _argv(doc)


# --- VideoTab ---


def test_video_drop_and_codec_restore(cli_env):
    shell, doc, c, _ = cli_env
    tab = c.tabs["Video"]

    tab.codec.setCurrentText("libx264")
    assert doc.items[0].job.outputs[0].video_codec == "libx264"

    tab.drop_video.setChecked(True)
    out = doc.items[0].job.outputs[0]
    assert out.video_codec is None
    assert "vn" in out.options
    argv = _argv(doc)
    assert "-vn" in argv
    assert "-c:v" not in argv

    _reload_row(shell)
    assert tab.drop_video.isChecked()
    assert tab.codec.currentText() == ""

    tab.codec.setCurrentText("libx264")
    assert "vn" not in doc.items[0].job.outputs[0].options
    assert doc.items[0].job.outputs[0].video_codec == "libx264"
    argv = _argv(doc)
    assert "-vn" not in argv
    assert _flag_value(argv, "-c:v") == "libx264"
    assert not tab.drop_video.isChecked()


def test_video_resolution_round_trip(cli_env):
    shell, doc, c, _ = cli_env
    tab = c.tabs["Video"]

    _select_data(tab.resolution, "1920:-2")
    assert "scale=1920:-2" in _flag_value(_argv(doc), "-vf")

    _reload_row(shell)
    assert tab.resolution.currentData() == "1920:-2"

    _select_data(tab.resolution, "1280:-2")
    vf = _flag_value(_argv(doc), "-vf")
    assert "scale=1280:-2" in vf
    assert "scale=1920:-2" not in vf

    _select_data(tab.resolution, "")
    assert "-vf" not in _argv(doc)


def test_video_fps_and_aspect_round_trip(cli_env):
    shell, doc, c, _ = cli_env
    tab = c.tabs["Video"]

    tab.fps.setCurrentText("30")
    tab.aspect.setCurrentText("16:9")
    argv = _argv(doc)
    assert _flag_value(argv, "-r") == "30"
    assert _flag_value(argv, "-aspect") == "16:9"

    _reload_row(shell)
    assert tab.fps.currentText() == "30"
    assert tab.aspect.currentText() == "16:9"


@pytest.mark.parametrize("expr", ["transpose=1", "transpose=2", "hflip,vflip", "hflip", "vflip"])
def test_video_rotate_round_trip(cli_env, expr):
    shell, doc, c, _ = cli_env
    tab = c.tabs["Video"]

    _select_data(tab.rotate, expr)
    assert expr in _flag_value(_argv(doc), "-vf")

    _reload_row(shell)
    assert tab.rotate.currentData() == expr

    # Switching rotation replaces the prior transform cleanly.
    replacement = "transpose=1" if expr != "transpose=1" else "hflip"
    _select_data(tab.rotate, replacement)
    vf = _flag_value(_argv(doc), "-vf")
    assert replacement in vf
    assert expr not in vf

    _select_data(tab.rotate, "")
    assert "-vf" not in _argv(doc)


def test_video_crop_round_trip(cli_env):
    shell, doc, c, _ = cli_env
    tab = c.tabs["Video"]

    _edit_line(tab.crop, "640:480:0:0")
    assert "crop=640:480:0:0" in _flag_value(_argv(doc), "-vf")

    _reload_row(shell)
    assert tab.crop.text() == "640:480:0:0"

    _edit_line(tab.crop, "")
    assert "-vf" not in _argv(doc)


def test_video_deinterlace_round_trip(cli_env):
    shell, doc, c, _ = cli_env
    tab = c.tabs["Video"]

    tab.deinterlace.setChecked(True)
    assert "yadif" in _flag_value(_argv(doc), "-vf")

    _reload_row(shell)
    assert tab.deinterlace.isChecked()

    tab.deinterlace.setChecked(False)
    assert "-vf" not in _argv(doc)


def test_video_speed_round_trip_and_validation(cli_env):
    shell, doc, c, _ = cli_env
    tab = c.tabs["Video"]

    _select_data(tab.speed, "1.5")
    argv = _argv(doc)
    assert "setpts=PTS/1.5" in _flag_value(argv, "-vf")
    assert "atempo=1.5" in _flag_value(argv, "-af")

    _reload_row(shell)
    assert tab.speed.currentData() == "1.5"

    # Clearing via Normal (empty) removes both video and audio speed filters.
    _select_data(tab.speed, "")
    argv = _argv(doc)
    assert "-vf" not in argv
    assert "-af" not in argv

    # Re-apply and clear via 1.0.
    _select_data(tab.speed, "2")
    assert "setpts=PTS/2" in _flag_value(_argv(doc), "-vf")
    assert c._apply_edit("speed", "", "1.0", 0) is None
    assert "-vf" not in _argv(doc)
    assert "-af" not in _argv(doc)

    # Reject out-of-range and non-numeric rates.
    for bad in ("0.25", "2.5", "0", "-1", "fast"):
        err = c._apply_edit("speed", "", bad, 0)
        assert err is not None
        assert "-vf" not in _argv(doc)
        assert "-af" not in _argv(doc)


# --- AudioTab ---


def test_audio_drop_and_codec_restore(cli_env):
    shell, doc, c, _ = cli_env
    tab = c.tabs["Audio"]

    tab.codec.setCurrentText("aac")
    assert doc.items[0].job.outputs[0].audio_codec == "aac"

    tab.drop_audio.setChecked(True)
    out = doc.items[0].job.outputs[0]
    assert out.audio_codec is None
    assert "an" in out.options
    argv = _argv(doc)
    assert "-an" in argv
    assert "-c:a" not in argv

    _reload_row(shell)
    assert tab.drop_audio.isChecked()
    assert tab.codec.currentText() == ""

    tab.codec.setCurrentText("aac")
    assert "an" not in doc.items[0].job.outputs[0].options
    assert doc.items[0].job.outputs[0].audio_codec == "aac"
    argv = _argv(doc)
    assert "-an" not in argv
    assert _flag_value(argv, "-c:a") == "aac"
    assert not tab.drop_audio.isChecked()


def test_audio_quality_round_trip(cli_env):
    shell, doc, c, _ = cli_env
    tab = c.tabs["Audio"]

    _edit_line(tab.quality, "2")
    assert doc.items[0].job.outputs[0].audio_options["q:a"] == "2"
    assert _flag_value(_argv(doc), "-q:a") == "2"

    _reload_row(shell)
    assert tab.quality.text() == "2"

    _edit_line(tab.quality, "")
    assert "-q:a" not in _argv(doc)


# --- MetadataTab ---


def test_metadata_tags_round_trip(cli_env):
    shell, doc, c, _ = cli_env
    tab = c.tabs["Metadata"]
    tags = {
        "artist": "Ada Lovelace",
        "album": "Analytical Engine",
        "date": "2026",
        "genre": "Electronic",
        "track": "3/10",
    }
    for key, value in tags.items():
        _edit_line(getattr(tab, key), value)

    argv = _argv(doc)
    meta_pairs = [argv[i + 1] for i, tok in enumerate(argv) if tok == "-metadata"]
    for key, value in tags.items():
        assert f"{key}={value}" in meta_pairs

    _reload_row(shell)
    for key, value in tags.items():
        assert getattr(tab, key).text() == value

    _edit_line(tab.genre, "")
    argv = _argv(doc)
    meta_pairs = [argv[i + 1] for i, tok in enumerate(argv) if tok == "-metadata"]
    assert not any(p.startswith("genre=") for p in meta_pairs)


def test_metadata_stream_disposition_and_missing_spec_error(cli_env):
    shell, doc, c, _ = cli_env
    tab = c.tabs["Metadata"]
    messages: list[str] = []
    c.status.connect(messages.append)

    # Empty stream_spec rejects disposition with a clear error.
    _select_data(tab.stream_disposition, "forced")
    assert messages and "stream spec" in messages[-1].lower()
    assert not doc.items[0].job.outputs[0].dispositions
    assert "-disposition:s:0" not in _argv(doc)

    # Setting stream_spec then choosing disposition emits -disposition:s:0 forced.
    tab.stream_spec.setText("s:0")
    _select_data(tab.stream_disposition, "")
    _select_data(tab.stream_disposition, "forced")
    assert doc.items[0].job.outputs[0].dispositions == {"s:0": "forced"}
    assert _flag_value(_argv(doc), "-disposition:s:0") == "forced"

    # Clearing disposition removes it.
    _select_data(tab.stream_disposition, "")
    assert "-disposition:s:0" not in _argv(doc)


# --- AdvancedTab ---


def test_advanced_hwaccel_before_input_and_reload(cli_env):
    shell, doc, c, _ = cli_env
    tab = c.tabs["Advanced"]

    tab.hwaccel.setCurrentText("auto")
    argv = _argv(doc)
    assert _flag_value(argv, "-hwaccel") == "auto"
    assert argv.index("-hwaccel") < argv.index("-i")

    _reload_row(shell)
    assert tab.hwaccel.currentText() == "auto"


def test_advanced_itsoffset_before_input_and_rejects_garbage(cli_env):
    shell, doc, c, _ = cli_env
    tab = c.tabs["Advanced"]
    messages: list[str] = []
    c.status.connect(messages.append)

    _edit_line(tab.itsoffset, "-0.25")
    argv = _argv(doc)
    assert _flag_value(argv, "-itsoffset") == "-0.25"
    assert argv.index("-itsoffset") < argv.index("-i")

    _reload_row(shell)
    assert tab.itsoffset.text() == "-0.25"

    # Reject non-time garbage without overwriting the valid offset.
    _edit_line(tab.itsoffset, "not-a-time")
    assert messages and "invalid time" in messages[-1].lower()
    assert _flag_value(_argv(doc), "-itsoffset") == "-0.25"


def test_advanced_loglevel_round_trip(cli_env):
    shell, doc, c, _ = cli_env
    tab = c.tabs["Advanced"]

    tab.loglevel.setCurrentText("warning")
    assert doc.items[0].job.loglevel == "warning"
    assert _flag_value(_argv(doc), "-loglevel") == "warning"

    _reload_row(shell)
    assert tab.loglevel.currentText() == "warning"


# --- Curation ---


def test_curation_basics_yaml_loads_valid_entries():
    entries = curation.load()
    assert len(entries) >= 35
    for key, meta in entries.items():
        assert isinstance(meta.get("label"), str) and meta["label"].strip(), f"bad label on {key}"
        assert isinstance(meta.get("group"), str) and meta["group"].strip(), f"bad group on {key}"


# --- Real-FFmpeg end-to-end integration tests ---


def _make_synthetic_clip(dest: Path) -> Path:
    cmd = [
        "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
        "-f", "lavfi", "-i", "testsrc=duration=1:size=320x240:rate=10",
        "-f", "lavfi", "-i", "sine=frequency=440:duration=1",
        str(dest),
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True, check=False)
    assert proc.returncode == 0, f"failed to generate synthetic clip: {proc.stderr}"
    return dest


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg binary not available")
def test_ffmpeg_e2e_video_transform_pipeline(qapp, tmp_path, monkeypatch):
    monkeypatch.setenv("FFGUI_CACHE_DIR", str(tmp_path / "cache"))
    src = _make_synthetic_clip(tmp_path / "sample.mp4")
    out_file = tmp_path / "transformed.mp4"

    settings = QSettings(str(tmp_path / "settings.ini"), QSettings.Format.IniFormat)
    shell = Shell()
    cap = CapabilityIndex.stub()
    doc = QueueDocument(cap, prober=_av_probe)
    c = Controller(shell, doc, cap, settings)
    c.add_paths([str(src)])
    shell.queue.selectRow(0)

    container = c.tabs["Container"]
    video = c.tabs["Video"]
    metadata = c.tabs["Metadata"]
    advanced = c.tabs["Advanced"]

    _edit_line(container.name, str(out_file))
    _edit_line(container.end, "0.5")
    _select_data(video.resolution, "640:-2")
    _select_data(video.rotate, "transpose=1")
    video.deinterlace.setChecked(True)
    _select_data(video.speed, "1.5")
    _edit_line(metadata.artist, "Test Artist")
    _edit_line(metadata.album, "Test Album")
    advanced.loglevel.setCurrentText("error")

    argv = build(doc.items[0].job)
    vf = _flag_value(argv, "-vf")
    assert all(tok in vf for tok in ("scale=640:-2", "transpose=1", "yadif", "setpts=PTS/1.5"))
    assert "atempo=1.5" in _flag_value(argv, "-af")
    assert _flag_value(argv, "-to") == "0.5"
    assert "artist=Test Artist" in argv

    proc = subprocess.run(argv, capture_output=True, text=True, check=False)
    assert proc.returncode == 0, f"ffmpeg failed ({argv}):\n{proc.stderr}"
    assert out_file.is_file() and out_file.stat().st_size > 0


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg binary not available")
def test_ffmpeg_e2e_audio_only_extraction(qapp, tmp_path, monkeypatch):
    monkeypatch.setenv("FFGUI_CACHE_DIR", str(tmp_path / "cache"))
    src = _make_synthetic_clip(tmp_path / "sample.mp4")
    out_file = tmp_path / "extracted.mp3"

    settings = QSettings(str(tmp_path / "settings.ini"), QSettings.Format.IniFormat)
    shell = Shell()
    cap = CapabilityIndex.stub()
    doc = QueueDocument(cap, prober=_av_probe)
    c = Controller(shell, doc, cap, settings)
    c.add_paths([str(src)])
    shell.queue.selectRow(0)

    container = c.tabs["Container"]
    video = c.tabs["Video"]
    audio = c.tabs["Audio"]
    advanced = c.tabs["Advanced"]

    _edit_line(container.name, str(out_file))
    video.drop_video.setChecked(True)
    audio.codec.setCurrentText("libmp3lame")
    _edit_line(audio.quality, "2")
    advanced.loglevel.setCurrentText("error")

    argv = build(doc.items[0].job)
    assert "-vn" in argv
    assert _flag_value(argv, "-q:a") == "2"

    proc = subprocess.run(argv, capture_output=True, text=True, check=False)
    assert proc.returncode == 0, f"ffmpeg failed ({argv}):\n{proc.stderr}"
    assert out_file.is_file() and out_file.stat().st_size > 0
