"""QueueDocument tests: reorder persistence, bulk edit, validation, round-trip."""

import pytest
from fftui.ffmpeg.capability_index import CapabilityIndex
from fftui.model import InputStream, Input
from fftui.util.command_builder import build

from ffgui.doc import QueueDocument


def fake_probe(path: str):
    return _inputs[path]


_inputs = {}


def _mk_input(path: str):
    return Input(
        path=path,
        streams=[
            InputStream(input_index=0, spec="v:0", codec_type="video",
                        codec_name="h264", width=64, height=48),
            InputStream(input_index=0, spec="a:0", codec_type="audio",
                        codec_name="aac", sample_rate=48000),
        ])


@pytest.fixture()
def doc(tmp_path):
    for stem in ("alpha", "beta", "gamma"):
        p = tmp_path / f"{stem}.mp4"
        p.write_bytes(b"0")
        _inputs[str(p)] = _mk_input(str(p))
    return QueueDocument(CapabilityIndex.stub(), prober=fake_probe)


def paths_of(doc):
    return [it.job.inputs[0].path for it in doc.items]


def test_add_files_builds_jobs_with_defaults(doc, tmp_path):
    added = doc.add_files([str(tmp_path / "alpha.mp4"), str(tmp_path / "beta.mp4")])
    assert added == [0, 1]
    assert doc.items[0].job.outputs[0].path == "alpha_out.mp4"
    assert doc.items[0].meta["name"] == "alpha"
    assert doc.items[0].error is None


def test_reorder_moves_job_and_meta_together(doc, tmp_path):
    doc.add_files([str(tmp_path / f"{s}.mp4") for s in ("alpha", "beta", "gamma")])
    doc.set_option(0, "video", "crf", "20")
    doc.move_rows([0], 3)
    assert paths_of(doc)[2].endswith("alpha.mp4")
    assert doc.items[2].job.outputs[0].video_options["crf"] == "20"
    assert doc.items[2].meta["name"] == "alpha"


def test_bulk_set_option_hits_selection_only(doc, tmp_path):
    doc.add_files([str(tmp_path / f"{s}.mp4") for s in ("alpha", "beta", "gamma")])
    result = doc.bulk_set_option([0, 1], "video", "crf", "23")
    assert result == {0: None, 1: None}
    assert doc.items[0].job.outputs[0].video_options["crf"] == "23"
    assert doc.items[1].job.outputs[0].video_options["crf"] == "23"
    assert "crf" not in doc.items[2].job.outputs[0].video_options


def test_invalid_option_blocks_and_leaves_model_unchanged(doc, tmp_path):
    doc.add_files([str(tmp_path / "alpha.mp4")])
    err = doc.set_option(0, "video", "crf", "abc")
    assert err and "integer" in err
    assert "crf" not in doc.items[0].job.outputs[0].video_options
    err = doc.set_option(0, "video", "crf", '23"')
    assert err and "quotes" in err
    assert '"' not in str(doc.items[0].job.outputs[0].video_options)
    err = doc.set_option(0, "nope", "crf", "1")
    assert err and "unknown scope" in err


def test_argv_matches_builder(doc, tmp_path):
    doc.add_files([str(tmp_path / "alpha.mp4")])
    doc.set_codec(0, "video", "libx264")
    assert doc.argv(0) == build(doc.items[0].job)


def test_save_load_round_trip(doc, tmp_path):
    doc.add_files([str(tmp_path / f"{s}.mp4") for s in ("alpha", "beta")])
    doc.set_option(0, "video", "crf", "18")
    doc.set_codec(1, "video", "libx265")
    store = tmp_path / "store"
    doc.save(store)
    loaded = QueueDocument.load(CapabilityIndex.stub(), store, prober=fake_probe)
    assert len(loaded) == 2
    assert loaded.items[0].job.outputs[0].video_options["crf"] == "18"
    assert loaded.items[1].job.outputs[0].video_codec == "libx265"
    assert loaded.items[0].meta["name"] == "alpha"


