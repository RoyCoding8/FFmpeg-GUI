"""Controller tests offscreen: table refresh, preview, export, presets, locator."""

import os
import sys
from unittest.mock import patch

import pytest
from fftui.ffmpeg.capability_index import CapabilityIndex
from fftui.model import InputStream, Input

from ffgui.controller import Controller, aim_ffmpeg, locate_ffmpeg
from ffgui.doc import QueueDocument


@pytest.fixture()
def env(qapp, tmp_path, monkeypatch):

    for stem in ("alpha", "beta"):
        p = tmp_path / f"{stem}.mp4"
        p.write_bytes(b"0")
        monkeypatch.setattr(
            "ffgui.doc.probe",
            lambda path, _p=p: Input(path=str(_p), streams=[
                InputStream(input_index=0, spec="v:0", codec_type="video",
                            codec_name="h264", width=64, height=48),
                InputStream(input_index=0, spec="a:0", codec_type="audio",
                            codec_name="aac", sample_rate=48000)]),
            raising=False,
        )


    import ffgui.doc as doc_mod
    real = doc_mod.probe
    doc_mod.probe = lambda path: Input(path=path, streams=[
        InputStream(input_index=0, spec="v:0", codec_type="video",
                    codec_name="h264", width=64, height=48),
        InputStream(input_index=0, spec="a:0", codec_type="audio",
                    codec_name="aac", sample_rate=48000)])
    yield doc_mod
    doc_mod.probe = real


@pytest.fixture()
def wired(qapp, env, tmp_path, monkeypatch):
    from PySide6.QtCore import QSettings

    from ffgui.ui.shell import Shell

    monkeypatch.setenv("FFGUI_CACHE_DIR", str(tmp_path / "cache"))
    settings = QSettings(str(tmp_path / "settings.ini"), QSettings.Format.IniFormat)
    shell = Shell()
    doc = QueueDocument(CapabilityIndex.stub(), prober=env.probe)
    controller = Controller(shell, doc, CapabilityIndex.stub(), settings)
    return shell, doc, controller, tmp_path


def test_add_and_refresh(wired):
    shell, doc, c, tmp_path = wired
    c.add_paths([str(tmp_path / "alpha.mp4"), str(tmp_path / "beta.mp4")])
    assert shell.queue.rowCount() == 2
    assert shell.queue_stack.currentIndex() == 1
    assert shell.queue.item(0, 1).text() == "alpha"


def test_preview_shows_argv(wired):
    shell, doc, c, tmp_path = wired
    c.add_paths([str(tmp_path / "alpha.mp4")])
    shell.queue.selectRow(0)
    text = shell.preview.toPlainText()
    assert "alpha_out.mp4" in text and "ffmpeg" in text


def test_tab_edit_routes_to_doc(wired):
    shell, doc, c, tmp_path = wired
    c.add_paths([str(tmp_path / "alpha.mp4")])
    shell.queue.selectRow(0)
    c.tabs["Container"].start.setText("5")
    c._on_tab_edit("field", "start", "5")
    assert doc.items[0].job.outputs[0].start == "5"


def test_export_writes_script(wired):
    shell, doc, c, tmp_path = wired
    c.add_paths([str(tmp_path / "alpha.mp4")])
    out = tmp_path / "run.bat"
    written = c.export_to(str(out), "bat")
    assert written == str(out)
    text = out.read_bytes().decode("utf-8")
    assert text.startswith("@echo off") and "alpha_out.mp4" in text


def test_export_queue_n_scripts(wired):
    shell, doc, c, tmp_path = wired
    c.add_paths([str(tmp_path / "alpha.mp4"), str(tmp_path / "beta.mp4")])
    written = c.export_to(str(tmp_path / "q.bat"), "sh", one_file=False)
    assert written.endswith("_beta_out.sh") or written.endswith("_alpha_out.sh")
    scripts = list(tmp_path.glob("q*_*.sh"))
    assert len(scripts) == 2
    for s in scripts:
        assert s.read_text(encoding="utf-8").startswith("#!/bin/sh")


def test_presets_round_trip(wired):
    shell, doc, c, tmp_path = wired
    c.add_paths([str(tmp_path / "alpha.mp4")])
    shell.queue.selectRow(0)
    from ffgui.store import delete_preset, load_presets, save_preset
    doc.set_option(0, "video", "crf", "22")
    save_preset("mine", doc.items[0].job, doc.items[0].meta)
    assert "mine" in load_presets()
    c.refresh_presets()
    assert shell.preset_list.count() == 1
    doc.set_option(0, "video", "crf", "18")
    c.apply_preset("mine")
    assert doc.items[0].job.outputs[0].video_options["crf"] == "22"
    delete_preset("mine")
    c.refresh_presets()
    assert shell.preset_list.count() == 0


def test_move_selected(wired):
    shell, doc, c, tmp_path = wired
    c.add_paths([str(tmp_path / "alpha.mp4"), str(tmp_path / "beta.mp4")])
    shell.queue.selectRow(0)
    c.move_selected(1)
    assert doc.items[1].meta["name"] == "alpha"
    assert shell.queue.item(1, 1).text() == "alpha"


