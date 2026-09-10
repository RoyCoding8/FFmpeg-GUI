"""Store tests: schema-2 queue/presets round-trip, quarantine, TUI import (written first)."""

import json

import pytest
from fftui.model import Job

from ffgui.store import (
    GUI_SCHEMA,
    UnparsedRow,
    gui_dirs,
    import_tui_queue,
    load_presets,
    load_queue,
    save_preset,
    save_queue,
)


def job_dict(inp="a.mp4", out="a_out.mp4") -> dict:
    return {"inputs": [{"path": inp}], "outputs": [{"path": out}]}


def meta(name="Row 1", source_hash="h1") -> dict:
    return {"name": name, "enabled": True, "notes": "", "source_hash": source_hash}


def test_round_trip_with_meta(tmp_path):
    rows = [(Job.from_dict(job_dict()), meta("First", "h1")),
            (Job.from_dict(job_dict("b.mkv", "b_out.mkv")), meta("Second", "h2"))]
    save_queue(rows, tmp_path)
    loaded = load_queue(tmp_path)
    want = [(Job, meta("First", "h1")), (Job, meta("Second", "h2"))]
    assert [(type(j), m) for j, m in loaded] == want
    assert loaded[0][0].outputs[0].path == "a_out.mp4"
    assert loaded[1][0].inputs[0].path == "b.mkv"


@pytest.mark.parametrize("text", ["{not json", ""])
def test_corrupt_file_quarantines_and_empties(tmp_path, text):
    (tmp_path / "queue.json").write_text(text, encoding="utf-8")
    assert load_queue(tmp_path) == []
    assert list(tmp_path.glob("queue.json.bad*"))


def test_unknown_row_carried_through_save(tmp_path):
    rows = [{"job": job_dict(), "gui": meta()},
            {"job": {"outputs": "garbage"}, "gui": meta("bad")}]
    (tmp_path / "queue.json").write_text(
        json.dumps({"schema": GUI_SCHEMA, "rows": rows}), encoding="utf-8")
    loaded = load_queue(tmp_path)
    assert isinstance(loaded[0][0], Job)
    assert isinstance(loaded[1][0], UnparsedRow)
    save_queue(loaded, tmp_path)
    on_disk = json.loads((tmp_path / "queue.json").read_text(encoding="utf-8"))
    assert len(on_disk["rows"]) == 2
    assert on_disk["rows"][1]["job"] == {"outputs": "garbage"}


def test_foreign_envelope_quarantines(tmp_path):
    (tmp_path / "queue.json").write_text(
        json.dumps({"schema": 1, "jobs": [job_dict()]}), encoding="utf-8")
    assert load_queue(tmp_path) == []
    assert list(tmp_path.glob("queue.json.bad*"))


def test_import_tui_queue(tmp_path):
    tui = tmp_path / "tui"
    tui.mkdir()
    (tui / "queue.json").write_text(
        json.dumps({"schema": 1, "jobs": [job_dict(), job_dict("b.mkv", "b_out.mkv"),
                                          {"outputs": 7}]}),
        encoding="utf-8")
    gui = tmp_path / "gui"
    imported, skipped, errors = import_tui_queue(tui, gui)
    assert (imported, skipped, len(errors)) == (2, 0, 1)
    assert "row 3" in errors[0]
    rows = load_queue(gui)
    want = [_hash(job_dict()), _hash(job_dict("b.mkv", "b_out.mkv"))]
    assert [m["source_hash"] for _, m in rows] == want
    assert all(m["enabled"] and m["name"] for _, m in rows)
    again = import_tui_queue(tui, gui)
    assert again[:2] == (0, 2)


def _hash(raw: dict) -> str:
    import hashlib

    return hashlib.sha256(json.dumps(raw, sort_keys=True).encode()).hexdigest()


def test_import_tui_bare_list_and_missing_file(tmp_path):
    tui = tmp_path / "tui"
    tui.mkdir()
    (tui / "queue.json").write_text(json.dumps([job_dict()]), encoding="utf-8")
    imported, _, _ = import_tui_queue(tui, tmp_path / "gui")
    assert imported == 1
    assert import_tui_queue(tmp_path / "none", tmp_path / "gui2") == (0, 0, [])


def test_import_preserves_unimported_bad_row_on_reimport(tmp_path):
    tui = tmp_path / "tui"
    tui.mkdir()
    (tui / "queue.json").write_text(
        json.dumps({"schema": 1, "jobs": [{"outputs": 7}, job_dict()]}), encoding="utf-8")
    gui = tmp_path / "gui"
    imported, _, errors = import_tui_queue(tui, gui)
    assert imported == 1 and len(errors) == 1
    imported2, _, _ = import_tui_queue(tui, gui)
    assert imported2 == 0


def test_presets_round_trip(tmp_path):
    save_preset("fast", Job.from_dict(job_dict()), meta("Fast", "h9"), tmp_path)
    presets = load_presets(tmp_path)
    assert set(presets) == {"fast"}
    job, m = presets["fast"]
    assert isinstance(job, Job)
    assert m == meta("Fast", "h9")


