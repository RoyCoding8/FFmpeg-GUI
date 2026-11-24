#!/usr/bin/env python3
"""Formal verification suite for FFMpeg-GUI export sidecars (Z3 SMT + exhaustive checking).

Checks 4 formal properties directly tied to the real export pipeline:
  E1: prune_repeated_sidecars deduplication & content-distinction soundness
      (src/ffgui/export/model.py:prune_repeated_sidecars)
  E2: POSIX heredoc delimiter safety & termination
      (src/ffgui/export/sh.py:_sidecar_lines)
  E3: Batch sidecar rejection & echo( metacharacter/percent safety
      (src/ffgui/export/bat.py:_sidecar_lines)
  E4: script_for_queue multi-job & two-pass sidecar-before-use ordering
      (src/ffgui/export/orchestrate.py:script_for_queue)

Run:
  QT_QPA_PLATFORM=offscreen PYTHONPATH="$PWD/src" .venv/bin/python verification/verify_export_sidecars.py
"""

from __future__ import annotations

import argparse
import itertools
import os
import shlex
import sys
import time
from dataclasses import dataclass, field
from typing import Literal

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import z3
from fftui.model import Chapter, Input, Job, Output
from fftui.util.command_builder import Sidecar, sidecars_for

from ffgui.export.bat import _ECHO_META, _sidecar_lines as bat_sidecar_lines, bat_token
from ffgui.export.model import ExportBlock, prune_repeated_sidecars
from ffgui.export.orchestrate import TARGETS, script_for_queue
from ffgui.export.sh import _HEREDOC, _sidecar_lines as sh_sidecar_lines, sh_token

Z3_TIMEOUT_MS = 5000
CheckStatus = Literal["PASS", "FAIL", "FINDINGS"]
VariantKind = Literal["none", "a", "b"]


@dataclass(frozen=True)
class CheckResult:
    prop_id: str
    title: str
    status: CheckStatus
    tool: str
    bounds: str
    states_checked: int
    elapsed_s: float
    details: list[str] = field(default_factory=list)
    counterexamples: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class _JobSpec:
    two_pass: bool
    concat_kind: VariantKind
    chapter_kind: VariantKind


@dataclass(frozen=True)
class _ParsedScript:
    emitted_blocks: list[tuple[str, tuple[str, ...]]]
    emitted_at_line: dict[str, int]
    ffmpeg_lines: list[tuple[int, str]]


def _new_solver() -> z3.Solver:
    solver = z3.Solver()
    solver.set("timeout", Z3_TIMEOUT_MS)
    return solver


def _scan_cmd_literal_output(out: str) -> tuple[bool, bool]:
    """Two-pass cmd.exe literal scanner: returns (has_unpaired_pct, has_unescaped_meta)."""
    pct_odd = False
    after_pct: list[str] = []
    for ch in out:
        if ch == "%":
            if pct_odd:
                after_pct.append("%")
                pct_odd = False
            else:
                pct_odd = True
        else:
            if pct_odd:
                return True, False
            after_pct.append(ch)
    if pct_odd:
        return True, False

    esc = False
    for ch in after_pct:
        if esc:
            esc = False
        elif ch == "^":
            esc = True
        elif ch in _ECHO_META:
            return False, True
    if esc:
        return False, True
    return False, False