def test_remove_rows_multi_select(doc, tmp_path):
    doc.add_files([str(tmp_path / f"{s}.mp4") for s in ("alpha", "beta", "gamma")])
    doc.remove_rows([0, 2])
    assert [it.meta["name"] for it in doc.items] == ["beta"]


def test_duplicate_clones_settings(doc, tmp_path):
    doc.add_files([str(tmp_path / "alpha.mp4")])
    doc.set_option(0, "video", "crf", "21")
    new = doc.duplicate_row(0)
    assert new == 1
    assert doc.items[1].job.outputs[0].video_options["crf"] == "21"
    assert doc.items[1].meta["name"] == "alpha copy"
    assert doc.items[1].job is not doc.items[0].job


def test_set_codec_subtitles_none(doc, tmp_path):
    doc.add_files([str(tmp_path / "alpha.mp4")])
    assert doc.set_codec(0, "subtitle", "none") is None
    out = doc.items[0].job.outputs[0]
    assert out.subtitle_codec is None and out.sn_explicit
    assert doc.set_codec(0, "subtitle", "mov_text") is None
    assert out.subtitle_codec == "mov_text" and not out.sn_explicit
    assert doc.set_codec(0, "midi", "x")


def test_add_file_with_probe_error_records_row(doc, tmp_path):
    def bad_prober(path):
        raise RuntimeError("probe failed")
    doc2 = QueueDocument(CapabilityIndex.stub(), prober=bad_prober)
    doc2.add_files([str(tmp_path / "alpha.mp4")])
    assert len(doc2) == 1
    assert "probe failed" in doc2.items[0].error


def test_load_unparseable_row_surfaces_error(tmp_path):
    from ffgui.store import save_queue, UnparsedRow
    from fftui.util.paths import cache_dir as _c  # noqa: F401
    store = tmp_path / "store"
    save_queue([(UnparsedRow({"outputs": 7}), {"name": "broken", "enabled": True,
                                               "notes": "", "source_hash": ""})], store)
    loaded = QueueDocument.load(CapabilityIndex.stub(), store, prober=fake_probe)
    assert len(loaded) == 1 and loaded.items[0].error


def test_set_codec_warns_unknown_encoder_but_commits(doc, tmp_path):
    doc.add_files([str(tmp_path / "alpha.mp4")])
    assert doc.set_codec(0, "video", "libx264") is None
    warn = doc.set_codec(0, "video", "made_up")
    assert warn and "made_up" in warn
    assert doc.items[0].job.outputs[0].video_codec == "made_up"
    assert doc.set_codec(0, "subtitle", "mov_text") is None


def test_trim_with_stream_copy_warns_but_commits(doc, tmp_path):
    doc.add_files([str(tmp_path / "alpha.mp4")])
    assert doc.set_codec(0, "video", "copy") is None
    warn = doc.set_output_field(0, "start", "5")
    assert warn and "copy" in warn
    assert doc.items[0].job.outputs[0].start == "5"
    doc.set_codec(0, "video", "libx264")
    assert doc.set_output_field(0, "start", "8") is None


def test_move_rows_ignores_duplicate_indices(doc, tmp_path):
    doc.add_files([str(tmp_path / f"{s}.mp4") for s in ("alpha", "beta", "gamma")])
    doc.move_rows([0, 0, 1, 1], 3)
    assert [it.meta["name"] for it in doc.items] == ["gamma", "alpha", "beta"]
    assert len(doc.items) == 3


def test_move_rows_empty_selection_is_noop(doc, tmp_path):
    doc.add_files([str(tmp_path / f"{s}.mp4") for s in ("alpha", "beta", "gamma")])
    before = paths_of(doc)
    doc.move_rows([], 1)
    assert paths_of(doc) == before


def test_move_rows_clamps_target_beyond_end(doc, tmp_path):
    doc.add_files([str(tmp_path / f"{s}.mp4") for s in ("alpha", "beta", "gamma")])
    doc.move_rows([0], 99)
    assert [it.meta["name"] for it in doc.items] == ["beta", "gamma", "alpha"]


