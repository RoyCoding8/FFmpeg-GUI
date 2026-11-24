---
name: verify-formal
description: Formally verify FFMpeg-GUI script escaping, sidecar/heredoc safety, store crash recovery, probe lifecycle concurrency, QueueDocument invariants, and UI round-trip bijectivity using Z3 SMT and exhaustive BFS model checking. Use before merging changes to src/ffgui/export/, src/ffgui/doc.py, src/ffgui/validate.py, src/ffgui/store.py, src/ffgui/app.py, src/ffgui/controller.py, or src/ffgui/ui/tabs.py.
---

# FFMpeg-GUI Formal Verification

Runs 17 formal properties across four fast modules (~2.9s total) using Z3 SMT (`z3-solver`) and exhaustive BFS state-space model checking directly against the repo's real Python modules. All 17/17 properties pass under `--strict`.

## Coverage & Sync Record

- **Covered files**:
  - `src/ffgui/export/bat.py` (`bat_token`, `_cmd_literal`, `_sidecar_lines`)
  - `src/ffgui/export/sh.py` (`sh_token`, `_sidecar_lines`)
  - `src/ffgui/export/model.py` (`reject_hostile`, `prune_repeated_sidecars`)
  - `src/ffgui/export/orchestrate.py` (`script_for_queue`)
  - `src/ffgui/doc.py` (`QueueDocument.move_rows`, `set_codec`, `set_stream_drop`, `set_output_field`, `set_filters`, `set_segment_enabled`, `set_tee`, `touch`)
  - `src/ffgui/validate.py` (`METADATA_TAGS`, `validate_option`, time/int/disposition checks)
  - `src/ffgui/store.py` (`import_tui_queue`, `save_queue`, `load_queue`, `save_preset`, `delete_preset`, `load_presets`, `_meta`, `_read_json`, `UnparsedRow`)
  - `src/ffgui/app.py` (`_wire_probe`, `_launch_probe`, `_Probe`)
  - `src/ffgui/controller.py` and `src/ffgui/ui/tabs.py` (all 8 tabs, 61 user controls, 42 filter pairs, row-switch isolation)

## Run Command

```bash
QT_QPA_PLATFORM=offscreen PYTHONPATH="$PWD/src" .venv/bin/python verification/verify_all.py --strict
```

Individual modules can also be run directly: `verify_formal.py` (P1–P6), `verify_export_sidecars.py` (E1–E4), `verify_store_crash.py` (S1–S4), `verify_probe_ui.py` (A1, U1, U2).

## Properties Checked (17/17 PASS)

- **P1–P3 (`verify_formal.py`)**: Z3 SMT UNSAT proofs over lengths `0..8` for `.bat` token quoting/trailing-backslash soundness, `.bat` `_cmd_literal` metacharacter/`%` escaping, and POSIX `.sh` token safety + `shlex.split` injectivity.
- **P4–P6 (`verify_formal.py`)**: Exhaustive `move_rows` permutation check (`N=1..4`), complete fixpoint BFS for stream-drop/codec mutex and `unparsed` recovery, and depth-3 BFS proving zero reachable `FFtuiError` states from accepted `QueueDocument` edits.
- **E1–E4 (`verify_export_sidecars.py`)**: Z3 SMT proofs for `prune_repeated_sidecars` deduplication/content-distinction, POSIX heredoc delimiter termination and collision freedom, `.bat` sidecar rejection/`echo(` safety, and exhaustive 896-script emit-before-use ordering.
- **S1–S4 (`verify_store_crash.py`)**: Z3 SMT proof for `import_tui_queue` idempotency, 39/39 crash-injection durability and quarantine checks (including JSON `null`), `UnparsedRow` byte-exact preservation, and `_meta` total sanitization.
- **A1, U1, U2 (`verify_probe_ui.py`)**: BFS concurrency model check of generation-guarded `app.py:_wire_probe` (no double `_boot`, no deadlock, no stale `CapabilityIndex` reuse), 61/61 real-Qt control round-trip bijectivity, and exhaustive 42-pair filter/row non-interference.

## Interpreting Results & Maintenance

- Any `FAIL` or `FINDINGS` under `--strict` is a real regression unless the spec intentionally changed. Never weaken a property to silence a failure.
- After modifying any covered file in `src/ffgui/`, run `/maintain-formal-verification-skill` to confirm the models still match the code and update this skill.