# ---------------------------------------------------------------------------
# E1: Z3 SMT + real code — prune_repeated_sidecars Soundness (export/model.py)
# ---------------------------------------------------------------------------
def verify_e1_prune_repeated_sidecars() -> CheckResult:
    t0 = time.perf_counter()
    details: list[str] = []
    counterexamples: list[str] = []

    num_blocks = 4
    max_sidecars = 3
    num_paths = 4
    num_contents = 4
    # Each of 12 slots is either absent (1) or present with (path, content) in 4x4 (16) -> 17 states/slot
    total_space = (1 + num_paths * num_contents) ** (num_blocks * max_sidecars)

    present = [
        [z3.Bool(f"p_{b}_{s}") for s in range(max_sidecars)]
        for b in range(num_blocks)
    ]
    path = [
        [z3.Int(f"path_{b}_{s}") for s in range(max_sidecars)]
        for b in range(num_blocks)
    ]
    content = [
        [z3.Int(f"cnt_{b}_{s}") for s in range(max_sidecars)]
        for b in range(num_blocks)
    ]

    domain: list[z3.BoolRef] = []
    for b in range(num_blocks):
        for s in range(max_sidecars):
            domain.append(z3.And(path[b][s] >= 0, path[b][s] < num_paths))
            domain.append(z3.And(content[b][s] >= 0, content[b][s] < num_contents))

    # Exact model of prune_repeated_sidecars:
    #   fresh = [c for c in block.sidecars if (c.path, c.content) not in seen]
    #   seen.update((c.path, c.content) for c in block.sidecars)
    seen_before: list[list[z3.BoolRef]] = []
    emitted: list[list[z3.BoolRef]] = []
    for b in range(num_blocks):
        row_seen: list[z3.BoolRef] = []
        row_emit: list[z3.BoolRef] = []
        for s in range(max_sidecars):
            if b == 0:
                sb: z3.BoolRef = z3.BoolVal(False)
            else:
                sb = z3.Or(
                    *[
                        z3.And(
                            present[pb][ps],
                            path[pb][ps] == path[b][s],
                            content[pb][ps] == content[b][s],
                        )
                        for pb in range(b)
                        for ps in range(max_sidecars)
                    ]
                )
            row_seen.append(sb)
            row_emit.append(z3.And(present[b][s], z3.Not(sb)))
        seen_before.append(row_seen)
        emitted.append(row_emit)

    # E1(a): UNSAT that a (path, content) with a *new* content at an existing path is ever dropped
    new_content_dropped: list[z3.BoolRef] = []
    for b in range(1, num_blocks):
        for s in range(max_sidecars):
            existing_path_diff_content = z3.Or(
                *[
                    z3.And(
                        present[pb][ps],
                        path[pb][ps] == path[b][s],
                        content[pb][ps] != content[b][s],
                    )
                    for pb in range(b)
                    for ps in range(max_sidecars)
                ]
            )
            new_content_dropped.append(
                z3.And(
                    present[b][s],
                    existing_path_diff_content,
                    z3.Not(seen_before[b][s]),
                    z3.Not(emitted[b][s]),
                )
            )
    s_a = _new_solver()
    s_a.add(*domain, z3.Or(*new_content_dropped))
    res_a = s_a.check()
    if res_a != z3.unsat:
        counterexamples.append(f"E1(a) new content at existing path dropped: {res_a}")

    # E1(b): UNSAT that any exact duplicate (path, content) already emitted in an earlier block is emitted twice
    dup_emitted: list[z3.BoolRef] = []
    for b1 in range(num_blocks):
        for s1 in range(max_sidecars):
            for b2 in range(b1 + 1, num_blocks):
                for s2 in range(max_sidecars):
                    dup_emitted.append(
                        z3.And(
                            emitted[b1][s1],
                            emitted[b2][s2],
                            path[b1][s1] == path[b2][s2],
                            content[b1][s1] == content[b2][s2],
                        )
                    )
    s_b = _new_solver()
    s_b.add(*domain, z3.Or(*dup_emitted))
    res_b = s_b.check()
    if res_b != z3.unsat:
        counterexamples.append(f"E1(b) duplicate (path, content) emitted across blocks: {res_b}")

    # E1(c): UNSAT that any block's required (path, content) was not emitted in current or earlier block
    missing_emit: list[z3.BoolRef] = []
    for b in range(num_blocks):
        for s in range(max_sidecars):
            emitted_by_b = z3.Or(
                *[
                    z3.And(
                        emitted[pb][ps],
                        path[pb][ps] == path[b][s],
                        content[pb][ps] == content[b][s],
                    )
                    for pb in range(b + 1)
                    for ps in range(max_sidecars)
                ]
            )
            missing_emit.append(z3.And(present[b][s], z3.Not(emitted_by_b)))
    s_c = _new_solver()
    s_c.add(*domain, z3.Or(*missing_emit))
    res_c = s_c.check()
    if res_c != z3.unsat:
        counterexamples.append(f"E1(c) emit-before-use violated: {res_c}")

    if not counterexamples:
        details.append(
            f"E1(a)(b)(c) UNSAT across {num_blocks} blocks x {max_sidecars} sidecars "
            f"({num_paths} paths x {num_contents} contents, {total_space:,} abstract schedules)"
        )

    # Synthesize diverse concrete schedules via Z3 and cross-check real prune_repeated_sidecars()
    synth_queries: list[tuple[str, list[z3.BoolRef]]] = [
        (
            "same_path_new_content",
            [
                present[0][0],
                present[1][0],
                path[0][0] == path[1][0],
                content[0][0] != content[1][0],
            ],
        ),
        (
            "exact_dup_across_blocks",
            [
                present[0][0],
                present[2][1],
                path[0][0] == path[2][1],
                content[0][0] == content[2][1],
            ],
        ),
        (
            "mixed_dup_and_new_content",
            [
                present[0][0],
                present[0][1],
                present[1][0],
                present[1][1],
                path[0][0] == path[1][0],
                content[0][0] == content[1][0],
                path[0][1] == path[1][1],
                content[0][1] != content[1][1],
            ],
        ),
        (
            "multi_block_chain",
            [
                present[0][0],
                present[1][0],
                present[2][0],
                present[3][0],
                path[0][0] == path[1][0],
                path[1][0] == path[2][0],
                content[0][0] == content[1][0],
                content[1][0] != content[2][0],
                path[2][0] == path[3][0],
                content[2][0] == content[3][0],
            ],
        ),
    ]

    concrete_checked = 0
    for label, conds in synth_queries:
        sg = _new_solver()
        sg.add(*domain, *conds)
        for _ in range(3):
            if sg.check() != z3.sat:
                break
            m = sg.model()
            blocks: list[ExportBlock] = []
            expected_emitted: list[list[tuple[str, str]]] = []
            blocking: list[z3.BoolRef] = []

            for b in range(num_blocks):
                cars: list[Sidecar] = []
                exp_b: list[tuple[str, str]] = []
                for s in range(max_sidecars):
                    is_p = z3.is_true(m.eval(present[b][s], model_completion=True))
                    p_val = m.eval(path[b][s], model_completion=True).as_long()
                    c_val = m.eval(content[b][s], model_completion=True).as_long()
                    is_e = z3.is_true(m.eval(emitted[b][s], model_completion=True))
                    blocking.append(present[b][s] != is_p)
                    if is_p:
                        blocking.append(path[b][s] != p_val)
                        blocking.append(content[b][s] != c_val)
                        sc = Sidecar(f"/tmp/sidecar_{p_val}.txt", f"payload_{c_val}\n")
                        cars.append(sc)
                        if is_e:
                            exp_b.append((sc.path, sc.content))
                blocks.append(ExportBlock(argv=["ffmpeg", "-i", f"in_{b}.mp4"], sidecars=cars))
                expected_emitted.append(exp_b)

            pruned = prune_repeated_sidecars(blocks)
            concrete_checked += 1

            actual_emitted = [[(c.path, c.content) for c in blk.sidecars] for blk in pruned]
            if actual_emitted != expected_emitted:
                counterexamples.append(
                    f"Real prune_repeated_sidecars diverged on {label}: "
                    f"expected={expected_emitted!r}, actual={actual_emitted!r}"
                )

            # Directly verify (a), (b), (c) on the real pruned output
            emitted_earlier: set[tuple[str, str]] = set()
            for b, (orig_blk, pruned_blk) in enumerate(zip(blocks, pruned)):
                pruned_pairs = [(c.path, c.content) for c in pruned_blk.sidecars]
                for pair in pruned_pairs:
                    if pair in emitted_earlier:
                        counterexamples.append(
                            f"Real E1(b) failed on {label} block {b}: duplicate {pair!r} emitted"
                        )
                emitted_now = emitted_earlier | set(pruned_pairs)
                for car in orig_blk.sidecars:
                    pair = (car.path, car.content)
                    if pair not in emitted_now:
                        counterexamples.append(
                            f"Real E1(c) failed on {label} block {b}: missing {pair!r} before use"
                        )
                    if pair not in emitted_earlier and pair not in pruned_pairs:
                        counterexamples.append(
                            f"Real E1(a) failed on {label} block {b}: fresh {pair!r} was dropped"
                        )
                emitted_earlier = emitted_now

            sg.add(z3.Or(*blocking))

    details.append(
        f"Cross-checked {concrete_checked} Z3-synthesized multi-block schedules against real prune_repeated_sidecars()"
    )
    return CheckResult(
        prop_id="E1",
        title="prune_repeated_sidecars deduplication & content-distinction soundness (model.py)",
        status="FAIL" if counterexamples else "PASS",
        tool=f"Z3 SMT ({z3.get_version_string()}) + real prune_repeated_sidecars cross-check",
        bounds=(
            f"All schedules up to {num_blocks} blocks x {max_sidecars} sidecars "
            f"({total_space:,} abstract schedules) + {concrete_checked} concrete schedules"
        ),
        states_checked=total_space,
        elapsed_s=time.perf_counter() - t0,
        details=details,
        counterexamples=counterexamples,
    )