def test_move_rows_clamps_negative_target(doc, tmp_path):
    doc.add_files([str(tmp_path / f"{s}.mp4") for s in ("alpha", "beta", "gamma")])
    doc.move_rows([2], -5)
    assert [it.meta["name"] for it in doc.items] == ["gamma", "alpha", "beta"]


def test_move_rows_full_list_move_preserves_order(doc, tmp_path):
    doc.add_files([str(tmp_path / f"{s}.mp4") for s in ("alpha", "beta", "gamma")])
    doc.move_rows([0, 1, 2], 0)
    assert [it.meta["name"] for it in doc.items] == ["alpha", "beta", "gamma"]
    doc.move_rows([2, 0, 1], 1)
    assert [it.meta["name"] for it in doc.items] == ["alpha", "beta", "gamma"]


def test_move_rows_ignores_out_of_range_indices(doc, tmp_path):
    doc.add_files([str(tmp_path / f"{s}.mp4") for s in ("alpha", "beta", "gamma")])
    doc.move_rows([7], 0)
    assert [it.meta["name"] for it in doc.items] == ["alpha", "beta", "gamma"]
    doc.move_rows([-1], 0)
    assert [it.meta["name"] for it in doc.items] == ["alpha", "beta", "gamma"]
    assert len(doc.items) == 3


def test_set_option_clear_pops_key(doc, tmp_path):
    doc.add_files([str(tmp_path / "alpha.mp4")])
    out = doc.items[0].job.outputs[0]
    assert doc.set_option(0, "video", "crf", "20") is None
    assert out.video_options == {"crf": "20"}
    assert doc.set_option(0, "video", "crf", "") is None
    assert "crf" not in out.video_options
    assert doc.set_option(0, "audio", "b:a", "128k") is None
    assert doc.set_option(0, "audio", "b:a", "") is None
    assert "b:a" not in out.audio_options


def test_set_option_global_list_fields_rejected(doc, tmp_path):
    doc.add_files([str(tmp_path / "alpha.mp4")])
    for key in ("inputs", "outputs", "video_filters", "audio_filters",
                "filter_complex", "hw_devices"):
        assert "not a plain option" in doc.set_option(0, "global", key, "x")
    assert "unknown global" in doc.set_option(0, "global", "nope", "x")


def test_set_option_int_empty_string_clears(doc, tmp_path):
    doc.add_files([str(tmp_path / "alpha.mp4")])
    out = doc.items[0].job.outputs[0]
    assert doc.set_option(0, "video", "crf", "20") is None
    assert doc.set_option(0, "video", "crf", "") is None
    assert "crf" not in out.video_options


def test_global_str_clear_restores_none_and_omits_flag(doc, tmp_path):
    """Clearing a global str option must not leave '' (build emits '-flag ''')."""
    doc.add_files([str(tmp_path / "alpha.mp4")])
    assert doc.set_option(0, "global", "cpuflags", "haswell") is None
    assert doc.items[0].job.cpuflags == "haswell"
    assert doc.set_option(0, "global", "cpuflags", "") is None
    assert doc.items[0].job.cpuflags is None
    assert "-cpuflags" not in doc.argv(0)


def test_int_fields_reject_non_canonical_numerics(doc, tmp_path):
    doc.add_files([str(tmp_path / "alpha.mp4")])
    for bad in ("5_0", "5.0", "0x10", "1e3", " 5", "23 "):
        err = doc.set_option(0, "video", "crf", bad)
        assert err and "integer" in err, bad
    assert "crf" not in doc.items[0].job.outputs[0].video_options
    for good in ("23", "-3", "+5", "0"):
        assert doc.set_option(0, "video", "crf", good) is None, good


def test_windows_paths_pass_output_field_validation(doc, tmp_path):
    """Backslashes are legal (Windows paths); quotes/newlines still rejected."""
    doc.add_files([str(tmp_path / "alpha.mp4")])
    assert doc.set_output_field(0, "path", "C:\\Vids\\out.mp4") is None
    assert doc.items[0].job.outputs[0].path == "C:\\Vids\\out.mp4"
    assert doc.set_output_field(0, "cover_art", "C:\\img\\cover.jpg") is None
    assert doc.set_output_field(0, "path", 'C:\\x"y') is not None


