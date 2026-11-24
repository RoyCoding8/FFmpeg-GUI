#!/usr/bin/env python3
"""Formal verification suite for FFMpeg-GUI store durability & boundary invariants.

Checks 4 formal properties directly tied to real src/ffgui/store.py:
  S1: import_tui_queue idempotency, content-hash deduplication & 1-based error index invariant
      (Z3 SMT over bounded TUI queues up to 5 rows + real import_tui_queue cross-check)
  S2: Atomic save_queue / save_preset durability, stale temp sweeping & quarantine byte preservation
      (Exhaustive crash-injection model check over real store.py + fftui.util.atomic)
  S3: UnparsedRow byte-exact preservation across save/load/reorder cycles
      (Exhaustive real-code permutation & multi-cycle check over save_queue / load_queue)
  S4: _meta(raw) boundary sanitization total function & idempotency
      (Exhaustive model check over all key subsets, extra keys, and hostile value types)

Run:
  QT_QPA_PLATFORM=offscreen PYTHONPATH="$PWD/src" .venv/bin/python verification/verify_store_crash.py
"""

from __future__ import annotations

import argparse
import copy
import itertools
import json
import os
import sys
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import z3
from fftui.model import Input, Job, Output
from fftui.util import atomic as fftui_atomic

from ffgui.store import (
    GUI_SCHEMA,
    TRUTHY,
    UnparsedRow,
    _meta,
    delete_preset,
    import_tui_queue,
    load_presets,
    load_queue,
    save_preset,
    save_queue,
    source_hash,
)

Z3_TIMEOUT_MS = 5000
EXPECTED_META_KEYS = frozenset({"name", "enabled", "notes", "source_hash"})


@dataclass
class PropertyResult:
    prop_id: str
    title: str
    status: str  # "PASS", "FAIL", "FINDINGS"
    tool: str
    bounds: str
    states_checked: int
    elapsed_s: float
    details: list[str] = field(default_factory=list)
    counterexamples: list[str] = field(default_factory=list)


def _new_solver() -> z3.Solver:
    s = z3.Solver()
    s.set("timeout", Z3_TIMEOUT_MS)
    return s


def _sample_job(tag: str) -> Job:
    """Create a valid minimal Job distinguishable by tag."""
    return Job(
        inputs=[Input(path=f"/media/{tag}_in.mp4")],
        outputs=[Output(path=f"/media/{tag}_out.mp4")],
    )