def test_remove_selected(wired):
    shell, doc, c, tmp_path = wired
    c.add_paths([str(tmp_path / "alpha.mp4"), str(tmp_path / "beta.mp4")])
    shell.queue.selectAll()
    c.remove_selected()
    assert shell.queue.rowCount() == 0
    assert shell.queue_stack.currentIndex() == 0


def test_locator_chain(qapp, tmp_path, monkeypatch):
    from PySide6.QtCore import QSettings


    monkeypatch.setenv("FFTUI_FFMPEG_DIR", "")
    fake = tmp_path / "ffmpeg.exe"
    fake.write_bytes(b"")
    monkeypatch.delenv("FFMPEG_PATH", raising=False)
    settings = QSettings(str(tmp_path / "s.ini"), QSettings.Format.IniFormat)

    monkeypatch.setattr("ffgui.controller.shutil.which", lambda name: None)
    monkeypatch.setattr("ffgui.controller.sys", type("S", (), {"executable": str(fake)}))
    assert locate_ffmpeg(settings) == (str(fake), True)

    monkeypatch.setattr("ffgui.controller.shutil.which", lambda name: "C:\\PATH\\ffmpeg.exe")
    assert locate_ffmpeg(settings) == (None, True)

    settings.setValue("ffmpeg_path", str(fake))
    monkeypatch.setattr("ffgui.controller.shutil.which", lambda name: None)
    fake_sys = type("S", (), {"executable": str(tmp_path / "python.exe")})
    monkeypatch.setattr("ffgui.controller.sys", fake_sys)
    assert locate_ffmpeg(settings) == (str(fake), True)

    settings.setValue("ffmpeg_path", "")
    venv_sys = type("S", (), {"executable": str(tmp_path / "venv" / "python.exe")})
    monkeypatch.setattr("ffgui.controller.sys", venv_sys)
    assert locate_ffmpeg(settings) == (None, False)

    aim_ffmpeg(str(fake))
    assert os.environ["FFTUI_FFMPEG_DIR"] == str(tmp_path)


def _error_row():
    from fftui.model import Job

    from ffgui.doc import QueueItem
    return QueueItem(Job(inputs=[], outputs=[]),
                     {"name": "bad", "enabled": True, "notes": "", "source_hash": ""},
                     error="unreadable")


def test_apply_preset_copies_per_row(wired):
    shell, doc, c, tmp_path = wired
    from ffgui.store import delete_preset, load_presets, save_preset
    c.add_paths([str(tmp_path / "alpha.mp4"), str(tmp_path / "beta.mp4")])
    doc.set_option(0, "video", "crf", "22")
    save_preset("mine", doc.items[0].job, doc.items[0].meta)
    before = load_presets()["mine"][0].to_dict()
    shell.queue.selectAll()
    c.apply_preset("mine")
    j0, j1 = doc.items[0].job, doc.items[1].job
    assert j0 is not j1
    assert j0.inputs[0].path.endswith("alpha.mp4")
    assert j1.inputs[0].path.endswith("beta.mp4")
    assert load_presets()["mine"][0].to_dict() == before
    delete_preset("mine")


def test_export_skips_rows_without_io(wired):
    shell, doc, c, tmp_path = wired
    c.add_paths([str(tmp_path / "alpha.mp4")])
    doc.items.append(_error_row())
    out = tmp_path / "run.bat"
    c.export_to(str(out), "bat")
    text = out.read_bytes().decode("utf-8")
    assert "alpha_out" in text and "bad" not in text


def test_stream_spec_edit_commits_language(wired):
    shell, doc, c, tmp_path = wired
    c.add_paths([str(tmp_path / "alpha.mp4")])
    shell.queue.selectRow(0)
    meta = c.tabs["Metadata"]
    meta.stream_lang.setText("fre")
    c._on_tab_edit("streamtag", "language", "fre")
    assert doc.items[0].job.outputs[0].stream_metadata == {}
    meta.stream_spec.setText("v:0")
    c._on_tab_edit("streamtag", "spec", "v:0")
    assert doc.items[0].job.outputs[0].stream_metadata == {"v:0|language": "fre"}


def test_chapters_empty_start_rejected(wired):
    shell, doc, c, tmp_path = wired
    c.add_paths([str(tmp_path / "alpha.mp4")])
    shell.queue.selectRow(0)
    c._on_tab_edit("chapters", "", "\x1f\x1f")
    assert doc.items[0].job.outputs[0].chapters == []
    c._on_tab_edit("chapters", "", "0\x1fIntro\x1feng")
    assert [ch.title for ch in doc.items[0].job.outputs[0].chapters] == ["Intro"]


def test_bsf_replace_semantics(wired):
    shell, doc, c, tmp_path = wired
    c.add_paths([str(tmp_path / "alpha.mp4")])
    shell.queue.selectRow(0)
    c._apply_advanced("bsf", "", "v:0=h264_mp4toannexb", 0)
    out = doc.items[0].job.outputs[0]
    assert out.bsf == {"v:0": "h264_mp4toannexb"}
    c._apply_advanced("bsf", "", "a:0=aac_adtstoasc", 0)
    assert out.bsf == {"a:0": "aac_adtstoasc"}
    c._apply_expert("bsf", "v:0=h264_mp4toannexb", None, "")
    assert out.bsf == {"v:0": "h264_mp4toannexb"}