def test_set_output_field_rejects_empty_path(doc, tmp_path):
    """Clearing the path committed None and crashed refresh (Path(None)):
    the filename box fires per keystroke, so select-all+delete is routine."""
    doc.add_files([str(tmp_path / "alpha.mp4")])
    before = doc.items[0].job.outputs[0].path
    assert doc.set_output_field(0, "path", "")
    assert doc.items[0].job.outputs[0].path == before


def test_empty_io_row_edits_are_guarded(doc, tmp_path):
    from fftui.model import Job

    from ffgui.doc import QueueItem
    doc.items.append(QueueItem(Job(inputs=[], outputs=[]),
                               {"name": "bad", "enabled": True, "notes": "", "source_hash": ""},
                               error="unreadable"))
    assert doc.set_hw_decode(0, "h264_cuvid") == "row has no input yet"
    assert doc.set_input_arg(0, "-ss", "5") == "row has no input yet"
    for method, args in ((doc.set_codec, ("video", "libx264")),
                         (doc.set_option, ("video", "crf", "20")),
                         (doc.set_output_field, ("start", "5")),
                         (doc.set_filters, ("video_filters", ["scale=64:64"])),
                         (doc.set_chapters, ([],)),
                         (doc.set_metadata, ("title", "x")),
                         (doc.set_disposition, ("v:0", "default")),
                         (doc.set_stream_meta, ("v:0", "language", "eng"))):
        assert method(0, *args) == "row has no output yet", method.__name__


def test_remove_rows_ignores_out_of_range_handles(doc, tmp_path):
    """A stale handle (-1, huge) must not delete the wrong row or raise."""
    doc.add_files([str(tmp_path / f"{s}.mp4") for s in ("alpha", "beta", "gamma")])
    doc.remove_rows([-1, 99])
    assert [it.meta["name"] for it in doc.items] == ["alpha", "beta", "gamma"]


def test_negative_row_handle_touches_nothing(doc, tmp_path):
    """-1 is Qt for 'no selection': every row API must reject it, never wrap."""
    import pytest

    doc.add_files([str(tmp_path / "alpha.mp4")])
    doc.set_option(0, "video", "crf", "20")
    with pytest.raises(IndexError):
        doc.item(-1)
    with pytest.raises(IndexError):
        doc.set_option(-1, "video", "crf", "22")
    with pytest.raises(IndexError):
        doc.set_codec(-1, "video", "libx264")
    with pytest.raises(IndexError):
        doc.set_two_pass(-1, True)
    with pytest.raises(IndexError):
        doc.duplicate_row(-1)
    assert doc.items[0].job.outputs[0].video_options["crf"] == "20"
    assert doc.items[0].job.two_pass is False
    assert len(doc.items) == 1


def test_bulk_set_option_reports_unknown_rows(doc, tmp_path):
    doc.add_files([str(tmp_path / "alpha.mp4")])
    result = doc.bulk_set_option([0, 99], "video", "crf", "20")
    assert result[0] is None
    assert "no such row" in result[99]
    assert doc.items[0].job.outputs[0].video_options["crf"] == "20"


def test_set_codec_empty_string_clears_flag(doc, tmp_path):
    """Clearing a codec must restore None, not store '' (build emits -c:v '')."""
    doc.add_files([str(tmp_path / "alpha.mp4")])
    assert doc.set_codec(0, "video", "libx264") is None
    assert doc.set_codec(0, "video", "") is None
    assert doc.items[0].job.outputs[0].video_codec is None
    assert "-c:v" not in doc.argv(0)


def test_noop_structure_ops_emit_no_signal(doc, tmp_path):
    """A no-op is not a mutation: empty/invalid add/remove/move stay silent."""
    doc.add_files([str(tmp_path / f"{s}.mp4") for s in ("alpha", "beta")])
    order = []
    doc.orderChanged.connect(lambda: order.append(1))
    assert doc.add_files([]) == []
    doc.remove_rows([])
    doc.remove_rows([-1, 99])
    doc.move_rows([], 1)
    assert order == []
    doc.move_rows([0], 1)
    doc.remove_rows([1])
    doc.add_files([str(tmp_path / "gamma.mp4")])
    assert len(order) == 3