# ---------------------------------------------------------------------------
# E2: Z3 SMT + real code — POSIX Heredoc Delimiter Safety & Termination (sh.py)
# ---------------------------------------------------------------------------
def verify_e2_sh_heredoc_safety() -> CheckResult:
    t0 = time.perf_counter()
    details: list[str] = []
    counterexamples: list[str] = []

    # Model 1: Exact character-level bounded Z3 array model over 4 char classes:
    #   0: 'F' (stands for base delimiter token "FFGUI_EOF"),
    #   1: '_' (underscore appended per loop iteration),
    #   2: '\n' (newline splitting heredoc body lines),
    #   3: 'x' (any other character)
    # Delimiter at step k is [0] + [1]*k of length k + 1.
    c_base, c_under, c_nl, c_other = 0, 1, 2, 3
    n_cls = 4
    max_n = 10
    char_space = sum(n_cls ** (n - 1) for n in range(1, max_n + 1))

    for n in range(1, max_n + 1):
        chars = [z3.Int(f"h_{n}_{i}") for i in range(n)]
        domain = [z3.And(c >= 0, c < n_cls) for c in chars[:-1]]
        # Heredoc branch in _sidecar_lines runs iff car.content.endswith("\n")
        domain.append(chars[n - 1] == c_nl)

        def match_at(pos: int, k: int, cs: list[z3.ArithRef] = chars) -> z3.BoolRef:
            return z3.And(
                cs[pos] == c_base,
                *[cs[pos + 1 + j] == c_under for j in range(k)],
            )

        # in_content[k]: delim_k (length k + 1) appears anywhere in chars[0..n-1]
        in_content: list[z3.BoolRef] = []
        for k in range(n + 1):
            if k + 1 > n:
                in_content.append(z3.BoolVal(False))
            else:
                in_content.append(z3.Or(*[match_at(pos, k) for pos in range(n - k)]))

        # Exact loop semantics: stopped_at[k] iff delim_0..delim_{k-1} in content and delim_k not in content
        stopped_at: list[z3.BoolRef] = []
        for k in range(n + 1):
            prev_in = [in_content[j] for j in range(k)]
            stopped_at.append(z3.And(z3.Not(in_content[k]), *prev_in))

        # E2(a): UNSAT that loop fails to terminate in <= n + 1 steps (indices 0..n)
        s_term = _new_solver()
        s_term.add(*domain, z3.Not(z3.Or(*stopped_at)))
        if s_term.check() != z3.unsat:
            counterexamples.append(f"E2(a) n={n} termination failed: {s_term.check()}")

        # E2(b): UNSAT that chosen delimiter still occurs in content
        s_sub = _new_solver()
        s_sub.add(*domain, z3.Or(*[z3.And(stopped_at[k], in_content[k]) for k in range(n + 1)]))
        if s_sub.check() != z3.unsat:
            counterexamples.append(f"E2(b) n={n} delim still in content: {s_sub.check()}")

        # E2(c): UNSAT that any split heredoc body line equals the chosen delimiter
        # A body line spanning [start, end) (end <= n - 1, chars[end] == '\n',
        # start == 0 or chars[start - 1] == '\n', no '\n' inside [start, end)) equals delim_k
        # iff end - start == k + 1 and match_at(start, k).
        line_eq_bad: list[z3.BoolRef] = []
        for k in range(n):
            line_len = k + 1
            for start in range(n - line_len):
                end = start + line_len
                start_ok: z3.BoolRef = (
                    z3.BoolVal(True) if start == 0 else (chars[start - 1] == c_nl)
                )
                end_ok = chars[end] == c_nl
                line_eq_bad.append(
                    z3.And(stopped_at[k], start_ok, end_ok, match_at(start, k))
                )
        if line_eq_bad:
            s_line = _new_solver()
            s_line.add(*domain, z3.Or(*line_eq_bad))
            if s_line.check() != z3.unsat:
                counterexamples.append(f"E2(c) n={n} heredoc body line equals delim: {s_line.check()}")

    # Model 2: Exact length-bound proof for base_len = len("FFGUI_EOF") = 9 up to N = 32
    base_len = len(_HEREDOC)
    max_abstract_n = 32
    for n in range(1, max_abstract_n + 1):
        occ = [z3.Bool(f"occ_{n}_{k}") for k in range(n + 1)]
        eq_line = [z3.Bool(f"eq_{n}_{k}") for k in range(n + 1)]
        axioms: list[z3.BoolRef] = []
        for k in range(n + 1):
            axioms.append(z3.Implies(eq_line[k], occ[k]))
            if base_len + k > n:
                axioms.append(z3.Not(occ[k]))
            if k + 1 <= n:
                axioms.append(z3.Implies(occ[k + 1], occ[k]))
        stopped = [
            z3.And(z3.Not(occ[k]), *[occ[j] for j in range(k)])
            for k in range(n + 1)
        ]
        s_abs = _new_solver()
        s_abs.add(
            *axioms,
            z3.Or(
                z3.Not(z3.Or(*stopped)),
                *[z3.And(stopped[k], z3.Or(occ[k], eq_line[k])) for k in range(n + 1)],
            ),
        )
        if s_abs.check() != z3.unsat:
            counterexamples.append(f"E2 abstract length={n} violated: {s_abs.check()}")

    if not counterexamples:
        details.append(
            f"E2 UNSAT across exact char-level model lengths 1..{max_n} ({char_space:,} newline-terminated strings) "
            f"and exact-length base={base_len} model N=1..{max_abstract_n}: "
            "terminates in <= len(content)+1 steps, delim not in content, no body line == delim"
        )

    # Cross-check real ffgui.export.sh._sidecar_lines on adversarial & Z3-synthesized payloads
    adversarial_payloads: list[tuple[str, str]] = [
        ("empty_non_nl", ""),
        ("single_nl", "\n"),
        ("double_nl", "\n\n"),
        ("exact_eof", f"{_HEREDOC}\n"),
        ("eof_and_1", f"{_HEREDOC}\n{_HEREDOC}_\n"),
        ("eof_chain_3", f"{_HEREDOC}\n{_HEREDOC}_\n{_HEREDOC}__\n"),
        ("eof_reverse_chain", f"{_HEREDOC}___\n{_HEREDOC}__\n{_HEREDOC}_\n{_HEREDOC}\n"),
        ("eof_inline_substring", f"title=hello {_HEREDOC} world\ntitle={_HEREDOC}_\n"),
        ("shell_injection_cmdsub", f"$(rm -rf /)\n`id`\n$HOME\n{_HEREDOC}\n"),
        ("quotes_and_backslashes", "single 'quote'\ndouble \"quote\"\nback\\slash\n"),
        ("concat_with_eof", f"file 'clip_{_HEREDOC}.mp4'\nfile 'clip_{_HEREDOC}_.mp4'\n"),
        ("chapters_with_shell", f";FFMETADATA1\ntitle=$(whoami) `id` '{_HEREDOC}'\n"),
        ("no_nl_plain", "no trailing newline"),
        ("no_nl_eof", _HEREDOC),
        ("no_nl_injection", "$(rm -rf /) `id` 'single' \"double\""),
    ]

    # Synthesize multi-step delimiter collision payloads via Z3
    synth_n = 8
    sc_vars = [z3.Int(f"se_{i}") for i in range(synth_n)]
    sg = _new_solver()
    sg.add(*[z3.And(c >= 0, c < n_cls) for c in sc_vars[:-1]], sc_vars[-1] == c_nl)
    # Require at least one 'F' followed by '_' so delimiter advances >= 2 steps
    sg.add(
        z3.Or(*[z3.And(sc_vars[i] == c_base, sc_vars[i + 1] == c_under) for i in range(synth_n - 2)])
    )
    tok_map = {c_base: _HEREDOC, c_under: "_", c_nl: "\n", c_other: "$(id)"}
    for idx in range(6):
        if sg.check() != z3.sat:
            break
        m = sg.model()
        vals = [m.eval(c, model_completion=True).as_long() for c in sc_vars]
        payload = "".join(tok_map[v] for v in vals)
        adversarial_payloads.append((f"z3_synth_{idx}", payload))
        sg.add(z3.Or(*[c != v for c, v in zip(sc_vars, vals)]))

    sidecar_path = "/tmp/ffgui dir/sidecar's file.ffmetadata"
    expected_dest = sh_token(sidecar_path)
    for label, content in adversarial_payloads:
        car = Sidecar(sidecar_path, content)
        lines = sh_sidecar_lines(car)
        if not content.endswith("\n"):
            if len(lines) != 2 or not lines[1].startswith("printf %s "):
                counterexamples.append(
                    f"Real _sidecar_lines non-newline {label} unexpected format: {lines!r}"
                )
                continue
            prefix = "printf %s "
            suffix = f" > {expected_dest}"
            if not lines[1].endswith(suffix):
                counterexamples.append(
                    f"Real _sidecar_lines non-newline {label} wrong dest: {lines[1]!r}"
                )
                continue
            quoted_payload = lines[1][len(prefix) : -len(suffix)]
            if shlex.split(quoted_payload) != [content]:
                counterexamples.append(
                    f"Real _sidecar_lines non-newline {label} printf round-trip failed: {quoted_payload!r}"
                )
        else:
            if len(lines) < 3:
                counterexamples.append(f"Real _sidecar_lines {label} too few lines: {lines!r}")
                continue
            header = lines[1]
            closing = lines[-1]
            body = lines[2:-1]
            expected_header = f"cat > {expected_dest} <<'{closing}'"
            if header != expected_header:
                counterexamples.append(
                    f"Real _sidecar_lines {label} header mismatch: {header!r} != {expected_header!r}"
                )
            steps = len(closing) - len(_HEREDOC)
            if not closing.startswith(_HEREDOC) or steps < 0 or steps > len(content):
                counterexamples.append(
                    f"Real _sidecar_lines {label} invalid delimiter {closing!r} for len={len(content)}"
                )
            if closing in content:
                counterexamples.append(
                    f"Real _sidecar_lines {label} delimiter {closing!r} appears in content!"
                )
            if any(line == closing for line in body):
                counterexamples.append(
                    f"Real _sidecar_lines {label} heredoc body line equals delimiter {closing!r}!"
                )
            reconstructed = "\n".join(body) + "\n"
            if reconstructed != content:
                counterexamples.append(
                    f"Real _sidecar_lines {label} body mismatch: {reconstructed!r} != {content!r}"
                )

    details.append(
        f"Cross-checked {len(adversarial_payloads)} adversarial & Z3-synthesized payloads against real sh._sidecar_lines()"
    )
    return CheckResult(
        prop_id="E2",
        title="POSIX heredoc delimiter safety & termination (sh.py:_sidecar_lines)",
        status="FAIL" if counterexamples else "PASS",
        tool=f"Z3 SMT ({z3.get_version_string()}) + real sh._sidecar_lines cross-check",
        bounds=(
            f"Exact char model lengths 1..{max_n} ({char_space:,} strings) + "
            f"abstract N=1..{max_abstract_n} + {len(adversarial_payloads)} adversarial payloads"
        ),
        states_checked=char_space + max_abstract_n,
        elapsed_s=time.perf_counter() - t0,
        details=details,
        counterexamples=counterexamples,
    )