def test_loudnorm_toggle_off_removes_filter(wired):
    """Checkbox toggles arrive as "True"/"False" strings: truthiness alone
    ("False" is truthy) made unchecking a silent no-op."""
    shell, doc, c, tmp_path = wired
    c.add_paths([str(tmp_path / "alpha.mp4")])
    c._apply_advanced("loudnorm", "", "True", 0)
    assert doc.items[0].job.audio_filters.filters == ["loudnorm=I=-16:TP=-1.5:LRA=11"]
    c._apply_advanced("loudnorm", "", "False", 0)
    assert doc.items[0].job.audio_filters.filters == []


def test_burn_and_volume_free_text_unaffected_by_toggle_fix(wired):
    """Burn carries a subtitle path, volume a dB value: non-empty means set,
    empty means clear — the loudnorm "True"/"False" coercion must not leak."""
    shell, doc, c, tmp_path = wired
    c.add_paths([str(tmp_path / "alpha.mp4")])
    c._apply_advanced("burn", "", "subs.srt", 0)
    out = doc.items[0].job.outputs[0]
    assert out.subtitle_burn_in == "subs.srt"
    assert not [f for f in out.video_filters.filters if f.startswith("subtitles")]
    c._apply_advanced("burn", "", "", 0)
    assert out.subtitle_burn_in in ("", None)
    c._apply_advanced("volume", "", "-6", 0)
    assert doc.items[0].job.audio_filters.filters == ["volume=-6dB"]
    c._apply_advanced("volume", "", "", 0)
    assert doc.items[0].job.audio_filters.filters == []


def test_selection_clears_after_remove(wired):
    shell, doc, c, tmp_path = wired
    c.add_paths([str(tmp_path / "alpha.mp4"), str(tmp_path / "beta.mp4")])
    shell.queue.selectAll()
    c.remove_selected()
    assert shell.queue.rowCount() == 0
    assert c.selected_rows() == []


def test_launch_script_platform_dispatch(monkeypatch):
    from ffgui.controller import launch_script, terminal_argv
    monkeypatch.setattr("ffgui.controller.sys", type("S", (), {"platform": "win32"}))
    opened = []
    monkeypatch.setattr("ffgui.controller.os.startfile",
                        lambda p: opened.append(p), raising=False)
    launch_script(r"C:\tmp\run.bat")
    assert opened == [r"C:\tmp\run.bat"]

    monkeypatch.setattr("ffgui.controller.sys", type("S", (), {"platform": "linux"}))
    monkeypatch.setattr("ffgui.controller.shutil.which",
                        lambda n: "/usr/bin/xterm" if n == "xterm" else None)
    spawned = []
    monkeypatch.setattr("ffgui.controller.subprocess.Popen",
                        lambda argv, **kw: spawned.append(argv))
    launch_script("/tmp/run.sh")
    assert spawned == [["xterm", "-e", "sh", "--", "/tmp/run.sh"]]

    monkeypatch.setattr("ffgui.controller.shutil.which", lambda n: None)
    with pytest.raises(RuntimeError):
        launch_script("/tmp/run.sh")

    monkeypatch.setattr("ffgui.controller.sys", type("S", (), {"platform": "darwin"}))
    argv = terminal_argv("/tmp/my run.sh")
    assert argv[0] == "osascript" and argv[-1] == "/tmp/my run.sh"
    assert "quoted form" in argv[2]


def test_launch_script_refuses_bat_off_windows(monkeypatch):
    """run_terminal picks the format from the save-dialog suffix while launch
    dispatched on platform: a .bat saved on posix was fed to `sh --`, which
    parses cmd.exe syntax line by line. Refuse loudly instead."""
    from ffgui.controller import launch_script
    monkeypatch.setattr("ffgui.controller.sys", type("S", (), {"platform": "linux"}))
    with pytest.raises(RuntimeError, match="\\.bat"):
        launch_script("/tmp/run.bat")


def test_expert_commit_refreshes_preview(wired):
    """Expert commits drive the same queue/preview refresh as tab edits."""
    from types import SimpleNamespace

    shell, doc, c, tmp_path = wired
    c.add_paths([str(tmp_path / "alpha.mp4")])
    shell.queue.selectRow(0)
    c.tabs["Advanced"].expert.optionCommitted.emit(
        "muxer", "anything", SimpleNamespace(name="movflags"), "faststart")
    assert doc.items[0].job.outputs[0].options["movflags"] == "faststart"
    assert "faststart" in shell.preview.toPlainText()


def test_expert_commit_error_surfaces_status(wired):
    """Expert validation failures must go loud, not vanish in the signal."""
    from types import SimpleNamespace

    shell, doc, c, tmp_path = wired
    c.add_paths([str(tmp_path / "alpha.mp4")])
    shell.queue.selectRow(0)
    messages = []
    c.status.connect(messages.append)
    c.tabs["Advanced"].expert.optionCommitted.emit(
        "muxer", "anything", SimpleNamespace(name='bad"opt'), "v")
    assert len(messages) == 1 and "bad" in messages[0]