def test_duplicate_row_tolerates_nameless_meta(doc, tmp_path):
    from ffgui.doc import QueueItem
    from fftui.model import Job
    doc.add_files([str(tmp_path / "alpha.mp4")])
    doc.items.append(QueueItem(Job(inputs=[], outputs=[]), {}))
    new = doc.duplicate_row(1)
    assert new == 2
    assert doc.items[2].meta["name"] == " copy"


def test_set_chapters_rejects_wire_separators(doc, tmp_path):
    """Chapter text rides a \\n-joined / \\x1f-separated wire format (tabs._emit);
    storing those chars would corrupt the decode into phantom rows or a crash."""
    from fftui.model import Chapter
    doc.add_files([str(tmp_path / "alpha.mp4")])
    for bad in ("a\nb", "a\rb", "a\x1fb", "a\0b"):
        err = doc.set_chapters(0, [Chapter(start="0", title=bad)])
        assert err, repr(bad)
    assert doc.items[0].job.outputs[0].chapters == []
    assert doc.set_chapters(0, [Chapter(start="0", title="café ☃ — say hi")]) is None


def test_set_stream_meta_stores_builder_pipe_format(doc, tmp_path):
    """edit->build must emit -metadata:s:<spec> <tag>=<value> (ffmpeg's shape);
    the builder only renders that from the ``spec|tag`` key the TUI writes."""
    doc.add_files([str(tmp_path / "alpha.mp4")])
    assert doc.set_stream_meta(0, "v:0", "language", "eng") is None
    out = doc.items[0].job.outputs[0]
    assert out.stream_metadata == {"v:0|language": "eng"}
    argv = doc.argv(0)
    flag = argv.index("-metadata:s:v:0")
    assert argv[flag + 1] == "language=eng"
    store = tmp_path / "store"
    doc.save(store)
    loaded = QueueDocument.load(CapabilityIndex.stub(), store, prober=fake_probe)
    assert loaded.items[0].job.outputs[0].stream_metadata == {"v:0|language": "eng"}
    assert loaded.argv(0) == argv


def test_set_stream_meta_rejects_wire_structural_keys(doc, tmp_path):
    """``|``/``=`` in the tag key would corrupt the builder's key split and
    silently mistarget the tag, so the owning layer refuses them."""
    doc.add_files([str(tmp_path / "alpha.mp4")])
    out = doc.items[0].job.outputs[0]
    assert doc.set_stream_meta(0, "v:0", "a|b", "eng")
    assert doc.set_stream_meta(0, "v:0", "a=b", "eng")
    assert out.stream_metadata == {}


def test_set_stream_meta_heals_legacy_colon_twin(doc, tmp_path):
    """Rows written before the ``spec|tag`` contract carry ``spec:tag``; an
    explicit rewrite of that tag must replace the twin, not duplicate it."""
    doc.add_files([str(tmp_path / "alpha.mp4")])
    out = doc.items[0].job.outputs[0]
    out.stream_metadata["v:0:language"] = "ger"
    assert doc.set_stream_meta(0, "v:0", "language", "eng") is None
    assert out.stream_metadata == {"v:0|language": "eng"}


def test_set_chapters_rejects_starts_the_builder_refuses(doc, tmp_path):
    """Refuse at edit what build() would raise on: unparseable/overflowing
    starts and out-of-order chapters (the builder's own render is the oracle)."""
    from fftui.model import Chapter
    doc.add_files([str(tmp_path / "alpha.mp4")])
    out = doc.items[0].job.outputs[0]
    for bad in ("", "banana", "12:ab", "inf", "1e999999999"):
        assert doc.set_chapters(0, [Chapter(start=bad, title="X")]), bad
    assert out.chapters == []
    assert "ascending" in doc.set_chapters(
        0, [Chapter(start="60", title="B"), Chapter(start="5", title="A")])
    assert out.chapters == []
    assert doc.set_chapters(0, [Chapter(start="0", title="Intro 🎬"),
                                Chapter(start="60", title="Act 1")]) is None
    build(doc.items[0].job)