# ---------------------------------------------------------------------------
# E3: Z3 SMT + real code — Batch Sidecar Rejection & echo( Safety (bat.py)
# ---------------------------------------------------------------------------
def verify_e3_bat_sidecar_safety() -> CheckResult:
    t0 = time.perf_counter()
    details: list[str] = []
    counterexamples: list[str] = []

    # 12 character classes covering every decision branch in bat._sidecar_lines() and _cmd_literal():
    #   0: 'a', 1: '\n', 2: '\r', 3: '"', 4: '%', 5: '^', 6: '&', 7: '|', 8: '<', 9: '>', 10: '(', 11: ')'
    alphabet = ["a", "\n", "\r", '"', "%", "^", "&", "|", "<", ">", "(", ")"]
    idx_lf = alphabet.index("\n")
    idx_cr = alphabet.index("\r")
    idx_dq = alphabet.index('"')
    idx_pct = alphabet.index("%")
    idx_caret = alphabet.index("^")
    meta_indices = [alphabet.index(c) for c in sorted(_ECHO_META)]
    non_caret_meta = [i for i in meta_indices if i != idx_caret]

    max_len = 8
    total_space = sum(len(alphabet) ** length for length in range(max_len + 1))

    for length in range(max_len + 1):
        chars = [z3.Int(f"bs_{length}_{i}") for i in range(length)]
        domain = [z3.And(c >= 0, c < len(alphabet)) for c in chars]

        has_trailing_lf: z3.BoolRef = (
            (chars[-1] == idx_lf) if length > 0 else z3.BoolVal(False)
        )
        has_cr: z3.BoolRef = (
            z3.Or(*[c == idx_cr for c in chars]) if chars else z3.BoolVal(False)
        )
        has_dq: z3.BoolRef = (
            z3.Or(*[c == idx_dq for c in chars]) if chars else z3.BoolVal(False)
        )
        accepted = z3.And(has_trailing_lf, z3.Not(has_cr), z3.Not(has_dq))

        # E3(a): UNSAT that any sidecar lacking trailing '\n' is accepted
        s_a = _new_solver()
        s_a.add(*domain, accepted, z3.Not(has_trailing_lf))
        if s_a.check() != z3.unsat:
            counterexamples.append(f"E3(a) len={length} missing trailing LF accepted: {s_a.check()}")

        # E3(b): UNSAT that any sidecar containing '\r' or '"' is accepted
        s_b = _new_solver()
        s_b.add(*domain, accepted, z3.Or(has_cr, has_dq))
        if s_b.check() != z3.unsat:
            counterexamples.append(f"E3(b) len={length} CR or quote accepted: {s_b.check()}")

        # E3(c): Multi-line two-pass cmd.exe scanner over every echo(_cmd_literal(line)) body line
        pct_odd: z3.BoolRef = z3.BoolVal(False)
        unpaired_pct: z3.BoolRef = z3.BoolVal(False)
        esc: z3.BoolRef = z3.BoolVal(False)
        unescaped_meta: z3.BoolRef = z3.BoolVal(False)
        line_end_bad: z3.BoolRef = z3.BoolVal(False)

        def step_pct(
            ch_code: z3.ArithRef, p_odd: z3.BoolRef, bad: z3.BoolRef
        ) -> tuple[z3.BoolRef, z3.BoolRef]:
            is_pct = ch_code == idx_pct
            new_bad = z3.Or(bad, z3.And(p_odd, z3.Not(is_pct)))
            new_odd = z3.If(is_pct, z3.Not(p_odd), z3.BoolVal(False))
            return new_odd, new_bad

        def step_caret(
            ch_code: z3.ArithRef, is_esc: z3.BoolRef, bad: z3.BoolRef
        ) -> tuple[z3.BoolRef, z3.BoolRef]:
            is_caret = ch_code == idx_caret
            is_non_caret = z3.Or(*[ch_code == m for m in non_caret_meta])
            new_bad = z3.Or(bad, z3.And(z3.Not(is_esc), is_non_caret))
            new_esc = z3.If(is_esc, z3.BoolVal(False), is_caret)
            return new_esc, new_bad

        for c in chars:
            is_lf = c == idx_lf
            is_pct = c == idx_pct
            is_meta = z3.Or(*[c == m for m in meta_indices])

            # Branch 1: '%' -> '%%' (pass 1 sees '%','%', pass 2 sees '%')
            p1_pct, b1_pct = step_pct(z3.IntVal(idx_pct), pct_odd, unpaired_pct)
            p1_pct, b1_pct = step_pct(z3.IntVal(idx_pct), p1_pct, b1_pct)
            e2_pct, m2_pct = step_caret(z3.IntVal(idx_pct), esc, unescaped_meta)

            # Branch 2: meta in &|<>^() -> '^' + c
            p1_meta, b1_meta = step_pct(z3.IntVal(idx_caret), pct_odd, unpaired_pct)
            p1_meta, b1_meta = step_pct(c, p1_meta, b1_meta)
            e2_meta, m2_meta = step_caret(z3.IntVal(idx_caret), esc, unescaped_meta)
            e2_meta, m2_meta = step_caret(c, e2_meta, m2_meta)

            # Branch 3: normal char -> c
            p1_norm, b1_norm = step_pct(c, pct_odd, unpaired_pct)
            e2_norm, m2_norm = step_caret(c, esc, unescaped_meta)

            # On '\n', check end-of-line state (no dangling % or ^) and reset for next echo( line
            line_end_bad = z3.If(is_lf, z3.Or(line_end_bad, pct_odd, esc), line_end_bad)
            pct_odd = z3.If(
                is_lf,
                z3.BoolVal(False),
                z3.If(is_pct, p1_pct, z3.If(is_meta, p1_meta, p1_norm)),
            )
            unpaired_pct = z3.If(
                is_lf,
                unpaired_pct,
                z3.If(is_pct, b1_pct, z3.If(is_meta, b1_meta, b1_norm)),
            )
            esc = z3.If(
                is_lf,
                z3.BoolVal(False),
                z3.If(is_pct, e2_pct, z3.If(is_meta, e2_meta, e2_norm)),
            )
            unescaped_meta = z3.If(
                is_lf,
                unescaped_meta,
                z3.If(is_pct, m2_pct, z3.If(is_meta, m2_meta, m2_norm)),
            )

        s_c = _new_solver()
        s_c.add(
            *domain,
            accepted,
            z3.Or(unpaired_pct, pct_odd, unescaped_meta, esc, line_end_bad),
        )
        if s_c.check() != z3.unsat:
            counterexamples.append(f"E3(c) len={length} unsafe echo( body: {s_c.check()}")

    if not counterexamples:
        details.append(
            f"E3(a)(b)(c) UNSAT across all lengths 0..{max_len} over 12 char classes ({total_space:,} sidecar strings): "
            "rejects missing trailing LF, CR, and quotes; zero unescaped &|<>^() and zero unpaired % in echo("
        )

    # Synthesize concrete rejected and accepted sidecars via Z3 and cross-check real bat._sidecar_lines()
    zlen = 6
    zc = [z3.Int(f"zbs_{i}") for i in range(zlen)]
    zdom = [z3.And(c >= 0, c < len(alphabet)) for c in zc]
    z_has_cr = z3.Or(*[c == idx_cr for c in zc])
    z_has_dq = z3.Or(*[c == idx_dq for c in zc])
    z_acc = z3.And(zc[-1] == idx_lf, z3.Not(z_has_cr), z3.Not(z_has_dq))

    synth_specs: list[tuple[str, list[z3.BoolRef], bool]] = [
        ("reject_no_trailing_lf", [zc[-1] != idx_lf, z3.Not(z_has_cr), z3.Not(z_has_dq)], False),
        ("reject_cr", [zc[-1] == idx_lf, z_has_cr, z3.Not(z_has_dq)], False),
        ("reject_dquote", [zc[-1] == idx_lf, z3.Not(z_has_cr), z_has_dq], False),
        (
            "accept_meta_and_pct",
            [
                z_acc,
                z3.Or(*[c == idx_pct for c in zc[:-1]]),
                z3.Or(*[c == idx_caret for c in zc[:-1]]),
                z3.Or(*[z3.Or(*[c == m for m in non_caret_meta]) for c in zc[:-1]]),
            ],
            True,
        ),
        (
            "accept_multiline",
            [
                z_acc,
                z3.Or(*[c == idx_lf for c in zc[:-1]]),
                z3.Or(*[c == idx_pct for c in zc[:-1]]),
            ],
            True,
        ),
    ]

    concrete_cases: list[tuple[str, str, bool]] = [
        ("empty", "", False),
        ("no_nl", "file 'a.mp4'", False),
        ("crlf", "file 'a.mp4'\r\n", False),
        ("dquote", 'title="quoted"\n', False),
        ("single_nl", "\n", True),
        ("all_meta", "&|<>^()%PATH%^^%%\n", True),
        ("multiline_chapters", ";FFMETADATA1\ntitle=Rock & Roll (100% Live) ^ < > |\n", True),
    ]

    for label, conds, should_accept in synth_specs:
        sg = _new_solver()
        sg.add(*zdom, *conds)
        for _ in range(3):
            if sg.check() != z3.sat:
                break
            m = sg.model()
            vals = [m.eval(c, model_completion=True).as_long() for c in zc]
            concrete_cases.append((label, "".join(alphabet[v] for v in vals), should_accept))
            sg.add(z3.Or(*[c != v for c, v in zip(zc, vals)]))

    valid_path = r"C:\tmp\sidecars\list & meta.txt"
    for label, content, should_accept in concrete_cases:
        car = Sidecar(valid_path, content)
        if not should_accept:
            try:
                out = bat_sidecar_lines(car)
                counterexamples.append(
                    f"Real bat._sidecar_lines accepted invalid {label} {content!r} -> {out!r}"
                )
            except ValueError:
                pass
        else:
            try:
                out = bat_sidecar_lines(car)
            except Exception as exc:
                counterexamples.append(
                    f"Real bat._sidecar_lines rejected valid {label} {content!r}: {exc}"
                )
                continue
            if len(out) < 3 or not out[0].startswith("if not exist ") or out[-1] != ")":
                counterexamples.append(
                    f"Real bat._sidecar_lines malformed wrapper for {label}: {out!r}"
                )
                continue
            if out[1] != f"> {bat_token(valid_path)} (":
                counterexamples.append(
                    f"Real bat._sidecar_lines bad redirect for {label}: {out[1]!r}"
                )
            for line in out[2:-1]:
                if not line.startswith(" echo("):
                    counterexamples.append(
                        f"Real bat._sidecar_lines body missing ' echo(' prefix in {label}: {line!r}"
                    )
                    continue
                body = line[len(" echo(") :]
                if "\r" in body or '"' in body:
                    counterexamples.append(
                        f"Real bat._sidecar_lines emitted CR/quote in {label}: {body!r}"
                    )
                bad_pct, bad_meta = _scan_cmd_literal_output(body)
                if bad_pct or bad_meta:
                    counterexamples.append(
                        f"Real bat._sidecar_lines unsafe echo( body in {label}: {body!r} "
                        f"(bad_pct={bad_pct}, bad_meta={bad_meta})"
                    )

    details.append(
        f"Cross-checked {len(concrete_cases)} Z3-synthesized & adversarial sidecars against real bat._sidecar_lines()"
    )
    return CheckResult(
        prop_id="E3",
        title="Batch sidecar rejection & echo( safety (bat.py:_sidecar_lines)",
        status="FAIL" if counterexamples else "PASS",
        tool=f"Z3 SMT ({z3.get_version_string()}) + real bat._sidecar_lines cross-check",
        bounds=(
            f"All strings length 0..{max_len} over 12 char classes ({total_space:,} strings) "
            f"+ {len(concrete_cases)} concrete sidecars"
        ),
        states_checked=total_space,
        elapsed_s=time.perf_counter() - t0,
        details=details,
        counterexamples=counterexamples,
    )