def test_metadata_entry_boxes_clear_on_row_change(wired):
    """Stream spec/lang are entry boxes, not stored state: switching rows
    must not carry the previous row's text into the next commit."""
    shell, doc, c, tmp_path = wired
    c.add_paths([str(tmp_path / "alpha.mp4"), str(tmp_path / "beta.mp4")])
    shell.queue.selectRow(0)
    meta = c.tabs["Metadata"]
    meta.stream_spec.setText("v:0")
    meta.stream_lang.setText("eng")
    shell.queue.selectRow(1)
    assert meta.stream_spec.text() == "" and meta.stream_lang.text() == ""


def test_apply_preset_keeps_target_concat_parts(wired):
    """Concat part-files identify the target row's input: a preset must not
    leak its own saved parts onto the row it is applied to."""
    shell, doc, c, tmp_path = wired
    from ffgui.store import delete_preset, save_preset
    c.add_paths([str(tmp_path / "alpha.mp4"), str(tmp_path / "beta.mp4")])
    doc.items[0].job.inputs[0].concat_paths = ["part1.mp4"]
    doc.items[1].job.inputs[0].concat_paths = ["mine-only.mp4"]
    save_preset("px", doc.items[0].job, doc.items[0].meta)
    shell.queue.selectRow(1)
    c.apply_preset("px")
    assert doc.items[1].job.inputs[0].concat_paths == ["mine-only.mp4"]
    delete_preset("px")


def test_apply_preset_converts_parked_row(wired):
    """Applying a preset replaces the job: a parked UnparsedRow must heal,
    or save() writes the stale bytes back over the preset."""
    shell, doc, c, tmp_path = wired
    from ffgui.store import UnparsedRow, delete_preset, save_preset
    c.add_paths([str(tmp_path / "alpha.mp4")])
    save_preset("px", doc.items[0].job, doc.items[0].meta)
    doc.items[0].unparsed = UnparsedRow({"outputs": 7})
    shell.queue.selectRow(0)
    c.apply_preset("px")
    assert doc.items[0].unparsed is None
    delete_preset("px")


def test_duplicate_selected_selects_copies(wired):
    """Duplicating moves focus to the new copies, like move reselects."""
    shell, doc, c, tmp_path = wired
    c.add_paths([str(tmp_path / "alpha.mp4"), str(tmp_path / "beta.mp4")])
    shell.queue.selectAll()
    c.duplicate_selected()
    rows = c.selected_rows()
    assert rows == [1, 3]
    assert all(doc.items[r].meta["name"].endswith(" copy") for r in rows)


def test_move_selected_keeps_multi_selection(wired):
    """Move reselects every moved row: selectRow replaces, so a loop over
    the range would leave only the last row selected."""
    shell, doc, c, tmp_path = wired
    (tmp_path / "gamma.mp4").write_bytes(b"0")
    c.add_paths([str(tmp_path / f"{s}.mp4") for s in ("alpha", "beta", "gamma")])
    shell.queue.selectAll()
    c.move_selected(-1)
    assert [it.meta["name"] for it in doc.items] == ["alpha", "beta", "gamma"]
    assert c.selected_rows() == [0, 1, 2]


def test_stream_info_selection_guard_warns(wired):
    """Stream info needs exactly one row: anything else must say so."""
    shell, doc, c, tmp_path = wired
    c.add_paths([str(tmp_path / "alpha.mp4"), str(tmp_path / "beta.mp4")])
    shell.queue.selectAll()
    messages = []
    c.status.connect(messages.append)
    c.stream_info_dialog()
    assert messages == ["select exactly one row for stream info"]


def test_flag_words_share_store_parser(wired):
    """'on'/'yes' count as on and 'off'/'0' as off through the shared parser
    on the _apply_flag path, like the queue-meta parser."""
    shell, doc, c, tmp_path = wired
    c.add_paths([str(tmp_path / "alpha.mp4")])
    shell.queue.selectRow(0)
    for word in ("on", "yes"):
        c._apply_flag(0, "two_pass", word)
        assert doc.items[0].job.two_pass is True
    for word in ("off", "0"):
        c._apply_flag(0, "two_pass", word)
        assert doc.items[0].job.two_pass is False
    c._on_tab_edit("flag", "two_pass", "yes")
    assert doc.items[0].job.two_pass is True


def test_run_terminal_picks_native_target(wired, monkeypatch):
    shell, doc, c, tmp_path = wired
    c.add_paths([str(tmp_path / "alpha.mp4")])
    monkeypatch.setattr("ffgui.controller.os.name", "posix", raising=False)
    monkeypatch.setattr("ffgui.controller.launch_script", lambda p: None)
    target = tmp_path / "job.sh"
    with patch("ffgui.controller.QFileDialog.getSaveFileName",
               return_value=(str(target), "")):
        c.run_terminal()
    assert target.is_file()
    assert target.read_bytes().decode("utf-8").startswith("#!/bin/sh")


def test_export_n_scripts_disambiguates_colliding_stems(wired):
    from fftui.model import Input, Job, Output

    from ffgui.doc import QueueItem
    shell, doc, c, tmp_path = wired
    for inp, name in (("/v/same.mp4", "same"), ("/w/other.mp4", "other"),
                      ("/x/third.mp4", "third")):
        doc.items.append(QueueItem(
            Job(inputs=[Input(path=inp)], outputs=[Output(path="/o/dup_out.mp4")]),
            {"name": name, "enabled": True, "notes": "", "source_hash": ""}))
    written = c.export_to(str(tmp_path / "q.bat"), "sh", one_file=False)
    scripts = sorted(tmp_path.glob("q_dup_out*.sh"))
    assert [s.name for s in scripts] == ["q_dup_out.sh", "q_dup_out_2.sh",
                                         "q_dup_out_3.sh"]
    assert written == str(scripts[-1])
    bodies = [s.read_text(encoding="utf-8") for s in scripts]
    for inp in ("/v/same.mp4", "/w/other.mp4", "/x/third.mp4"):
        assert sum(inp in b for b in bodies) == 1