def test_set_chapters_rejects_bat_unrepresentable_text(doc, tmp_path):
    """``"`` in a chapter title crashes .bat export (sidecars echo raw);
    NUL poisons argv and scripts alike. Refuse both at the owning layer."""
    from fftui.model import Chapter
    doc.add_files([str(tmp_path / "alpha.mp4")])
    out = doc.items[0].job.outputs[0]
    for bad in ('say "hi"', "a\0b"):
        assert doc.set_chapters(0, [Chapter(start="0", title=bad)]), repr(bad)
    assert out.chapters == []


def test_set_disposition_validates_values(doc, tmp_path):
    """ffmpeg knows a fixed disposition flag set (plus 0 to clear); anything
    else fails at runtime, so refuse it at edit with the accepted shape."""
    doc.add_files([str(tmp_path / "alpha.mp4")])
    out = doc.items[0].job.outputs[0]
    for bad in ("bogus!!!", "bogus", "ATTACHED_PIC", "default,forced", "1"):
        assert doc.set_disposition(0, "v:0", bad), bad
    assert out.dispositions == {}
    for good in ("0", "default", "forced", "+default", "-default",
                 "default+forced", "hearing_impaired"):
        assert doc.set_disposition(0, "v:0", good) is None, good
    assert out.dispositions == {"v:0": "hearing_impaired"}
    assert "-disposition:v:0" in doc.argv(0)
    assert doc.set_disposition(0, "v:0", "") is None
    assert out.dispositions == {}


def test_set_codec_subtitle_clear_resets_drop(doc, tmp_path):
    """Unchecking Drop commits ("codec", "subtitle", "") through set_codec's
    clear branch; it must reset sn_explicit, not leave -sn stuck on."""
    doc.add_files([str(tmp_path / "alpha.mp4")])
    out = doc.items[0].job.outputs[0]
    assert doc.set_codec(0, "subtitle", "none") is None
    assert out.sn_explicit
    assert doc.set_codec(0, "subtitle", "") is None
    assert out.subtitle_codec is None and not out.sn_explicit


def test_unparseable_row_survives_doc_save_roundtrip(tmp_path):
    """load() must park the raw payload so save() writes it back byte-equal
    instead of persisting the placeholder empty Job (store carry-through)."""
    from ffgui.store import load_queue, save_queue, UnparsedRow
    store = tmp_path / "store"
    raw = {"inputs": [], "outputs": [{"path": 123}]}
    save_queue([(UnparsedRow(raw), {"name": "broken", "enabled": True,
                                    "notes": "", "source_hash": ""})], store)
    loaded = QueueDocument.load(CapabilityIndex.stub(), store, prober=fake_probe)
    assert len(loaded) == 1 and loaded.items[0].error
    loaded.save(store)
    again = load_queue(store)
    assert isinstance(again[0][0], UnparsedRow)
    assert again[0][0].raw == raw


def test_editing_unparseable_row_converts_it_to_real_row(tmp_path):
    """A successful global edit drops the parked payload: the edit must win
    on save rather than vanish behind the untouched original."""
    from ffgui.store import load_queue, save_queue, UnparsedRow
    store = tmp_path / "store"
    save_queue([(UnparsedRow({"inputs": [], "outputs": [{"path": 123}]}),
                 {"name": "broken", "enabled": True,
                  "notes": "", "source_hash": ""})], store)
    loaded = QueueDocument.load(CapabilityIndex.stub(), store, prober=fake_probe)
    assert loaded.set_option(0, "global", "hide_banner", "1") is None
    loaded.save(store)
    again = load_queue(store)
    assert not isinstance(again[0][0], UnparsedRow)
    assert again[0][0].hide_banner is True