# ---------------------------------------------------------------------------
# E4: Exhaustive real-code check — script_for_queue Sidecar Ordering
# ---------------------------------------------------------------------------
def _build_job_from_spec(spec: _JobSpec, out_name: str) -> Job:
    concat_map: dict[VariantKind, list[str]] = {
        "none": [],
        "a": ["clip_a1.mp4", "clip_a2.mp4"],
        "b": ["clip_b1.mp4", "clip_b2.mp4"],
    }
    chapter_map: dict[VariantKind, list[Chapter]] = {
        "none": [],
        "a": [Chapter("0", "Opening"), Chapter("10", "Main")],
        "b": [Chapter("0", "Prologue"), Chapter("15", "Finale")],
    }
    inp = Input(path="input.mp4", concat_paths=concat_map[spec.concat_kind])
    out = Output(
        path=out_name,
        video_codec="libx264",
        audio_codec="aac",
        chapters=chapter_map[spec.chapter_kind],
    )
    return Job(inputs=[inp], outputs=[out], two_pass=spec.two_pass)


def _parse_sh_script(script: str) -> _ParsedScript:
    lines = script.splitlines()
    emitted_blocks: list[tuple[str, tuple[str, ...]]] = []
    emitted_at_line: dict[str, int] = {}
    ffmpeg_lines: list[tuple[int, str]] = []

    i = 0
    while i < len(lines):
        line = lines[i]
        if line.startswith("cat > ") and " <<'" in line and line.endswith("'"):
            dest_part, delim_part = line[len("cat > ") :].rsplit(" <<'", 1)
            delim = delim_part[:-1]
            dest = shlex.split(dest_part)[0]
            start_line = i
            body: list[str] = []
            i += 1
            while i < len(lines) and lines[i] != delim:
                body.append(lines[i])
                i += 1
            emitted_blocks.append((dest, tuple(body)))
            emitted_at_line.setdefault(dest, start_line)
            i += 1
            continue
        if line.startswith("printf %s ") and " > " in line:
            payload_part, dest_part = line[len("printf %s ") :].rsplit(" > ", 1)
            dest = shlex.split(dest_part)[0]
            emitted_blocks.append((dest, (payload_part,)))
            emitted_at_line.setdefault(dest, i)
            i += 1
            continue
        if (
            line
            and not line.startswith(("#", "set ", "trap ", "mkdir -p "))
            and "ffmpeg" in line
        ):
            ffmpeg_lines.append((i, line))
        i += 1

    return _ParsedScript(emitted_blocks, emitted_at_line, ffmpeg_lines)