def test_export_and_run_empty_queue_warn_without_dialog(wired, monkeypatch):
    shell, doc, c, tmp_path = wired
    messages = []
    c.status.connect(messages.append)
    with patch("ffgui.controller.QFileDialog.getSaveFileName",
               side_effect=AssertionError("no dialog expected")):
        c.export_dialog()
        c.run_terminal()
    assert messages == ["nothing to export — add media first",
                        "nothing to run — add media first"]
    assert list(tmp_path.glob("*.bat")) == [] and list(tmp_path.glob("*.sh")) == []


def test_run_terminal_uppercase_bat_suffix(wired, monkeypatch):
    shell, doc, c, tmp_path = wired
    c.add_paths([str(tmp_path / "alpha.mp4")])
    monkeypatch.setattr("ffgui.controller.os.name", "posix", raising=False)
    launched = []
    monkeypatch.setattr("ffgui.controller.launch_script", launched.append)
    target = tmp_path / "RUN.BAT"
    with patch("ffgui.controller.QFileDialog.getSaveFileName",
               return_value=(str(target), "")):
        c.run_terminal()
    assert target.is_file()
    assert target.read_bytes().decode("utf-8").startswith("@echo off")
    assert launched == [str(target)]


def test_export_to_suffix_less_path(wired):
    shell, doc, c, tmp_path = wired
    c.add_paths([str(tmp_path / "alpha.mp4")])
    out = tmp_path / "nosuffix"
    assert c.export_to(str(out), "bat") == str(out)
    assert out.is_file()


def test_export_dialog_surfaces_build_error_as_status(wired, monkeypatch):
    from fftui.model import Input, Job, Output

    from ffgui.doc import QueueItem
    shell, doc, c, tmp_path = wired
    doc.items.append(QueueItem(
        Job(inputs=[Input(path='my"quote.mp4')], outputs=[Output(path="o.mp4")]),
        {"name": "q", "enabled": True, "notes": "", "source_hash": ""}))
    messages = []
    c.status.connect(messages.append)
    target = tmp_path / "run.bat"
    with patch("ffgui.controller.QFileDialog.getSaveFileName",
               return_value=(str(target), "")):
        c.export_dialog()
    assert not target.is_file()
    assert len(messages) == 1 and messages[0].startswith("export failed — ")


def test_run_terminal_export_failure_skips_launch(wired, monkeypatch):
    from fftui.model import Input, Job, Output

    from ffgui.doc import QueueItem
    shell, doc, c, tmp_path = wired
    doc.items.append(QueueItem(
        Job(inputs=[Input(path='my"quote.mp4')], outputs=[Output(path="o.mp4")]),
        {"name": "q", "enabled": True, "notes": "", "source_hash": ""}))
    messages = []
    c.status.connect(messages.append)
    launched = []
    monkeypatch.setattr("ffgui.controller.launch_script", launched.append)
    target = tmp_path / "run.bat"
    with patch("ffgui.controller.QFileDialog.getSaveFileName",
               return_value=(str(target), "")):
        c.run_terminal()
    assert launched == [] and not target.is_file()
    assert len(messages) == 1 and messages[0].startswith("export failed — ")


def test_run_terminal_launch_oserror_surfaced(wired, monkeypatch):
    shell, doc, c, tmp_path = wired
    c.add_paths([str(tmp_path / "alpha.mp4")])
    messages = []
    c.status.connect(messages.append)

    if sys.platform == "win32":
        # Windows launches via os.startfile, never via terminal_argv.
        def _boom(_path):
            raise OSError("simulated launch failure")
        monkeypatch.setattr(os, "startfile", _boom, raising=False)
    else:
        monkeypatch.setattr("ffgui.controller.terminal_argv",
                            lambda p: ["ffgui-no-such-terminal-xyz", "-e", "sh", p])
    target = tmp_path / "job.sh"
    with patch("ffgui.controller.QFileDialog.getSaveFileName",
               return_value=(str(target), "")):
        c.run_terminal()
    assert target.is_file()
    assert len(messages) == 1
    assert messages[0].startswith(f"saved {target} — ")
    if sys.platform == "win32":
        assert "simulated launch failure" in messages[0]
    else:
        assert "No such file or directory" in messages[0]


def test_restore_state_tolerates_corrupt_values(qapp, tmp_path):
    from PySide6.QtCore import QSettings

    from ffgui.app import _restore_state
    from ffgui.ui.shell import Shell
    ini = tmp_path / "corrupt.ini"
    ini.write_text("[General]\ntab=bogus\nsplitter=junk\n", encoding="utf-8")
    settings = QSettings(str(ini), QSettings.Format.IniFormat)
    shell = Shell()
    _restore_state(settings, shell)
    assert shell.tabs.currentIndex() == 0