def test_argvs_matches_two_pass_mode(doc, tmp_path):
    """QueueDocument.argvs is the preview/export seam: one argv normally, two
    (pass-tagged) with two-pass on."""
    from fftui.util.command_builder import build_two_pass
    doc.add_files([str(tmp_path / "alpha.mp4")])
    assert doc.argvs(0) == [build(doc.items[0].job)]
    doc.set_two_pass(0, True)
    argvs = doc.argvs(0)
    assert argvs == build_two_pass(doc.items[0].job)
    assert len(argvs) == 2
    assert all(any("-pass" in tok for tok in argv) for argv in argvs)


def test_is_truthy_unifies_toggle_text():
    """The single truthy set behind flag/faststart/global-bool edits.

    Leaf-owned by store: doc, controller, and the expert check editor share
    this exact function — a re-spelled tuple once dropped 'yes'/'on'."""
    from ffgui.doc import is_truthy as doc_is_truthy
    from ffgui.store import TRUTHY, is_truthy
    assert doc_is_truthy is is_truthy
    assert frozenset({"1", "true", "yes", "on"}) == TRUTHY
    for on in ("1", "True", "TRUE", "yes", " on ", "ON"):
        assert is_truthy(on), on
    for off in ("", "0", "false", "no", "off"):
        assert not is_truthy(off), off
    assert not is_truthy(None) and not is_truthy(123)


def test_crf_clears_stale_cq_sibling(doc, tmp_path):
    """"CRF / CQ" is one VideoTab field (load shows crf-or-cq): editing it
    must not leave the sibling behind, or build() emits both -crf and -cq."""
    doc.add_files([str(tmp_path / "alpha.mp4")])
    out = doc.items[0].job.outputs[0]
    out.video_options["cq"] = "28"
    assert doc.set_option(0, "video", "crf", "23") is None
    assert out.video_options == {"crf": "23"}
    assert doc.set_option(0, "video", "cq", "28") is None
    assert out.video_options == {"cq": "28"}
    assert doc.set_option(0, "video", "crf", "") is None
    assert "crf" not in out.video_options and "cq" not in out.video_options


def test_every_successful_edit_drops_parked_unparsed_payload(doc):
    """Every mutating setter must convert the row: otherwise an edit to a
    parked row vanishes on save behind the untouched original payload."""
    from fftui.model import Chapter

    from ffgui.store import UnparsedRow
    doc.add_files([str(next(iter(_inputs)))])
    edits = [
        lambda: doc.set_codec(0, "video", "libx264"),
        lambda: doc.set_option(0, "video", "crf", "23"),
        lambda: doc.set_option(0, "global", "hide_banner", "1"),
        lambda: doc.set_output_field(0, "cover_art", "art.jpg"),
        lambda: doc.set_metadata(0, "title", "hi"),
        lambda: doc.set_two_pass(0, True),
        lambda: doc.set_flag(0, "noconfirm", "True"),
        lambda: doc.set_faststart(0, "True"),
        lambda: doc.set_segment_enabled(0, "True"),
        lambda: doc.set_bsf(0, "v:0=h264_mp4toannexb"),
        lambda: doc.set_tee(0, "[f=mp4]pipe:1"),
        lambda: doc.join_parts(0, [list(_inputs)[1]]),
        lambda: doc.set_filters(0, "audio_filters", ["aresample=48000"]),
        lambda: doc.set_chapters(0, [Chapter(start="0", title="Intro")]),
        lambda: doc.set_stream_meta(0, "v:0", "language", "eng"),
        lambda: doc.set_disposition(0, "v:0", "default"),
        lambda: doc.set_hw_decode(0, ""),
        lambda: doc.set_input_arg(0, "-ss", "1"),
    ]
    for edit in edits:
        doc.items[0].unparsed = UnparsedRow({"outputs": 7})
        assert edit() is None
        assert doc.items[0].unparsed is None