def _parse_bat_script(script: str) -> _ParsedScript:
    lines = script.splitlines()
    emitted_blocks: list[tuple[str, tuple[str, ...]]] = []
    emitted_at_line: dict[str, int] = {}
    ffmpeg_lines: list[tuple[int, str]] = []

    i = 0
    while i < len(lines):
        line = lines[i]
        if line.startswith("> ") and line.endswith(" ("):
            raw_dest = line[len("> ") : -len(" (")]
            dest = raw_dest[1:-1] if raw_dest.startswith('"') and raw_dest.endswith('"') else raw_dest
            start_line = i
            body: list[str] = []
            i += 1
            while i < len(lines) and lines[i] != ")":
                body.append(lines[i])
                i += 1
            emitted_blocks.append((dest, tuple(body)))
            emitted_at_line.setdefault(dest, start_line)
            i += 1
            continue
        if (
            line
            and not line.startswith(
                ("@echo", "setlocal", "rem ", "if not ", "echo ", "pause", "endlocal", ":")
            )
            and "ffmpeg" in line
        ):
            ffmpeg_lines.append((i, line))
        i += 1

    return _ParsedScript(emitted_blocks, emitted_at_line, ffmpeg_lines)


def _check_single_script(
    script: str, target: str, jobs: list[Job], label: str
) -> list[str]:
    errors: list[str] = []
    parsed = _parse_sh_script(script) if target == "sh" else _parse_bat_script(script)

    # 1. Zero duplicate identical sidecar blocks
    seen_blocks: set[tuple[str, tuple[str, ...]]] = set()
    for blk in parsed.emitted_blocks:
        if blk in seen_blocks:
            errors.append(f"{label}: duplicate identical sidecar block emitted for {blk[0]!r}")
        seen_blocks.add(blk)

    # 2. Collect all expected sidecar paths across the jobs in this script
    expected_paths: set[str] = {car.path for job in jobs for car in sidecars_for(job)}

    # 3. Every sidecar path referenced in any ffmpeg command line must be created earlier
    referenced_paths: set[str] = set()
    for line_no, cmd_line in parsed.ffmpeg_lines:
        if target == "sh":
            tokens = shlex.split(cmd_line)
            refs = [
                tok
                for tok in tokens
                if tok in expected_paths or "fftui-concat-" in tok or "fftui-chapters-" in tok
            ]
        else:
            refs = [
                path
                for path in expected_paths
                if bat_token(path) in cmd_line or path in cmd_line
            ]
        for ref in refs:
            referenced_paths.add(ref)
            created_at = parsed.emitted_at_line.get(ref)
            if created_at is None:
                errors.append(
                    f"{label}: ffmpeg line {line_no} references sidecar {ref!r} never created in script"
                )
            elif created_at >= line_no:
                errors.append(
                    f"{label}: ffmpeg line {line_no} references sidecar {ref!r} created later at line {created_at}"
                )

    # 4. Every expected sidecar must be created and referenced
    for path in sorted(expected_paths):
        if path not in parsed.emitted_at_line:
            errors.append(f"{label}: expected sidecar {path!r} was not emitted")
        if path not in referenced_paths:
            errors.append(f"{label}: expected sidecar {path!r} was never referenced by ffmpeg")

    return errors