def _canonical_json_bytes(obj: object) -> bytes:
    """Canonical UTF-8 JSON representation that distinguishes bool vs int vs float vs None."""
    return json.dumps(obj, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode(
        "utf-8"
    )


# ---------------------------------------------------------------------------
# S1: Z3 SMT + Real Code — import_tui_queue Idempotency & Content-Hash Dedup
# ---------------------------------------------------------------------------
def verify_s1_import_idempotency() -> PropertyResult:
    t0 = time.perf_counter()
    details: list[str] = []
    counterexamples: list[str] = []

    max_rows = 5
    # Abstract state count: for each N in 1..5, init_seen has 2^N subsets,
    # each of N rows has (is_obj: 2) * (is_valid: 2) * (hash_bucket: N) choices.
    total_space = sum((2**n) * ((4 * n) ** n) for n in range(1, max_rows + 1))

    for n in range(1, max_rows + 1):
        h_buckets = n
        is_obj = [z3.Bool(f"obj_{n}_{i}") for i in range(n)]
        is_valid = [z3.Bool(f"val_{n}_{i}") for i in range(n)]
        hashes = [z3.Int(f"h_{n}_{i}") for i in range(n)]
        init_seen = [z3.Bool(f"init_{n}_{c}") for c in range(h_buckets)]

        domain = [z3.And(h >= 0, h < h_buckets) for h in hashes]

        def _simulate_run(
            start_seen: list[z3.BoolRef],
        ) -> tuple[
            list[z3.BoolRef],
            list[z3.BoolRef],
            z3.ArithRef,
            list[z3.ArithRef],
        ]:
            cur_seen = list(start_seen)
            imp_flags: list[z3.BoolRef] = []
            err_indices: list[z3.ArithRef] = []
            row_cursor: z3.ArithRef = z3.IntVal(0)
            for i in range(n):
                row_cursor = row_cursor + 1
                err_indices.append(row_cursor)
                in_seen = z3.Or(
                    *[z3.And(hashes[i] == c, cur_seen[c]) for c in range(h_buckets)]
                )
                imp_i = z3.And(is_obj[i], z3.Not(in_seen), is_valid[i])
                imp_flags.append(imp_i)
                cur_seen = [
                    z3.Or(cur_seen[c], z3.And(imp_i, hashes[i] == c))
                    for c in range(h_buckets)
                ]
            imported_count = z3.Sum(*[z3.If(f, 1, 0) for f in imp_flags])
            return cur_seen, imp_flags, imported_count, err_indices

        seen_after_1, imp_1, _imported_1, err_idx_1 = _simulate_run(init_seen)
        # Note: when imported_1 == 0, seen_after_1 == init_seen automatically,
        # matching `if imported: write_text_atomic(...)` in import_tui_queue.
        _seen_after_2, _imp_2, imported_2, err_idx_2 = _simulate_run(seen_after_1)

        # (a) UNSAT: running import_tui_queue twice imports > 0 rows on the 2nd run
        s_a = _new_solver()
        s_a.add(*domain, imported_2 > 0)
        if s_a.check() != z3.unsat:
            counterexamples.append(f"S1(a) N={n} not UNSAT: {s_a.check()}")

        # (b) UNSAT: a duplicate job dict inside the same TUI queue is imported twice
        dup_clauses = [
            z3.And(is_obj[i], is_obj[j], hashes[i] == hashes[j], imp_1[i], imp_1[j])
            for i in range(n)
            for j in range(i + 1, n)
        ]
        if dup_clauses:
            s_b = _new_solver()
            s_b.add(*domain, z3.Or(*dup_clauses))
            if s_b.check() != z3.unsat:
                counterexamples.append(f"S1(b) N={n} not UNSAT: {s_b.check()}")

        # (c) UNSAT: non-object row error indices shift from exact 1-based position (i + 1)
        shift_clauses = [
            z3.And(
                z3.Not(is_obj[i]),
                z3.Or(err_idx_1[i] != i + 1, err_idx_2[i] != i + 1),
            )
            for i in range(n)
        ]
        s_c = _new_solver()
        s_c.add(*domain, z3.Or(*shift_clauses))
        if s_c.check() != z3.unsat:
            counterexamples.append(f"S1(c) N={n} not UNSAT: {s_c.check()}")

    if not counterexamples:
        details.append(
            f"S1(a)(b)(c) UNSAT across all N=1..{max_rows} ({total_space:,} abstract TUI queue + prior-import states)"
        )

    # Real-code cross-check against ffgui.store.import_tui_queue
    real_cases = 0
    job_a = _sample_job("alpha").to_dict()
    job_a_reordered = dict(reversed(list(job_a.items())))
    job_b = _sample_job("beta").to_dict()
    invalid_job = {"inputs": "not-a-list", "outputs": []}

    with tempfile.TemporaryDirectory(prefix="fv_s1_") as tmp:
        root = Path(tmp)

        # Case 1: List-format TUI queue with non-objects, duplicates (including key-reordered),
        # invalid job dict, and repeated runs (run 1, run 2, run 3)
        real_cases += 1
        tui_1 = root / "tui_1"
        gui_1 = root / "gui_1"
        tui_1.mkdir()
        gui_1.mkdir()
        mixed_rows = [
            42,               # row 1: non-object
            job_a,            # row 2: valid (import)
            "str-row",        # row 3: non-object
            job_a_reordered,  # row 4: duplicate of row 2 with different key order (skip)
            invalid_job,      # row 5: invalid Job dict (error)
            job_b,            # row 6: valid (import)
            [1, 2, 3],        # row 7: non-object
        ]
        (tui_1 / "queue.json").write_text(json.dumps(mixed_rows), encoding="utf-8")

        imp1, skip1, err1 = import_tui_queue(tui_1, gui_1)
        if (imp1, skip1) != (2, 1):
            counterexamples.append(f"Case 1 run 1: expected (2, 1), got ({imp1}, {skip1})")
        expected_non_obj = ["row 1: not an object", "row 3: not an object", "row 7: not an object"]
        for expected in expected_non_obj:
            if expected not in err1:
                counterexamples.append(f"Case 1 run 1 missing error {expected!r} in {err1!r}")
        if not any(e.startswith("row 5: ") for e in err1):
            counterexamples.append(f"Case 1 run 1 missing row 5 invalid-job error in {err1!r}")

        imp2, skip2, err2 = import_tui_queue(tui_1, gui_1)
        if (imp2, skip2) != (0, 3):
            counterexamples.append(f"Case 1 run 2: expected (0, 3), got ({imp2}, {skip2})")
        if err2 != err1:
            counterexamples.append(f"Case 1 run 2 errors shifted: {err2!r} != {err1!r}")

        imp3, skip3, err3 = import_tui_queue(tui_1, gui_1)
        if (imp3, skip3) != (0, 3) or err3 != err1:
            counterexamples.append(f"Case 1 run 3 drifted: ({imp3}, {skip3}, {err3!r})")

        loaded_1 = load_queue(gui_1)
        if len(loaded_1) != 2:
            counterexamples.append(f"Case 1 queue length expected 2, got {len(loaded_1)}")

        # Case 2: Schema-wrapped TUI queue {"schema": 1, "jobs": [...]} with partial prior import
        real_cases += 1
        tui_2 = root / "tui_2"
        gui_2 = root / "gui_2"
        tui_2.mkdir()
        gui_2.mkdir()
        save_queue(
            [(Job.from_dict(job_a), {"name": "alpha", "source_hash": source_hash(job_a)})],
            gui_2,
        )
        wrapped = {"schema": 1, "jobs": [None, job_a, job_b, True, job_b]}
        (tui_2 / "queue.json").write_text(json.dumps(wrapped), encoding="utf-8")

        imp_p1, skip_p1, err_p1 = import_tui_queue(tui_2, gui_2)
        if (imp_p1, skip_p1) != (1, 2):
            counterexamples.append(
                f"Case 2 partial prior run 1: expected (1, 2), got ({imp_p1}, {skip_p1})"
            )
        if err_p1 != ["row 1: not an object", "row 4: not an object"]:
            counterexamples.append(f"Case 2 partial prior errors mismatch: {err_p1!r}")

        imp_p2, skip_p2, err_p2 = import_tui_queue(tui_2, gui_2)
        if (imp_p2, skip_p2) != (0, 3) or err_p2 != err_p1:
            counterexamples.append(
                f"Case 2 partial prior run 2 not idempotent: ({imp_p2}, {skip_p2}, {err_p2!r})"
            )

        # Case 3: Hostile / malformed TUI queue files (bool schema, truncated JSON, wrong type)
        for label, payload in [
            ("bool_schema", json.dumps({"schema": True, "jobs": [job_a]})),
            ("truncated_json", '{"schema": 1, "jobs": ['),
            ("wrong_type", json.dumps({"unrelated": 123})),
        ]:
            real_cases += 1
            tui_bad = root / f"tui_{label}"
            gui_bad = root / f"gui_{label}"
            tui_bad.mkdir()
            gui_bad.mkdir()
            (tui_bad / "queue.json").write_text(payload, encoding="utf-8")
            ib, sb, eb = import_tui_queue(tui_bad, gui_bad)
            if ib != 0 or sb != 0 or not eb:
                counterexamples.append(
                    f"Case 3 ({label}): expected (0, 0, [error]), got ({ib}, {sb}, {eb!r})"
                )

    details.append(
        f"Cross-checked {real_cases} real import_tui_queue scenarios "
        "(multi-run idempotency, key-order-invariant hashing, partial prior import, 1-based non-object indices)"
    )
    return PropertyResult(
        prop_id="S1",
        title="import_tui_queue idempotency & content-hash dedup (store.py:import_tui_queue)",
        status="FAIL" if counterexamples else "PASS",
        tool=f"Z3 SMT ({z3.get_version_string()}) + real import_tui_queue cross-check",
        bounds=f"All TUI queues N=1..{max_rows} ({total_space:,} abstract states) + {real_cases} real multi-run scenarios",
        states_checked=total_space + real_cases,
        elapsed_s=time.perf_counter() - t0,
        details=details,
        counterexamples=counterexamples,
    )


# ---------------------------------------------------------------------------
# S2: Exhaustive Crash-Injection Model Check — Atomic save_queue & save_preset
# ---------------------------------------------------------------------------
def verify_s2_crash_durability() -> PropertyResult:
    t0 = time.perf_counter()
    details: list[str] = []
    hard_failures: list[str] = []
    findings: list[str] = []
    checks = 0

    job_1 = _sample_job("durable_1")
    job_2 = _sample_job("durable_2")

    with tempfile.TemporaryDirectory(prefix="fv_s2_") as tmp:
        root = Path(tmp)

        # (1) Stale temp files swept without corrupting committed data; fresh temps left intact
        for kind in ("queue", "presets"):
            checks += 1
            d = root / f"stale_{kind}"
            d.mkdir()
            if kind == "queue":
                save_queue([(job_1, {"name": "q1"})], d)
                base_name = "queue.json"
            else:
                save_preset("p1", job_1, {"name": "p1"}, d)
                base_name = "presets.json"

            stale_a = d / f"{base_name}.crash1.tmp"
            stale_b = d / f"{base_name}.crash2.tmp"
            fresh = d / f"{base_name}.active.tmp"
            for p in (stale_a, stale_b, fresh):
                p.write_text("{partial write", encoding="utf-8")
            old_ts = time.time() - 7200.0
            os.utime(stale_a, (old_ts, old_ts))
            os.utime(stale_b, (old_ts, old_ts))

            if kind == "queue":
                rows = load_queue(d)
                if len(rows) != 1 or not isinstance(rows[0][0], Job):
                    hard_failures.append(f"Stale temp sweep corrupted queue: {rows!r}")
            else:
                presets = load_presets(d)
                if list(presets.keys()) != ["p1"]:
                    hard_failures.append(f"Stale temp sweep corrupted presets: {presets!r}")

            if stale_a.exists() or stale_b.exists():
                hard_failures.append(f"Stale {base_name}.*.tmp files were not swept")
            if not fresh.exists():
                hard_failures.append(f"Fresh {base_name}.active.tmp was prematurely deleted")

        # (2) Simulated mid-write crash (os.replace fails with OSError):
        #     committed data stays intact and no temp file leaks
        for kind in ("queue", "presets"):
            checks += 1
            d = root / f"midcrash_{kind}"
            d.mkdir()
            if kind == "queue":
                save_queue([(job_1, {"name": "committed_q"})], d)
                base_name = "queue.json"
            else:
                save_preset("committed_p", job_1, {"name": "committed_p"}, d)
                base_name = "presets.json"

            orig_replace = fftui_atomic._replace

            def _failing_replace(_src, _dst):
                raise OSError("simulated power loss during atomic rename")

            fftui_atomic._replace = _failing_replace
            try:
                raised = False
                try:
                    if kind == "queue":
                        save_queue([(job_2, {"name": "uncommitted"})], d)
                    else:
                        save_preset("uncommitted", job_2, {"name": "uncommitted"}, d)
                except OSError:
                    raised = True
            finally:
                fftui_atomic._replace = orig_replace

            if not raised:
                hard_failures.append(f"Mid-write crash on {kind} did not raise OSError")
            leaked_tmps = list(d.glob(f"{base_name}.*.tmp"))
            if leaked_tmps:
                hard_failures.append(f"Mid-write crash on {kind} leaked temps: {leaked_tmps}")
            if kind == "queue":
                rows = load_queue(d)
                if len(rows) != 1 or rows[0][1]["name"] != "committed_q":
                    hard_failures.append(f"Mid-write crash corrupted committed queue: {rows!r}")
            else:
                presets = load_presets(d)
                if list(presets.keys()) != ["committed_p"]:
                    hard_failures.append(
                        f"Mid-write crash corrupted committed presets: {presets!r}"
                    )

        # (3) Exhaustive hostile / corrupt payloads quarantined with exact original bytes preserved
        queue_corrupt_payloads: list[tuple[str, bytes]] = [
            ("truncated_json", b'{"schema": 2, "rows": ['),
            ("invalid_utf8", b"\xff\xfe\x00\x80invalid"),
            ("empty_file", b""),
            ("top_list", b"[]"),
            ("top_str", b'"hello"'),
            ("top_int", b"42"),
            ("top_null", b"null"),
            ("wrong_schema_1", json.dumps({"schema": 1, "rows": []}).encode()),
            ("wrong_schema_99", json.dumps({"schema": 99, "rows": []}).encode()),
            ("bool_schema", json.dumps({"schema": True, "rows": []}).encode()),
            ("missing_rows", json.dumps({"schema": GUI_SCHEMA}).encode()),
            ("extra_key", json.dumps({"schema": GUI_SCHEMA, "rows": [], "extra": 1}).encode()),
            ("rows_not_list", json.dumps({"schema": GUI_SCHEMA, "rows": {}}).encode()),
            ("row_not_dict", json.dumps({"schema": GUI_SCHEMA, "rows": [123]}).encode()),
        ]
        for label, raw_bytes in queue_corrupt_payloads:
            checks += 1
            d = root / f"q_bad_{label}"
            d.mkdir()
            qpath = d / "queue.json"
            qpath.write_bytes(raw_bytes)
            rows = load_queue(d)
            if rows != []:
                hard_failures.append(f"queue ({label}): expected [], got {rows!r}")
            quarantined = sorted(d.glob("queue.json.bad*"))
            if qpath.exists() or len(quarantined) != 1:
                msg = (
                    f"load_queue on queue.json={raw_bytes!r} ({label}): "
                    f"file left unquarantined (exists={qpath.exists()}, bad_files={len(quarantined)}) — "
                    "_read_json() returns None for JSON 'null', colliding with its read-error sentinel"
                )
                if label == "top_null":
                    findings.append(msg)
                else:
                    hard_failures.append(msg)
            elif quarantined[0].read_bytes() != raw_bytes:
                hard_failures.append(f"queue ({label}): quarantine bytes mutated")

        presets_corrupt_payloads: list[tuple[str, bytes]] = [
            ("truncated_json", b'{"schema": 2, "presets": {'),
            ("invalid_utf8", b"\xff\xfe\x80"),
            ("empty_file", b""),
            ("top_list", b"[]"),
            ("top_str", b'"bad"'),
            ("top_int", b"7"),
            ("top_null", b"null"),
            ("wrong_schema_1", json.dumps({"schema": 1, "presets": {}}).encode()),
            ("bool_schema", json.dumps({"schema": True, "presets": {}}).encode()),
            ("missing_presets", json.dumps({"schema": GUI_SCHEMA}).encode()),
            ("extra_key", json.dumps({"schema": GUI_SCHEMA, "presets": {}, "x": 1}).encode()),
            ("presets_not_dict", json.dumps({"schema": GUI_SCHEMA, "presets": []}).encode()),
        ]
        for label, raw_bytes in presets_corrupt_payloads:
            checks += 1
            d = root / f"p_bad_{label}"
            d.mkdir()
            ppath = d / "presets.json"
            ppath.write_bytes(raw_bytes)
            presets = load_presets(d)
            if presets != {}:
                hard_failures.append(f"presets ({label}): expected {{}}, got {presets!r}")
            quarantined = sorted(d.glob("presets.json.bad*"))
            if ppath.exists() or len(quarantined) != 1:
                msg = (
                    f"load_presets on presets.json={raw_bytes!r} ({label}): "
                    f"file left unquarantined (exists={ppath.exists()}, bad_files={len(quarantined)}) — "
                    "_read_json() returns None for JSON 'null', colliding with its read-error sentinel"
                )
                if label == "top_null":
                    findings.append(msg)
                else:
                    hard_failures.append(msg)
            elif quarantined[0].read_bytes() != raw_bytes:
                hard_failures.append(f"presets ({label}): quarantine bytes mutated")

        # (4) Sequential quarantines preserve all prior .bad, .bad.1, .bad.2 without overwrite
        checks += 1
        d_seq = root / "seq_quarantine"
        d_seq.mkdir()
        seq_payloads = [b"{bad0", b"{bad1", b"{bad2"]
        for payload in seq_payloads:
            (d_seq / "queue.json").write_bytes(payload)
            load_queue(d_seq)
        bad_files = sorted(d_seq.glob("queue.json.bad*"))
        if len(bad_files) != 3:
            hard_failures.append(f"Sequential quarantine expected 3 files, got {len(bad_files)}")
        else:
            preserved = sorted(p.read_bytes() for p in bad_files)
            if preserved != sorted(seq_payloads):
                hard_failures.append(f"Sequential quarantine lost bytes: {preserved!r}")

        # (5) save_preset & delete_preset on top of corrupt presets.json quarantine before writing
        for op_name, corrupt_label, corrupt_bytes in [
            ("save_preset", "wrong_schema", b'{"schema": 1, "presets": "corrupt"}'),
            ("delete_preset", "wrong_schema", b'{"schema": 1, "presets": "corrupt"}'),
            ("save_preset", "top_null", b"null"),
        ]:
            checks += 1
            d_op = root / f"corrupt_write_{op_name}_{corrupt_label}"
            d_op.mkdir()
            (d_op / "presets.json").write_bytes(corrupt_bytes)
            if op_name == "save_preset":
                save_preset("fresh", job_1, {"name": "fresh"}, d_op)
                loaded_p = load_presets(d_op)
                if list(loaded_p.keys()) != ["fresh"]:
                    hard_failures.append(f"{op_name} over {corrupt_label} failed: {loaded_p!r}")
            else:
                delete_preset("missing", d_op)
                loaded_p = load_presets(d_op)
                if loaded_p != {}:
                    hard_failures.append(f"{op_name} over {corrupt_label} failed: {loaded_p!r}")
            bad_p = list(d_op.glob("presets.json.bad*"))
            if len(bad_p) != 1 or bad_p[0].read_bytes() != corrupt_bytes:
                msg = (
                    f"{op_name} over presets.json={corrupt_bytes!r} ({corrupt_label}): "
                    f"silently overwrote corrupt file without quarantine (bad_files={len(bad_p)}) — "
                    "_write_presets() skips quarantine_or_raise when _read_json() returns None"
                )
                if corrupt_label == "top_null":
                    findings.append(msg)
                else:
                    hard_failures.append(msg)

        # (6) One corrupt entry inside presets.json quarantines the WHOLE file (never drops a preset)
        valid_entry_1 = {"job": job_1.to_dict(), "gui": _meta({"name": "valid_1"})}
        valid_entry_2 = {"job": job_2.to_dict(), "gui": _meta({"name": "valid_2"})}
        corrupt_entries: list[tuple[str, object]] = [
            ("entry_str", "not-a-dict"),
            ("entry_null", None),
            ("entry_missing_job", {"gui": {}}),
            ("entry_job_list", {"job": [1, 2], "gui": {}}),
            ("entry_invalid_job_inputs", {"job": {"inputs": "not-a-list"}, "gui": {}}),
        ]
        for label, bad_entry in corrupt_entries:
            checks += 1
            d_mix = root / f"p_one_bad_{label}"
            d_mix.mkdir()
            doc = {
                "schema": GUI_SCHEMA,
                "presets": {
                    "valid_1": valid_entry_1,
                    "corrupt_one": bad_entry,
                    "valid_2": valid_entry_2,
                },
            }
            raw_bytes = json.dumps(doc, indent=2).encode("utf-8")
            ppath = d_mix / "presets.json"
            ppath.write_bytes(raw_bytes)
            loaded = load_presets(d_mix)
            if loaded != {}:
                hard_failures.append(
                    f"presets one-corrupt-entry ({label}): silently dropped corrupt entry and returned {list(loaded.keys())!r}"
                )
            if ppath.exists():
                hard_failures.append(
                    f"presets one-corrupt-entry ({label}): file was not quarantined"
                )
            bad_files = list(d_mix.glob("presets.json.bad*"))
            if len(bad_files) != 1 or bad_files[0].read_bytes() != raw_bytes:
                hard_failures.append(
                    f"presets one-corrupt-entry ({label}): original bytes not preserved in quarantine"
                )

    all_cex = [*hard_failures, *findings]
    status = "FAIL" if hard_failures else ("FINDINGS" if findings else "PASS")
    details.append(
        f"Verified {checks} crash-injection & quarantine scenarios across save_queue/load_queue/save_preset/delete_preset/load_presets"
    )
    details.append(
        f"Passed {checks - len(all_cex)}/{checks} scenarios; "
        f"real store.py findings discovered: {len(findings)}"
    )
    return PropertyResult(
        prop_id="S2",
        title="Atomic save_queue & save_preset durability and quarantine (store.py)",
        status=status,
        tool="Exhaustive crash-injection model check over real store.py + fftui.util.atomic",
        bounds=f"{checks} crash, stale-temp, corrupt-payload, and partial-preset-corruption scenarios",
        states_checked=checks,
        elapsed_s=time.perf_counter() - t0,
        details=details,
        counterexamples=all_cex,
    )


# ---------------------------------------------------------------------------
# S3: Exhaustive Real-Code Check — UnparsedRow Byte-Exact Preservation
# ---------------------------------------------------------------------------
def verify_s3_unparsed_preservation() -> PropertyResult:
    t0 = time.perf_counter()
    details: list[str] = []
    counterexamples: list[str] = []
    cycles_checked = 0

    healthy_a = _sample_job("healthy_a")
    healthy_b = _sample_job("healthy_b")

    # Diverse unparseable payloads: nested dicts, unknown schema fields, unicode, ints, floats,
    # booleans, nulls, and non-dict legacy payloads (all rejected by Job.from_dict).
    unparsed_payloads: list[tuple[str, object]] = [
        (
            "nested_future_unicode",
            {
                "inputs": "v9-graph-ref",
                "future_schema": 9,
                "nested": {
                    "unicode": "café_日本語_🎬",
                    "nums": [0, -1, 42, 3.14159, 1e12],
                    "flags": [True, False, None],
                    "deep": {"a": {"b": {"c": []}}},
                },
            },
        ),
        (
            "invalid_scalar_mixed",
            {
                "stats": "always",
                "unknown_key": {"k1": True, "k2": 1, "k3": False, "k4": 0, "k5": None},
                "empty_obj": {},
                "empty_arr": [],
            },
        ),
        ("non_dict_list", ["legacy", 1, 2.5, True, False, None, {"u": "Ω"}]),
        ("non_dict_str", "opaque-v9-blob-日本語"),
        ("non_dict_int", 9001),
        ("non_dict_null", None),
    ]

    with tempfile.TemporaryDirectory(prefix="fv_s3_") as tmp:
        root = Path(tmp)

        # (1) All 6 UnparsedRow payloads interleaved with healthy Jobs across 2 save/load cycles
        base_rows: list[tuple[str, Job | UnparsedRow, dict]] = [
            ("job_a", healthy_a, {"name": "healthy_a", "enabled": True, "notes": "ok", "source_hash": ""}),
            ("job_b", healthy_b, {"name": "healthy_b", "enabled": False, "notes": "n", "source_hash": "abc"}),
        ]
        for label, payload in unparsed_payloads:
            base_rows.append(
                (
                    label,
                    UnparsedRow(copy.deepcopy(payload)),  # type: ignore[arg-type]
                    {"name": label, "enabled": True, "notes": f"raw:{label}", "source_hash": ""},
                )
            )

        d_all = root / "all_payloads"
        d_all.mkdir()
        initial_rows = [(item, meta) for _, item, meta in base_rows]
        save_queue(initial_rows, d_all)
        loaded_1 = load_queue(d_all)
        reordered = list(reversed(loaded_1))
        save_queue(reordered, d_all)
        loaded_2 = load_queue(d_all)
        cycles_checked += 2

        expected_rev = list(reversed(base_rows))
        if len(loaded_2) != len(expected_rev):
            counterexamples.append(
                f"All-payloads cycle: expected {len(expected_rev)} rows, got {len(loaded_2)}"
            )
        else:
            for idx, ((label, orig_item, orig_meta), (got_item, got_meta)) in enumerate(
                zip(expected_rev, loaded_2)
            ):
                if isinstance(orig_item, UnparsedRow):
                    if not isinstance(got_item, UnparsedRow):
                        counterexamples.append(
                            f"Row {idx} ({label}) mutated from UnparsedRow to {type(got_item).__name__}"
                        )
                    elif _canonical_json_bytes(got_item.raw) != _canonical_json_bytes(
                        orig_item.raw
                    ):
                        counterexamples.append(
                            f"Row {idx} ({label}) raw payload mutated: {got_item.raw!r} != {orig_item.raw!r}"
                        )
                else:
                    if not isinstance(got_item, Job) or got_item.to_dict() != orig_item.to_dict():
                        counterexamples.append(f"Row {idx} ({label}) healthy Job mutated")
                if got_meta != orig_meta:
                    counterexamples.append(f"Row {idx} ({label}) meta mutated: {got_meta!r}")

        # (2) Exhaustive 4! = 24 permutations of a 4-row mixed queue (2 Jobs + 2 UnparsedRows)
        #     across save_queue -> load_queue -> rotate -> save_queue -> load_queue
        four_items: list[tuple[str, Job | UnparsedRow, dict]] = [
            ("job_a", healthy_a, {"name": "a", "enabled": True, "notes": "", "source_hash": ""}),
            (
                "unp_dict",
                UnparsedRow(copy.deepcopy(unparsed_payloads[0][1])),  # type: ignore[arg-type]
                {"name": "u0", "enabled": False, "notes": "future", "source_hash": ""},
            ),
            ("job_b", healthy_b, {"name": "b", "enabled": True, "notes": "", "source_hash": ""}),
            (
                "unp_list",
                UnparsedRow(copy.deepcopy(unparsed_payloads[2][1])),  # type: ignore[arg-type]
                {"name": "u1", "enabled": True, "notes": "legacy", "source_hash": ""},
            ),
        ]
        d_perm = root / "perms"
        d_perm.mkdir()
        for perm_idx, perm in enumerate(itertools.permutations(four_items)):
            cycles_checked += 2
            save_queue([(it, m) for _, it, m in perm], d_perm)
            r1 = load_queue(d_perm)
            r1_rot = r1[1:] + r1[:1]
            save_queue(r1_rot, d_perm)
            r2 = load_queue(d_perm)

            expected_rot = [*perm[1:], perm[0]]
            if len(r2) != 4:
                counterexamples.append(f"Perm #{perm_idx}: expected 4 rows, got {len(r2)}")
                continue
            for (label, orig_item, orig_meta), (got_item, got_meta) in zip(expected_rot, r2):
                if isinstance(orig_item, UnparsedRow):
                    if not isinstance(got_item, UnparsedRow):
                        counterexamples.append(
                            f"Perm #{perm_idx} ({label}): UnparsedRow turned into {type(got_item).__name__}"
                        )
                    elif _canonical_json_bytes(got_item.raw) != _canonical_json_bytes(
                        orig_item.raw
                    ):
                        counterexamples.append(
                            f"Perm #{perm_idx} ({label}): UnparsedRow.raw mutated"
                        )
                elif not isinstance(got_item, Job) or got_item.to_dict() != orig_item.to_dict():
                    counterexamples.append(f"Perm #{perm_idx} ({label}): Job mutated")
                if got_meta != orig_meta:
                    counterexamples.append(f"Perm #{perm_idx} ({label}): meta mutated")

    details.append(
        f"Verified {cycles_checked} save_queue/load_queue cycles across 6 hostile UnparsedRow payloads "
        "and all 4! = 24 mixed-queue reorder permutations"
    )
    return PropertyResult(
        prop_id="S3",
        title="UnparsedRow byte-exact preservation across save/load/reorder (store.py)",
        status="FAIL" if counterexamples else "PASS",
        tool="Exhaustive real-code permutation & multi-cycle check over save_queue / load_queue",
        bounds=f"6 UnparsedRow payload classes + all 24 permutations of 4-row mixed queue ({cycles_checked} cycles)",
        states_checked=cycles_checked,
        elapsed_s=time.perf_counter() - t0,
        details=details,
        counterexamples=counterexamples,
    )


# ---------------------------------------------------------------------------
# S4: Exhaustive Model Check — _meta Boundary Sanitization Total Function
# ---------------------------------------------------------------------------
def _expected_enabled(val: object) -> bool:
    if isinstance(val, str):
        return val.strip().lower() in TRUTHY
    if isinstance(val, (bool, int, float)):
        return bool(val)
    return True


def verify_s4_meta_total_function() -> PropertyResult:
    t0 = time.perf_counter()
    details: list[str] = []
    counterexamples: list[str] = []
    cases_checked = 0

    def _check_one(raw: object, expected_en: bool | None = None) -> None:
        nonlocal cases_checked
        cases_checked += 1
        snapshot = copy.deepcopy(raw) if isinstance(raw, dict) else None
        try:
            out = _meta(raw)
        except Exception as exc:
            counterexamples.append(f"_meta({raw!r}) raised {type(exc).__name__}: {exc}")
            return

        if not isinstance(out, dict):
            counterexamples.append(f"_meta({raw!r}) returned non-dict {type(out).__name__}")
            return
        if frozenset(out.keys()) != EXPECTED_META_KEYS:
            counterexamples.append(f"_meta({raw!r}) keys={set(out.keys())!r}")
            return
        for skey in ("name", "notes", "source_hash"):
            if type(out[skey]) is not str:
                counterexamples.append(
                    f"_meta({raw!r})[{skey!r}] type={type(out[skey]).__name__} (val={out[skey]!r})"
                )
        if type(out["enabled"]) is not bool:
            counterexamples.append(
                f"_meta({raw!r})['enabled'] type={type(out['enabled']).__name__} (val={out['enabled']!r})"
            )
        if expected_en is not None and out["enabled"] is not expected_en:
            counterexamples.append(
                f"_meta({raw!r})['enabled']={out['enabled']!r}, expected {expected_en!r}"
            )
        if snapshot is not None and raw != snapshot:
            counterexamples.append(f"_meta mutated caller input dict: {raw!r} != {snapshot!r}")
        out2 = _meta(out)
        if out2 != out or type(out2["enabled"]) is not bool:
            counterexamples.append(f"_meta not idempotent on {raw!r}: {out2!r} != {out!r}")

    # (1) All non-dict boundary inputs
    non_dict_inputs: list[object] = [
        None,
        0,
        1,
        -1,
        3.14,
        True,
        False,
        "",
        "true",
        [],
        ["name", "enabled"],
        (),
        set(),
        object(),
    ]
    for val in non_dict_inputs:
        _check_one(val, expected_en=True)

    # (2) All 2^4 = 16 missing/present key subsets, with and without extra unknown keys
    extra_variants: list[dict[str, object]] = [
        {},
        {"unknown": 123, "extra_nested": {"a": [1, 2]}, "__proto__": "x"},
    ]
    for mask in itertools.product((False, True), repeat=4):
        for extras in extra_variants:
            d: dict[str, object] = dict(extras)
            if mask[0]:
                d["name"] = "clip"
            if mask[1]:
                d["enabled"] = False
            if mask[2]:
                d["notes"] = "note"
            if mask[3]:
                d["source_hash"] = "deadbeef"
            _check_one(d, expected_en=not mask[1])

    # (3) Exhaustive hostile value types for enabled & string fields
    hostile_str_vals: list[object] = [
        None,
        0,
        1,
        3.14,
        True,
        False,
        [],
        {},
        "",
        "valid_str",
    ]
    hostile_enabled_vals: list[object] = [
        None,
        0,
        1,
        -1,
        0.0,
        1.5,
        True,
        False,
        [],
        {},
        "",
        "1",
        "true",
        "TRUE",
        " True ",
        "yes",
        "YES",
        "on",
        "ON",
        "0",
        "false",
        "FALSE",
        "no",
        "NO",
        "off",
        "OFF",
        "banana",
        " 0 ",
    ]

    for name_v, en_v in itertools.product(hostile_str_vals, hostile_enabled_vals):
        _check_one(
            {"name": name_v, "enabled": en_v, "notes": name_v, "source_hash": name_v, "extra": 9},
            expected_en=_expected_enabled(en_v),
        )
    for notes_v, hash_v in itertools.product(hostile_str_vals, hostile_str_vals):
        _check_one(
            {"notes": notes_v, "source_hash": hash_v},
            expected_en=True,
        )

    details.append(
        f"Verified _meta(raw) is total, non-mutating, idempotent, and strictly typed across {cases_checked} inputs"
    )
    details.append(
        "Checked all 14 non-dict inputs, all 16 key-presence subsets (+ unknown keys), "
        "and full Cartesian product of hostile types & truthy/falsy enabled strings"
    )
    return PropertyResult(
        prop_id="S4",
        title="_meta boundary sanitization total function (store.py:_meta)",
        status="FAIL" if counterexamples else "PASS",
        tool="Exhaustive model check over real ffgui.store._meta",
        bounds=f"{cases_checked} combinations of non-dicts, key subsets, extra keys, and hostile value types",
        states_checked=cases_checked,
        elapsed_s=time.perf_counter() - t0,
        details=details,
        counterexamples=counterexamples,
    )


def _print_report(results: list[PropertyResult], total_s: float) -> None:
    print("=" * 78)
    print(f"FFMpeg-GUI Store Crash & Boundary Verification Report (Z3 {z3.get_version_string()})")
    print("=" * 78)
    for r in results:
        print(f"\n[{r.status:8s}] {r.prop_id}: {r.title}")
        print(f"  Tool   : {r.tool}")
        print(f"  Bounds : {r.bounds}")
        print(f"  Checked: {r.states_checked:,} ({r.elapsed_s:.3f}s)")
        for line in r.details:
            print(f"  Detail : {line}")
        for idx, cex in enumerate(r.counterexamples, 1):
            print(f"  Cex #{idx}: {cex}")
    print("\n" + "=" * 78)
    summary = ", ".join(f"{r.prop_id}={r.status}" for r in results)
    print(f"Summary ({total_s:.3f}s): {summary}")
    print("=" * 78)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="FFMpeg-GUI store crash & boundary formal verification suite (S1-S4)"
    )
    parser.add_argument(
        "--report",
        action="store_true",
        help="Always exit 0 after printing the full verification report",
    )
    parser.add_argument(
        "--strict",
        action="store_true",
        help="Exit 1 if any property fails OR produces findings",
    )
    args = parser.parse_args(argv)

    t0 = time.perf_counter()
    results = [
        verify_s1_import_idempotency(),
        verify_s2_crash_durability(),
        verify_s3_unparsed_preservation(),
        verify_s4_meta_total_function(),
    ]
    _print_report(results, time.perf_counter() - t0)

    if args.report:
        return 0
    if args.strict:
        return 1 if any(r.status != "PASS" for r in results) else 0
    return 1 if any(r.status == "FAIL" for r in results) else 0


if __name__ == "__main__":
    sys.exit(main())