def test_restore_state_applies_valid_values(qapp, tmp_path):
    from PySide6.QtCore import QSettings

    from ffgui.app import _restore_state
    from ffgui.ui.shell import Shell
    ini = tmp_path / "good.ini"
    settings = QSettings(str(ini), QSettings.Format.IniFormat)
    settings.setValue("tab", 2)
    settings.setValue("splitter", [100, 900])
    shell = Shell()
    _restore_state(settings, shell)
    assert shell.tabs.currentIndex() == 2
    assert len(shell.splitter.sizes()) == 2


def test_edits_on_output_less_row_surface_status(wired):
    """Probe-failure rows (Job with no outputs) are selectable: clearing the
    codec or setting cover art must report, never raise IndexError."""
    shell, doc, c, tmp_path = wired
    c.add_paths([str(tmp_path / "alpha.mp4")])
    doc.items.append(_error_row())
    c.refresh()
    messages = []
    c.status.connect(messages.append)
    shell.queue.selectRow(1)
    c._on_tab_edit("codec", "video", "")
    c._on_tab_edit("field", "cover_art", "x.jpg")
    assert messages == ["row has no output yet", "row has no output yet"]


def test_terminal_argv_darwin_passes_path_as_argv(monkeypatch):
    """A hostile path must never enter the AppleScript source: it travels as
    an osascript argument and is quoted at runtime by `quoted form of`."""
    from ffgui.controller import terminal_argv
    monkeypatch.setattr("ffgui.controller.sys",
                        type("S", (), {"platform": "darwin"}))
    hostile = '/tmp/x"; osascript -e \'evil\'; echo "'
    argv = terminal_argv(hostile)
    assert argv[0] == "osascript" and argv[-1] == hostile
    assert hostile not in " ".join(argv[:-1])
    assert "quoted form of" in " ".join(argv[:-1])


def test_terminal_argv_darwin_guards_leading_dash(monkeypatch):
    """The darwin `do script` string must pass `--` before the path: a dash-led
    save name would otherwise parse as an sh option."""
    from ffgui.controller import terminal_argv
    monkeypatch.setattr("ffgui.controller.sys",
                        type("S", (), {"platform": "darwin"}))
    argv = terminal_argv("-c")
    assert argv[-1] == "-c"
    assert '"sh -- " &' in argv[2]
    assert "-c" not in argv[2]


def test_restore_state_tolerates_corrupt_geometry(qapp, tmp_path):
    from PySide6.QtCore import QSettings

    from ffgui.app import _restore_state
    from ffgui.ui.shell import Shell
    ini = tmp_path / "badgeometry.ini"
    ini.write_text("[General]\ngeometry=junk\n", encoding="utf-8")
    settings = QSettings(str(ini), QSettings.Format.IniFormat)
    shell = Shell()
    _restore_state(settings, shell)
    assert shell.tabs.currentIndex() == 0


def test_terminal_argv_linux_guards_leading_dash(monkeypatch):
    from ffgui.controller import terminal_argv
    monkeypatch.setattr("ffgui.controller.sys",
                        type("S", (), {"platform": "linux"}))
    monkeypatch.setattr("ffgui.controller.shutil.which",
                        lambda n: "/usr/bin/xterm" if n == "xterm" else None)
    assert terminal_argv("-c") == ["xterm", "-e", "sh", "--", "-c"]


def test_run_terminal_unwritable_dir_surfaced(wired, monkeypatch):
    shell, doc, c, tmp_path = wired
    c.add_paths([str(tmp_path / "alpha.mp4")])
    messages = []
    c.status.connect(messages.append)
    launched = []
    monkeypatch.setattr("ffgui.controller.launch_script", launched.append)
    blocker = tmp_path / "blocker"
    blocker.write_bytes(b"x")
    target = blocker / "run.sh"
    with patch("ffgui.controller.QFileDialog.getSaveFileName",
               return_value=(str(target), "")):
        c.run_terminal()
    assert launched == []
    assert len(messages) == 1 and messages[0].startswith("export failed — ")


def test_open_job_file_deep_nesting_surfaced(wired):
    shell, doc, c, tmp_path = wired
    evil = tmp_path / "evil.ffgui"
    evil.write_text('{"job": {"inputs": ' + "[" * 200000, encoding="utf-8")
    messages = []
    c.status.connect(messages.append)
    c.open_job_file(str(evil))
    assert len(doc.items) == 0
    assert len(messages) == 1 and messages[0].startswith("invalid job file: ")


def test_open_job_file_non_dict_meta_falls_back_to_stem(wired):
    """A hand-edited .ffgui with meta as list/str/null must open with the
    file stem as name — AttributeError escaped the except tuple (crash)."""
    import json
    shell, doc, c, tmp_path = wired
    job = {"inputs": [{"path": "a.mp4"}], "outputs": [{"path": "o.mp4"}]}
    messages = []
    c.status.connect(messages.append)
    for tag, meta in (("list", []), ("str", "x"), ("null", None)):
        evil = tmp_path / f"{tag}.ffgui"
        evil.write_text(json.dumps({"job": job, "meta": meta}), encoding="utf-8")
        c.open_job_file(str(evil))
    assert [it.meta["name"] for it in doc.items] == ["list", "str", "null"]
    assert messages == [f"opened {tmp_path / t}.ffgui" for t in ("list", "str", "null")]