def test_presets_corrupt_quarantines(tmp_path):
    (tmp_path / "presets.json").write_text("[[[", encoding="utf-8")
    assert load_presets(tmp_path) == {}
    assert list(tmp_path.glob("presets.json.bad*"))


def test_gui_dirs_are_ffgui_owned():
    cache, config = gui_dirs()
    assert cache.name == "ffgui" and config.name == "ffgui"
    assert cache != config


def test_overwrite_preserves_nothing_stale(tmp_path):
    save_queue([(Job.from_dict(job_dict()), meta())], tmp_path)
    save_queue([], tmp_path)
    assert load_queue(tmp_path) == []


def test_save_preset_quarantines_corrupt_store(tmp_path):
    (tmp_path / "presets.json").write_text("{not json", encoding="utf-8")
    save_preset("fresh", Job(), {}, tmp_path)
    assert list(tmp_path.glob("presets.json.bad*"))
    assert set(load_presets(tmp_path)) == {"fresh"}


def test_round_trip_two_pass_chapters_concat_unicode(tmp_path):
    from fftui.model import Input, Output
    chaptered = Job.from_dict({
        "inputs": [{"path": "a.mp4", "concat_paths": ["b.mp4", "c.mp4"]}],
        "outputs": [{"path": "o.mp4",
                     "chapters": [{"start": "0", "title": "Intro", "lang": "eng"}]}],
        "two_pass": True,
    })
    uni = Job(inputs=[Input(path="vidéó — 测试.mp4")],
              outputs=[Output(path="sörtie_out.mp4")])
    uni_meta = {"name": "Ünï", "enabled": True, "notes": "héllo",
                "source_hash": "h2"}
    rows = [(chaptered, meta("Ch", "h1")), (uni, uni_meta)]
    save_queue(rows, tmp_path)
    loaded = load_queue(tmp_path)
    assert len(loaded) == 2
    job, m = loaded[0]
    assert job.two_pass is True
    assert job.inputs[0].concat_paths == ["b.mp4", "c.mp4"]
    assert [(c.start, c.title) for c in job.outputs[0].chapters] == [("0", "Intro")]
    assert m == meta("Ch", "h1")
    job, m = loaded[1]
    assert job.inputs[0].path == "vidéó — 测试.mp4"
    assert job.outputs[0].path == "sörtie_out.mp4"
    assert m == uni_meta


def test_round_trip_kitchen_sink(tmp_path):
    from fftui.model import FilterChain, Input, Output
    job = Job(
        inputs=[Input(path="a.mp4", decoder_v="h264_cuvid",
                      input_args=["-ss", "5"])],
        outputs=[Output(path="o.mp4", video_codec="libx264",
                        stream_metadata={"v:0:language": "eng"},
                        dispositions={"v:0": "default"},
                        bsf={"v:0": "h264_mp4toannexb"},
                        segment_enabled=True, segment_time="60",
                        tee_spec="[f=mp4]a.mp4|[f=mpegts]b.ts")],
        video_filters=FilterChain(filters=["scale=64:64"]),
        filter_hw_device="cuda:0",
    )
    disabled = {"name": "K", "enabled": False, "notes": "",
                "source_hash": "hk"}
    save_queue([(job, disabled)], tmp_path)
    (loaded_job, m), = load_queue(tmp_path)
    assert loaded_job.to_dict() == job.to_dict()
    assert m == disabled


def test_missing_queue_file_loads_empty(tmp_path):
    assert load_queue(tmp_path / "fresh") == []


def test_non_utf8_queue_file_quarantines(tmp_path):
    (tmp_path / "queue.json").write_bytes(b"\xff\xfe\x00bad")
    assert load_queue(tmp_path) == []
    assert list(tmp_path.glob("queue.json.bad*"))


def test_save_preset_quarantines_schema_mismatch(tmp_path):
    raw = {"schema": 1, "presets": {"old": {"job": job_dict(), "gui": {}}}}
    (tmp_path / "presets.json").write_text(json.dumps(raw), encoding="utf-8")
    save_preset("fresh", Job(), {}, tmp_path)
    bad = list(tmp_path.glob("presets.json.bad*"))
    assert bad and "old" in bad[0].read_text(encoding="utf-8")
    assert set(load_presets(tmp_path)) == {"fresh"}


def test_unparsed_row_save_load_save_is_byte_stable(tmp_path):
    """Rows this build cannot parse ride through untouched, byte-identical."""
    save_queue([(UnparsedRow({"outputs": 7}), meta("broken"))], tmp_path)
    before = (tmp_path / "queue.json").read_bytes()
    loaded = load_queue(tmp_path)
    assert isinstance(loaded[0][0], UnparsedRow)
    save_queue(loaded, tmp_path)
    assert (tmp_path / "queue.json").read_bytes() == before