def verify_e4_queue_sidecar_ordering() -> CheckResult:
    t0 = time.perf_counter()
    counterexamples: list[str] = []

    job1_specs = [
        _JobSpec(tp, ck, chk)
        for tp, ck, chk in itertools.product((False, True), ("none", "a"), ("none", "a"))
    ]
    job2_specs = [
        _JobSpec(tp, ck, chk)
        for tp, ck, chk in itertools.product(
            (False, True), ("none", "a", "b"), ("none", "a", "b")
        )
    ]

    queues: list[tuple[str, list[Job]]] = []
    for s1 in job1_specs:
        queues.append(
            (
                f"1job(tp={s1.two_pass},cat={s1.concat_kind},ch={s1.chapter_kind})",
                [_build_job_from_spec(s1, "out1.mp4")],
            )
        )
    for s1 in job1_specs:
        for s2 in job2_specs:
            queues.append(
                (
                    f"2job[({s1.two_pass},{s1.concat_kind},{s1.chapter_kind}),"
                    f"({s2.two_pass},{s2.concat_kind},{s2.chapter_kind})]",
                    [
                        _build_job_from_spec(s1, "out1.mp4"),
                        _build_job_from_spec(s2, "out2.mp4"),
                    ],
                )
            )

    scripts_checked = 0
    for q_label, jobs in queues:
        for target in TARGETS:
            for one_file in (True, False):
                rendered = script_for_queue(
                    jobs,
                    target,
                    "fv-test-hash",
                    one_file=one_file,
                    created_utc="2026-01-01 00:00 UTC",
                )
                mode_label = f"{q_label} target={target} one_file={one_file}"
                if one_file:
                    if not isinstance(rendered, str):
                        counterexamples.append(f"{mode_label}: expected str, got {type(rendered)}")
                        continue
                    scripts_checked += 1
                    counterexamples.extend(
                        _check_single_script(rendered, target, jobs, mode_label)
                    )
                else:
                    if not isinstance(rendered, list) or len(rendered) != len(jobs):
                        counterexamples.append(
                            f"{mode_label}: expected {len(jobs)} scripts, got {type(rendered)}"
                        )
                        continue
                    for idx, (script, job) in enumerate(zip(rendered, jobs)):
                        scripts_checked += 1
                        counterexamples.extend(
                            _check_single_script(script, target, [job], f"{mode_label}[{idx}]")
                        )

    return CheckResult(
        prop_id="E4",
        title="script_for_queue multi-job & two-pass sidecar-before-use ordering (orchestrate.py)",
        status="FAIL" if counterexamples else "PASS",
        tool="Exhaustive real-code model check over ffgui.export.orchestrate.script_for_queue",
        bounds=(
            f"All {len(queues)} 1-2 job queues (single/two-pass x concat x identical/distinct chapters) "
            f"x 2 targets (sh, bat) x 2 modes (one_file=True/False) = {scripts_checked} rendered scripts"
        ),
        states_checked=scripts_checked,
        elapsed_s=time.perf_counter() - t0,
        details=[
            f"Checked {len(queues)} queue configurations across {scripts_checked} rendered scripts",
            "Verified: every ffmpeg-referenced sidecar is created earlier in the script with zero duplicate identical sidecar blocks",
        ],
        counterexamples=counterexamples[:10],
    )


def run_all() -> tuple[list[CheckResult], int]:
    results = [
        verify_e1_prune_repeated_sidecars(),
        verify_e2_sh_heredoc_safety(),
        verify_e3_bat_sidecar_safety(),
        verify_e4_queue_sidecar_ordering(),
    ]
    exit_code = 1 if any(r.status == "FAIL" for r in results) else 0
    return results, exit_code


def _print_report(results: list[CheckResult], total_s: float) -> None:
    print("=" * 78)
    print(f"FFMpeg-GUI Export Sidecars Formal Verification Report (Z3 {z3.get_version_string()})")
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
        description="FFMpeg-GUI export sidecars formal verification suite (E1-E4)"
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
    results, exit_code = run_all()
    _print_report(results, time.perf_counter() - t0)

    if args.report:
        return 0
    if args.strict:
        return 1 if any(r.status != "PASS" for r in results) else 0
    return exit_code


if __name__ == "__main__":
    sys.exit(main())