def test_probe_parented_survives_ref_drop(qapp, monkeypatch):
    import gc

    from PySide6.QtCore import QThread

    from ffgui.app import _launch_probe
    from ffgui.ui.shell import Shell
    monkeypatch.setattr("ffgui.app._Probe.run", lambda self: self.msleep(800))
    shell = Shell()
    probe = _launch_probe(shell, lambda cap: None, lambda msg: None)
    assert probe.parent() is shell
    del probe
    gc.collect()
    survivors = shell.findChildren(QThread)
    assert len(survivors) == 1
    assert survivors[0].wait(10000)


def test_reentrant_probe_shares_running_probe(qapp, monkeypatch):
    """A rescan while a probe runs must not launch a second probe: two
    completions would _boot twice, building two Controllers on one shell."""
    import gc

    from PySide6.QtCore import QThread

    from ffgui.app import _launch_probe
    from ffgui.ui.shell import Shell
    monkeypatch.setattr("ffgui.app._Probe.run", lambda self: self.msleep(800))
    shell = Shell()
    first = _launch_probe(shell, lambda cap: None, lambda msg: None)
    second = _launch_probe(shell, lambda cap: None, lambda msg: None)
    assert second is first
    assert len(shell.findChildren(QThread)) == 1
    assert first.wait(10000)
    gc.collect()


def test_duplicate_selected_duplicates_each_source_once(wired):
    """Multi-select duplicate must copy every selected row: duplicating
    low-to-high re-duplicated the just-inserted copy (indices shift)."""
    shell, doc, c, tmp_path = wired
    c.add_paths([str(tmp_path / "alpha.mp4"), str(tmp_path / "beta.mp4")])
    shell.queue.selectAll()
    c.duplicate_selected()
    names = [it.meta["name"] for it in doc.items]
    assert len(doc.items) == 4
    assert sorted(names) == ["alpha", "alpha copy", "beta", "beta copy"]
    assert names.count("alpha copy") == 1 and names.count("beta copy") == 1


def test_add_files_dialog_adds_chosen_paths(wired, monkeypatch):
    """The File-menu dialog funnels through add_paths (suffix filter applies)."""
    from PySide6.QtWidgets import QFileDialog
    shell, doc, c, tmp_path = wired
    picked = str(tmp_path / "alpha.mp4")
    monkeypatch.setattr(QFileDialog, "getOpenFileNames",
                        lambda *a, **k: ([picked, str(tmp_path / "notes.txt")], ""))
    (tmp_path / "notes.txt").write_bytes(b"x")
    c.add_files_dialog()
    assert [it.meta["name"] for it in doc.items] == ["alpha", "notes"]


def test_add_folder_dialog_recurses(wired, monkeypatch):
    from PySide6.QtWidgets import QFileDialog
    shell, doc, c, tmp_path = wired
    sub = tmp_path / "sub"
    sub.mkdir()
    (sub / "clip.mp4").write_bytes(b"0")
    monkeypatch.setattr(QFileDialog, "getExistingDirectory",
                        lambda *a, **k: str(tmp_path))
    c.add_folder_dialog()
    names = sorted(it.meta["name"] for it in doc.items)
    assert names == ["alpha", "beta", "clip"]


def test_concat_parts_dialog_extends_selection(wired, monkeypatch):
    from PySide6.QtWidgets import QFileDialog
    shell, doc, c, tmp_path = wired
    c.add_paths([str(tmp_path / "alpha.mp4")])
    shell.queue.selectRow(0)
    monkeypatch.setattr(QFileDialog, "getOpenFileNames",
                        lambda *a, **k: ([str(tmp_path / "beta.mp4")], ""))
    c.concat_parts_dialog()
    inp = doc.items[0].job.inputs[0]
    assert inp.concat_paths == [str(tmp_path / "alpha.mp4"), str(tmp_path / "beta.mp4")]
    assert "-f" in inp.input_args and "concat" in inp.input_args


def test_add_paths_ignores_non_iterable(wired):
    """A truthy non-list (programming slip, not user input) must not raise
    out of the Qt slot — like non-string elements, it is skipped."""
    _, doc, c, _ = wired
    c.add_paths(5)
    assert len(doc) == 0


def test_concat_parts_dialog_needs_single_row(wired, monkeypatch):
    from PySide6.QtWidgets import QFileDialog
    shell, doc, c, tmp_path = wired
    c.add_paths([str(tmp_path / "alpha.mp4"), str(tmp_path / "beta.mp4")])
    called = []
    monkeypatch.setattr(QFileDialog, "getOpenFileNames",
                        lambda *a, **k: called.append(1) or ([], ""))
    shell.queue.selectAll()
    c.concat_parts_dialog()
    assert called == []


def test_stream_info_dialog_lists_streams(wired, monkeypatch):
    """The dialog must open offscreen without hanging and reflect mapped flags."""
    from PySide6.QtWidgets import QDialog
    shell, doc, c, tmp_path = wired
    c.add_paths([str(tmp_path / "alpha.mp4")])
    shell.queue.selectRow(0)
    monkeypatch.setattr(QDialog, "exec", lambda self, *a, **k: 0)
    c.stream_info_dialog()
    dialogs = shell.findChildren(QDialog)
    assert dialogs
    from PySide6.QtWidgets import QCheckBox
    checks = dialogs[-1].findChildren(QCheckBox)
    assert len(checks) == 2 and all(b.isChecked() for b in checks)


