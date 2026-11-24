"""Tab widget tests: caret preservation, subtitle burn-in restore, codec combo
filtering, and ContainerTab overwrite/noconfirm inversion."""
from fftui.ffmpeg.capability_index import CapabilityIndex
from fftui.model import Job, Output

from ffgui.ui.tabs import AudioTab, ContainerTab, SubtitlesTab, VideoTab


def _container_tab(qapp):
    tab = ContainerTab()
    seen = []
    tab.edit.connect(lambda kind, key, value: seen.append((kind, key, value)))
    return tab, seen


def test_populate_preserves_caret(qapp):
    tab = ContainerTab()
    out, job = Output(path="holiday video.mp4"), Job()
    tab.populate(out, job)
    tab.name.setCursorPosition(8)
    tab.populate(out, job)
    assert tab.name.cursorPosition() == 8


def test_burn_restores_from_subtitle_burn_in(qapp):
    tab = SubtitlesTab(CapabilityIndex.stub())
    tab.populate(Output(path="o.mp4", subtitle_burn_in="subs.ass"), Job())
    assert tab.burn.text() == "subs.ass"


def test_burn_falls_back_to_legacy_filter(qapp):
    job = Job()
    job.video_filters.filters.append("subtitles=legacy.srt")
    tab = SubtitlesTab(CapabilityIndex.stub())
    tab.populate(Output(path="o.mp4"), job)
    assert tab.burn.text() == "legacy.srt"


def test_audio_codec_combo_excludes_video_encoders(qapp):
    combo = AudioTab(CapabilityIndex.stub()).codec
    names = [combo.itemText(i) for i in range(combo.count())]
    assert "libx264" not in names


def test_video_codec_combo_excludes_audio_encoders(qapp):
    tab = VideoTab(CapabilityIndex.stub())
    names = [tab.codec.itemText(i) for i in range(tab.codec.count())]
    assert "aac" not in names and "libmp3lame" not in names


def test_checked_means_overwrite_y(qapp):
    tab, seen = _container_tab(qapp)
    tab.overwrite.setChecked(True)
    assert ("flag", "noconfirm", "False") in seen


def test_unchecked_means_ask_n(qapp):
    tab, seen = _container_tab(qapp)
    tab.overwrite.setChecked(True)
    seen.clear()
    tab.overwrite.setChecked(False)
    assert ("flag", "noconfirm", "True") in seen


def test_load_restores_overwrite_from_noconfirm(qapp):
    tab, _ = _container_tab(qapp)
    tab.load(Output(path="o.mp4"), Job())
    assert tab.overwrite.isChecked()