def test_hostile_meta_values_coerce_to_shape(tmp_path):
    """Value-type drift must not survive load: a non-string name crashes the queue
    table and a string "false" is truthy, wrongly enabling the row."""
    rows = [{"job": job_dict(), "gui": {"name": ["x"], "enabled": "false",
                                        "notes": None, "source_hash": 7,
                                        "EXTRA": 1}}]
    (tmp_path / "queue.json").write_text(
        json.dumps({"schema": GUI_SCHEMA, "rows": rows}), encoding="utf-8")
    (loaded_job, m), = load_queue(tmp_path)
    assert isinstance(loaded_job, Job)
    assert m == {"name": "", "enabled": False, "notes": "", "source_hash": ""}
    save_queue(load_queue(tmp_path), tmp_path)
    on_disk = json.loads((tmp_path / "queue.json").read_text(encoding="utf-8"))
    assert on_disk["rows"][0]["gui"] == m
    assert b"EXTRA" not in (tmp_path / "queue.json").read_bytes()
    assert load_queue(tmp_path)[0][1] == m


def test_enabled_string_parsing(tmp_path):
    """String enabled flags parse (no bool("false") == True trap)."""
    from ffgui.store import _meta
    assert _meta({"enabled": "false"})["enabled"] is False
    assert _meta({"enabled": "0"})["enabled"] is False
    assert _meta({"enabled": "yes"})["enabled"] is True
    assert _meta({"enabled": True})["enabled"] is True
    assert _meta({})["enabled"] is True
    assert _meta({"name": 5})["name"] == ""


def test_enabled_nonscalar_falls_back_to_default():
    """Non-scalar hostiles (list/dict) have no boolean meaning: bool([]) is
    False, so truthiness coercion flipped a default-enabled row off."""
    from ffgui.store import _meta
    assert _meta({"enabled": []})["enabled"] is True
    assert _meta({"enabled": {"x": 1}})["enabled"] is True
    assert _meta({"enabled": 0})["enabled"] is False
    assert _meta({"enabled": 1})["enabled"] is True


def test_queue_non_dict_row_quarantines(tmp_path):
    """A row that is not an object is envelope corruption, not a skippable row."""
    (tmp_path / "queue.json").write_text(
        json.dumps({"schema": GUI_SCHEMA,
                    "rows": [{"job": job_dict(), "gui": meta()}, 42]}),
        encoding="utf-8")
    assert load_queue(tmp_path) == []
    assert list(tmp_path.glob("queue.json.bad*"))


def test_presets_bad_entry_quarantines_loudly(tmp_path):
    """One unparseable preset must not vanish silently; bytes ride to .bad."""
    raw = {"schema": GUI_SCHEMA, "presets": {
        "good": {"job": job_dict(), "gui": meta()},
        "bad": {"job": {"outputs": 7}, "gui": meta()},
        "naked": "not-a-dict"}}
    (tmp_path / "presets.json").write_text(json.dumps(raw), encoding="utf-8")
    assert load_presets(tmp_path) == {}
    bad = list(tmp_path.glob("presets.json.bad*"))
    assert bad and "good" in bad[0].read_text(encoding="utf-8")


def test_import_tui_non_dict_row_reports_numbered_error(tmp_path):
    """Non-object TUI rows are loud errors with accurate numbers, not silent drops."""
    tui = tmp_path / "tui"
    tui.mkdir()
    (tui / "queue.json").write_text(
        json.dumps([job_dict(), 42, {"outputs": 7}]), encoding="utf-8")
    imported, skipped, errors = import_tui_queue(tui, tmp_path / "gui")
    assert imported == 1 and skipped == 0
    assert len(errors) == 2
    assert errors[0].startswith("row 2") and errors[1].startswith("row 3")


def test_truthy_parsing_has_one_owner():
    """store.is_truthy is the single parser behind queue meta and Job flags."""
    from ffgui.doc import _coerce_global
    from ffgui.store import fresh_meta, is_truthy
    for word in ("1", "true", "yes", "on", " True ", "ON"):
        assert is_truthy(word) is True, word
    for word in ("", "0", "false", "no", "off", "2"):
        assert is_truthy(word) is False, word
    assert _coerce_global("noconfirm", "on") is True
    assert _coerce_global("noconfirm", "0") is False
    assert fresh_meta("Row") == {"name": "Row", "enabled": True, "notes": "",
                                 "source_hash": ""}


def test_save_normalizes_gui_meta_shape(tmp_path):
    """Extra meta keys must not persist: save->load->save is byte-identical."""
    rows = [(Job.from_dict(job_dict()), {**meta("First", "h1"), "EXTRA": "x"})]
    save_queue(rows, tmp_path)
    on_disk = json.loads((tmp_path / "queue.json").read_text(encoding="utf-8"))
    assert on_disk["rows"][0]["gui"] == meta("First", "h1")
    before = (tmp_path / "queue.json").read_bytes()
    save_queue(load_queue(tmp_path), tmp_path)
    assert (tmp_path / "queue.json").read_bytes() == before