def test_drop_filter_forwards_local_files(qapp):
    """External drops reach add_paths as local paths (non-local urls ignored)."""
    from PySide6.QtCore import QEvent, QMimeData, QUrl
    from ffgui.controller import _DropFilter
    filt = _DropFilter()
    got = []
    filt.pathsDropped.connect(got.extend)
    mime = QMimeData()
    mime.setUrls([QUrl.fromLocalFile("/tmp/a.mp4"), QUrl("https://x/y")])

    class Ev:
        def __init__(self, t):
            self._t = t
        def type(self):
            return self._t
        def mimeData(self):
            return mime
        def acceptProposedAction(self):
            pass

    assert filt.eventFilter(None, Ev(QEvent.Type.DragEnter)) is True
    assert filt.eventFilter(None, Ev(QEvent.Type.Drop)) is True
    assert got == ["/tmp/a.mp4"]


def test_preset_dialogs_save_and_delete(wired, monkeypatch):
    """Save dialog stores the selected row; delete removes the listed preset."""
    from PySide6.QtWidgets import QInputDialog
    from ffgui.store import load_presets
    shell, doc, c, tmp_path = wired
    c.add_paths([str(tmp_path / "alpha.mp4")])
    shell.queue.selectRow(0)
    monkeypatch.setattr(QInputDialog, "getText",
                        lambda *a, **k: ("evening", True))
    c.save_preset_dialog()
    assert "evening" in load_presets()
    c.refresh_presets()
    shell.preset_list.setCurrentRow(0)
    c.delete_preset()
    assert "evening" not in load_presets()


def test_locator_finds_posix_sibling_without_exe(qapp, tmp_path, monkeypatch):
    """A bundled `ffmpeg` (no .exe) next to the executable counts as found."""
    from PySide6.QtCore import QSettings

    sib = tmp_path / "sibling-ffmpeg"
    (sib / "ffmpeg").parent.mkdir(parents=True, exist_ok=True)
    (sib / "ffmpeg").write_bytes(b"")
    monkeypatch.delenv("FFMPEG_PATH", raising=False)
    settings = QSettings(str(tmp_path / "sib.ini"), QSettings.Format.IniFormat)
    monkeypatch.setattr("ffgui.controller.shutil.which", lambda name: None)
    monkeypatch.setattr("ffgui.controller.sys",
                        type("S", (), {"executable": str(sib / "python")}))
    assert locate_ffmpeg(settings) == (str(sib / "ffmpeg"), True)


def test_faststart_toggle_converges_legacy_slot(wired):
    """Rows written before the slot unification (movflags in video_options)
    converge to the mux slot on the next toggle — no duplicate flag."""
    from fftui.util.command_builder import build
    shell, doc, c, tmp_path = wired
    c.add_paths([str(tmp_path / "alpha.mp4")])
    shell.queue.selectRow(0)
    doc.items[0].job.outputs[0].video_options["movflags"] = "+faststart"
    assert c._apply_advanced("faststart", "", "False", 0) is None
    out = doc.items[0].job.outputs[0]
    assert "movflags" not in out.video_options
    assert build(doc.items[0].job).count("-movflags") == 0


def test_locator_expands_tilde(qapp, tmp_path, monkeypatch):
    """~/ffmpeg in FFMPEG_PATH must resolve via the home directory."""
    from PySide6.QtCore import QSettings

    fake_home = tmp_path / "home"
    fake_home.mkdir()
    (fake_home / "ffmpeg").write_bytes(b"")
    monkeypatch.setenv("HOME", str(fake_home))
    # expanduser("~") reads USERPROFILE, not HOME, on Windows.
    monkeypatch.setenv("USERPROFILE", str(fake_home))
    monkeypatch.setenv("FFMPEG_PATH", "~/ffmpeg")
    settings = QSettings(str(tmp_path / "tilde.ini"), QSettings.Format.IniFormat)
    monkeypatch.setattr("ffgui.controller.shutil.which", lambda name: None)
    assert locate_ffmpeg(settings) == (str(fake_home / "ffmpeg"), True)


def test_apply_flag_validates_instead_of_raw_setattr(wired):
    """Global blocked-list vs set_flag overlap: raw setattr let any flag key
    through — stray attrs build() ignores, blocked fields clobbered, saved
    queues poisoned. The flag path now routes through doc validation."""
    shell, doc, c, tmp_path = wired
    c.add_paths([str(tmp_path / "alpha.mp4")])
    assert c._apply_flag(0, "noconfirm", "True") is None
    assert doc.items[0].job.noconfirm is True
    err = c._apply_flag(0, "bogus", "True")
    assert err and "bogus" in err
    assert not hasattr(doc.items[0].job, "bogus")
    err = c._apply_flag(0, "inputs", "True")
    assert err and "not a flag" in err
    assert len(doc.items[0].job.inputs) == 1
    assert c._apply_flag(0, "two_pass", "True") is None
    assert doc.items[0].job.two_pass is True
