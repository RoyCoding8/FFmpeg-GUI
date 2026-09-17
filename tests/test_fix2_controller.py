"""Gate: presets don't leak output paths; volume replaces only its own filter;
Remove-selected shortcut is scoped to the queue."""
from PySide6.QtCore import Qt


def test_apply_preset_keeps_target_output_path(wired):
    """The output file identifies the target row: a preset must not leak its
    own saved output path onto the row it is applied to."""
    shell, doc, c, tmp = wired
    from ffgui.store import delete_preset, save_preset
    c.add_paths([str(tmp / "alpha.mp4"), str(tmp / "beta.mp4")])
    save_preset("px", doc.items[0].job, doc.items[0].meta)
    shell.queue.selectRow(1)
    c.apply_preset("px")
    delete_preset("px")
    assert doc.items[1].job.outputs[0].path == "beta_out.mp4"
    assert doc.items[0].job.outputs[0].path != doc.items[1].job.outputs[0].path


def test_volume_keeps_unrelated_audio_filters(wired):
    shell, doc, c, tmp = wired
    c.add_paths([str(tmp / "alpha.mp4")])
    doc.items[0].job.audio_filters.filters.append("volumedetect")
    assert c._apply_advanced("volume", "", "-6", 0) is None
    assert doc.items[0].job.audio_filters.filters == ["volumedetect", "volume=-6dB"]


def test_loudnorm_keeps_unrelated_audio_filters(wired):
    shell, doc, c, tmp = wired
    c.add_paths([str(tmp / "alpha.mp4")])
    doc.items[0].job.audio_filters.filters.append("volumedetect")
    assert c._apply_advanced("loudnorm", "", "on", 0) is None
    assert doc.items[0].job.audio_filters.filters == [
        "volumedetect", "loudnorm=I=-16:TP=-1.5:LRA=11"]


def test_remove_shortcut_scoped_to_queue(wired):
    shell, doc, c, row = wired
    action = next(a for a in shell.queue.actions() if a.text() == "Remove selected")
    assert action.parent() is shell.queue
    assert action.shortcutContext() == Qt.ShortcutContext.WidgetWithChildrenShortcut