def test_mux_container_key_refused_as_invalid_flag(doc, tmp_path):
    """Validate-vs-build parity: mux-slot 'container' would emit '-container x',
    which ffmpeg has no such flag for — the Container output field owns -f."""
    doc.add_files([str(tmp_path / "alpha.mp4")])
    err = doc.set_option(0, "mux", "container", "mp4")
    assert err and "Container" in err
    out = doc.items[0].job.outputs[0]
    assert "container" not in out.options
    assert "-container" not in doc.argv(0)


def test_container_key_refused_in_codec_scopes(doc, tmp_path):
    """Same bogus emission as the mux slot: codec option dicts render every
    key as -key, so 'container' would emit '-container x' from video/audio too."""
    doc.add_files([str(tmp_path / "alpha.mp4")])
    for scope in ("video", "audio"):
        err = doc.set_option(0, scope, "container", "mp4")
        assert err and "Container" in err
    out = doc.items[0].job.outputs[0]
    assert out.video_options == {} and out.audio_options == {}
    assert "-container" not in doc.argv(0)


def test_mux_f_clears_container_field_single_dash_f(doc, tmp_path):
    """Validate-vs-build parity: container field + mux 'f' must not reach the
    script as two competing -f flags (silent last-wins); last write wins."""
    doc.add_files([str(tmp_path / "alpha.mp4")])
    assert doc.set_output_field(0, "container", "mkv") is None
    assert doc.set_option(0, "mux", "f", "matroska") is None
    assert doc.items[0].job.outputs[0].container is None
    argv = doc.argv(0)
    assert argv.count("-f") == 1 and "matroska" in argv


def test_container_field_clears_mux_f(doc, tmp_path):
    doc.add_files([str(tmp_path / "alpha.mp4")])
    assert doc.set_option(0, "mux", "f", "matroska") is None
    assert doc.set_output_field(0, "container", "mp4") is None
    assert "f" not in doc.items[0].job.outputs[0].options
    argv = doc.argv(0)
    assert argv.count("-f") == 1 and "mp4" in argv


def test_global_two_pass_refused_in_favour_of_toggle(doc, tmp_path):
    """Validate-vs-build parity: build() cannot render a two-pass job, so the
    plain-option path must refuse it and point at set_two_pass + argvs()."""
    doc.add_files([str(tmp_path / "alpha.mp4")])
    err = doc.set_option(0, "global", "two_pass", "true")
    assert err and "two_pass" in err
    assert doc.items[0].job.two_pass is False
    assert doc.argv(0)


def test_global_bool_rejects_garbage_text(doc, tmp_path):
    """Validate-vs-build parity: curated ints reject garbage ('abc'), but any
    string coerced to False — 'banana' must not silently mean off."""
    doc.add_files([str(tmp_path / "alpha.mp4")])
    err = doc.set_option(0, "global", "noconfirm", "banana")
    assert err and "true" in err
    assert doc.items[0].job.noconfirm is False
    for good, want in (("True", True), ("False", False), ("1", True),
                       ("0", False), ("yes", True), ("no", False), ("", False)):
        assert doc.set_option(0, "global", "noconfirm", good) is None, good
        assert doc.items[0].job.noconfirm is want, good


def test_set_flag_toggles_and_validates(doc, tmp_path):
    """The flag-kind edit path owns global bools: unknown/blocked/non-bool keys
    are refused, two_pass routes to the two-pass toggle."""
    doc.add_files([str(tmp_path / "alpha.mp4")])
    assert doc.set_flag(0, "noconfirm", "True") is None
    assert doc.items[0].job.noconfirm is True
    assert "-n" in doc.argv(0)
    assert doc.set_flag(0, "noconfirm", "False") is None
    assert doc.items[0].job.noconfirm is False
    assert "bogus" in (doc.set_flag(0, "bogus", "True") or "")
    assert not hasattr(doc.items[0].job, "bogus")
    before = list(doc.items[0].job.inputs)
    assert doc.set_flag(0, "inputs", "True") is not None
    assert list(doc.items[0].job.inputs) == before
    assert doc.set_flag(0, "loglevel", "True") is not None
    assert doc.items[0].job.loglevel is None
    assert doc.set_flag(0, "two_pass", "True") is None
    assert doc.items[0].job.two_pass is True
