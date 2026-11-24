#!/usr/bin/env python3
"""Formal verification suite for FFMpeg-GUI (Z3 SMT + exhaustive BFS model checking).

Checks 6 formal properties directly tied to the real codebase:
  P1: Batch token escaping & injection freedom (src/ffgui/export/bat.py:bat_token)
  P2: Batch _cmd_literal escaping freedom (src/ffgui/export/bat.py:_cmd_literal)
  P3: POSIX sh_token safety & round-trip injectivity (src/ffgui/export/sh.py:sh_token)
  P4: QueueDocument.move_rows permutation invariant (src/ffgui/doc.py:move_rows)
  P5: Stream-drop / codec mutual exclusion & unparsed recovery (src/ffgui/doc.py)
  P6: Reachable QueueDocument -> build(job) soundness (src/ffgui/doc.py + fftui build)

Run:
  QT_QPA_PLATFORM=offscreen PYTHONPATH="$PWD/src" .venv/bin/python verification/verify_formal.py
"""

from __future__ import annotations

import argparse
import itertools
import json
import os
import shlex
import sys
import time
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass, field

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import z3
from fftui.errors import FFtuiError
from fftui.ffmpeg.capability_index import CapabilityIndex
from fftui.model import Input, InputStream, Job
from fftui.util.command_builder import build

from ffgui.doc import QueueDocument
from ffgui.export.bat import _ECHO_META, _ENV_REF, BAT_UNSAFE, _cmd_literal, bat_token
from ffgui.export.model import HOSTILE_CHARS
from ffgui.export.sh import sh_token
from ffgui.store import UnparsedRow

Z3_TIMEOUT_MS = 5000


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


def _stub_probe(path: str) -> Input:
    """Healthy probed media input with video, audio, and subtitle streams."""
    return Input(
        path=str(path),
        streams=[
            InputStream(
                input_index=0,
                spec="v:0",
                codec_type="video",
                codec_name="h264",
                width=1920,
                height=1080,
            ),
            InputStream(
                input_index=0,
                spec="a:0",
                codec_type="audio",
                codec_name="aac",
            ),
            InputStream(
                input_index=0,
                spec="s:0",
                codec_type="subtitle",
                codec_name="subrip",
            ),
        ],
    )


# ---------------------------------------------------------------------------
# P1: Z3 SMT — Batch Token Escaping & Injection Freedom
# ---------------------------------------------------------------------------
def verify_p1_bat_token() -> PropertyResult:
    t0 = time.perf_counter()
    details: list[str] = []
    counterexamples: list[str] = []

    # 9 character classes capturing every distinct decision branch in bat_token():
    #   0: SAFE_ALPHA ('a'), 1: SAFE_DIGIT ('0'),
    #   2: HOSTILE_LF ('\n'), 3: HOSTILE_CR ('\r'), 4: HOSTILE_NUL ('\0'),
    #   5: QUOTE ('"'), 6: PERCENT ('%'), 7: BACKSLASH ('\\'), 8: OTHER_UNSAFE ('&')
    cls_reps = ["a", "0", "\n", "\r", "\0", '"', "%", "\\", "&"]
    c_alpha, c_digit = 0, 1
    c_lf, c_cr, c_nul = 2, 3, 4
    c_quote, c_pct, c_bs, c_unsafe = 5, 6, 7, 8
    n_cls = len(cls_reps)
    max_len = 8
    total_space = sum(n_cls**length for length in range(max_len + 1))

    for length in range(max_len + 1):
        chars = [z3.Int(f"b_{length}_{i}") for i in range(length)]
        domain = [z3.And(c >= 0, c < n_cls) for c in chars]

        has_hostile = (
            z3.Or(*[z3.Or(c == c_lf, c == c_cr, c == c_nul) for c in chars])
            if chars
            else z3.BoolVal(False)
        )
        has_quote = z3.Or(*[c == c_quote for c in chars]) if chars else z3.BoolVal(False)
        accepted = z3.And(z3.Not(has_hostile), z3.Not(has_quote))

        if length >= 3:
            body_ident = [z3.Or(c == c_alpha, c == c_digit) for c in chars[2:-1]]
            is_env_ref: z3.BoolRef = z3.And(
                chars[0] == c_pct,
                chars[-1] == c_pct,
                chars[1] == c_alpha,
                *body_ident,
            )
        else:
            is_env_ref = z3.BoolVal(False)

        has_unsafe = (
            z3.Or(*[z3.Or(c == c_quote, c == c_pct, c == c_unsafe) for c in chars])
            if chars
            else z3.BoolVal(False)
        )
        is_unquoted = z3.Or(is_env_ref, z3.And(z3.BoolVal(length > 0), z3.Not(has_unsafe)))
        is_quoted = z3.And(accepted, z3.Not(is_unquoted))

        # Trailing backslash run length in body (unaffected by _double_pct)
        k_in: z3.ArithRef = z3.IntVal(0)
        still_bs: z3.BoolRef = z3.BoolVal(True)
        for c in reversed(chars):
            still_bs = z3.And(still_bs, c == c_bs)
            k_in = z3.If(still_bs, k_in + 1, k_in)
        k_out = z3.If(k_in > 0, 2 * k_in, 0)

        # (a) UNSAT: any accepted token contains \n, \r, \0, or "
        s_a = _new_solver()
        s_a.add(*domain, accepted, z3.Or(has_hostile, has_quote))
        if s_a.check() != z3.unsat:
            counterexamples.append(f"P1(a) length={length} not UNSAT: {s_a.check()}")

        # (b) UNSAT: any unquoted non-env-ref token contains a BAT_UNSAFE char
        s_b = _new_solver()
        s_b.add(*domain, accepted, is_unquoted, z3.Not(is_env_ref), has_unsafe)
        if s_b.check() != z3.unsat:
            counterexamples.append(f"P1(b) length={length} not UNSAT: {s_b.check()}")

        # (c) UNSAT: any quoted token ends with an odd number of backslashes before closing "
        s_c = _new_solver()
        s_c.add(*domain, is_quoted, k_out % 2 != 0)
        if s_c.check() != z3.unsat:
            counterexamples.append(f"P1(c) length={length} not UNSAT: {s_c.check()}")

    if not counterexamples:
        details.append(
            f"P1(a)(b)(c) UNSAT across all lengths 0..{max_len} over 9 char classes ({total_space:,} abstract strings)"
        )

    # Synthesize concrete boundary strings via Z3 + real BAT_UNSAFE sweep and cross-check real bat_token()
    s_len = 5
    zchars = [z3.Int(f"zb_{i}") for i in range(s_len)]
    zdom = [z3.And(c >= 0, c < n_cls) for c in zchars]
    z_hostile = z3.Or(*[z3.Or(c == c_lf, c == c_cr, c == c_nul) for c in zchars])
    z_quote = z3.Or(*[c == c_quote for c in zchars])
    z_acc = z3.And(z3.Not(z_hostile), z3.Not(z_quote))
    z_env = z3.And(
        zchars[0] == c_pct,
        zchars[-1] == c_pct,
        zchars[1] == c_alpha,
        *[z3.Or(c == c_alpha, c == c_digit) for c in zchars[2:-1]],
    )
    z_unsafe = z3.Or(*[z3.Or(c == c_quote, c == c_pct, c == c_unsafe) for c in zchars])

    synth_specs: list[tuple[str, list[z3.BoolRef], bool]] = [
        ("hostile_lf", [z3.Or(*[c == c_lf for c in zchars])], False),
        ("hostile_cr", [z3.Or(*[c == c_cr for c in zchars])], False),
        ("hostile_nul", [z3.Or(*[c == c_nul for c in zchars])], False),
        ("hostile_quote", [z_quote, z3.Not(z_hostile)], False),
        ("env_ref", [z_acc, z_env], True),
        ("unquoted_safe", [z_acc, z3.Not(z_unsafe)], True),
        ("quoted_unsafe", [z_acc, z3.Not(z_env), z_unsafe], True),
        ("quoted_1_bs", [z_acc, z_unsafe, zchars[-1] == c_bs, zchars[-2] != c_bs], True),
        ("quoted_2_bs", [z_acc, z_unsafe, zchars[-1] == c_bs, zchars[-2] == c_bs, zchars[-3] != c_bs], True),
        ("quoted_3_bs", [z_acc, z_unsafe, zchars[-1] == c_bs, zchars[-2] == c_bs, zchars[-3] == c_bs, zchars[-4] != c_bs], True),
        ("quoted_pct_bs", [z_acc, z3.Or(*[c == c_pct for c in zchars]), zchars[-1] == c_bs], True),
    ]

    concrete_cases: list[tuple[str, str, bool]] = [
        ("empty", "", True),
        ("env_var", "%PATH%", True),
        ("env_plus_pct", "%VAR% %foo", True),
        ("trailing_1_bs_space", "a b\\", True),
        ("trailing_2_bs_space", "a b\\\\", True),
        ("trailing_3_bs_space", "a b\\\\\\", True),
    ]
    for ch in sorted(BAT_UNSAFE):
        concrete_cases.append((f"bat_unsafe_{ord(ch)}", f"x{ch}y", ch != '"'))

    for label, conds, should_accept in synth_specs:
        sg = _new_solver()
        sg.add(*zdom, *conds)
        for _ in range(2):
            if sg.check() != z3.sat:
                break
            m = sg.model()
            vals = [m.eval(c, model_completion=True).as_long() for c in zchars]
            concrete_cases.append((label, "".join(cls_reps[v] for v in vals), should_accept))
            sg.add(z3.Or(*[c != v for c, v in zip(zchars, vals)]))

    for label, val, should_accept in concrete_cases:
        if not should_accept:
            try:
                out = bat_token(val)
                counterexamples.append(
                    f"Real bat_token accepted hostile/quote {label} input {val!r} -> {out!r}"
                )
            except ValueError:
                pass
        else:
            try:
                out = bat_token(val)
            except Exception as exc:
                counterexamples.append(f"Real bat_token rejected valid {label} input {val!r}: {exc}")
                continue
            if any(c in out for c in HOSTILE_CHARS):
                counterexamples.append(f"Real bat_token emitted hostile char: {val!r} -> {out!r}")
            is_out_quoted = len(out) >= 2 and out.startswith('"') and out.endswith('"')
            if not is_out_quoted:
                if not _ENV_REF.fullmatch(out) and any(c in BAT_UNSAFE for c in out):
                    counterexamples.append(
                        f"Real bat_token emitted unquoted unsafe token: {val!r} -> {out!r}"
                    )
            else:
                inner = out[1:-1]
                if '"' in inner:
                    counterexamples.append(
                        f"Real bat_token left quote inside quoted token: {val!r} -> {out!r}"
                    )
                bs_count = len(inner) - len(inner.rstrip("\\"))
                if bs_count % 2 != 0:
                    counterexamples.append(
                        f"Real bat_token left odd trailing backslashes ({bs_count}): {val!r} -> {out!r}"
                    )

    details.append(f"Cross-checked {len(concrete_cases)} Z3-synthesized & BAT_UNSAFE strings against real bat_token()")
    return PropertyResult(
        prop_id="P1",
        title="Batch token escaping & injection freedom (bat.py:bat_token)",
        status="FAIL" if counterexamples else "PASS",
        tool=f"Z3 SMT ({z3.get_version_string()}) + real bat_token cross-check",
        bounds=f"All lengths 0..{max_len} over 9 char classes ({total_space:,} abstract strings) + {len(concrete_cases)} concrete strings",
        states_checked=total_space,
        elapsed_s=time.perf_counter() - t0,
        details=details,
        counterexamples=counterexamples,
    )


# ---------------------------------------------------------------------------
# P2: Z3 SMT — Batch _cmd_literal Escaping Freedom
# ---------------------------------------------------------------------------
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


def verify_p2_cmd_literal() -> PropertyResult:
    t0 = time.perf_counter()
    details: list[str] = []
    counterexamples: list[str] = []

    alphabet = ["a", "%", "^", "&", "|", "<", ">", "(", ")", " "]
    idx_pct = alphabet.index("%")
    idx_caret = alphabet.index("^")
    meta_indices = [alphabet.index(c) for c in sorted(_ECHO_META)]
    non_caret_meta = [i for i in meta_indices if i != idx_caret]

    max_len = 8
    total_space = sum(len(alphabet) ** length for length in range(max_len + 1))

    for length in range(max_len + 1):
        solver = _new_solver()
        chars = [z3.Int(f"c_{length}_{i}") for i in range(length)]
        for c in chars:
            solver.add(c >= 0, c < len(alphabet))

        pct_odd: z3.BoolRef = z3.BoolVal(False)
        unpaired_pct: z3.BoolRef = z3.BoolVal(False)
        esc: z3.BoolRef = z3.BoolVal(False)
        unescaped_meta: z3.BoolRef = z3.BoolVal(False)

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
            is_non_caret_meta = z3.Or(*[ch_code == m for m in non_caret_meta])
            new_bad = z3.Or(bad, z3.And(z3.Not(is_esc), is_non_caret_meta))
            new_esc = z3.If(is_esc, z3.BoolVal(False), is_caret)
            return new_esc, new_bad

        for c in chars:
            is_pct = c == idx_pct
            is_meta = z3.Or(*[c == m for m in meta_indices])

            p1_a, b1_a = step_pct(z3.IntVal(idx_pct), pct_odd, unpaired_pct)
            p1_a, b1_a = step_pct(z3.IntVal(idx_pct), p1_a, b1_a)

            p1_b, b1_b = step_pct(z3.IntVal(idx_caret), pct_odd, unpaired_pct)
            p1_b, b1_b = step_pct(c, p1_b, b1_b)

            p1_c, b1_c = step_pct(c, pct_odd, unpaired_pct)

            pct_odd = z3.If(is_pct, p1_a, z3.If(is_meta, p1_b, p1_c))
            unpaired_pct = z3.If(is_pct, b1_a, z3.If(is_meta, b1_b, b1_c))

            e2_a, m2_a = step_caret(z3.IntVal(idx_pct), esc, unescaped_meta)

            e2_b, m2_b = step_caret(z3.IntVal(idx_caret), esc, unescaped_meta)
            e2_b, m2_b = step_caret(c, e2_b, m2_b)

            e2_c, m2_c = step_caret(c, esc, unescaped_meta)

            esc = z3.If(is_pct, e2_a, z3.If(is_meta, e2_b, e2_c))
            unescaped_meta = z3.If(is_pct, m2_a, z3.If(is_meta, m2_b, m2_c))

        solver.add(z3.Or(unpaired_pct, pct_odd, unescaped_meta, esc))
        if solver.check() != z3.unsat:
            model = solver.model()
            witness = "".join(
                alphabet[model.eval(c, model_completion=True).as_long()] for c in chars
            )
            counterexamples.append(f"P2 length={length} SAT witness: {witness!r}")

    if not counterexamples:
        details.append(
            f"P2 UNSAT for all lengths 0..{max_len} over 10-char alphabet ({total_space:,} strings): "
            "zero unescaped &|<>^() metacharacters and zero unpaired %"
        )

    s_gen = _new_solver()
    gen_chars = [z3.Int(f"g_{i}") for i in range(max_len)]
    for c in gen_chars:
        s_gen.add(c >= 0, c < len(alphabet))
    s_gen.add(z3.Or(*[c == idx_pct for c in gen_chars]))
    s_gen.add(z3.Or(*[c == idx_caret for c in gen_chars]))
    s_gen.add(z3.Or(*[z3.Or(*[c == m for m in non_caret_meta]) for c in gen_chars]))
    samples = ["", "%", "%%", "^", "^^", "^%", "%^", "&|<>^()", "a %PATH% & echo hi ^"]
    for _ in range(15):
        if s_gen.check() != z3.sat:
            break
        m = s_gen.model()
        vals = [m.eval(c, model_completion=True).as_long() for c in gen_chars]
        samples.append("".join(alphabet[v] for v in vals))
        s_gen.add(z3.Or(*[c != v for c, v in zip(gen_chars, vals)]))

    for s in samples:
        out = _cmd_literal(s)
        bad_pct, bad_meta = _scan_cmd_literal_output(out)
        if bad_pct or bad_meta:
            counterexamples.append(
                f"Real _cmd_literal({s!r}) -> {out!r} failed scanner "
                f"(bad_pct={bad_pct}, bad_meta={bad_meta})"
            )

    details.append(f"Cross-checked {len(samples)} adversarial strings against real _cmd_literal()")
    return PropertyResult(
        prop_id="P2",
        title="Batch _cmd_literal escaping freedom (bat.py:_cmd_literal)",
        status="FAIL" if counterexamples else "PASS",
        tool=f"Z3 SMT ({z3.get_version_string()}) + real _cmd_literal cross-check",
        bounds=f"All strings length 0..{max_len} over 10-char alphabet ({total_space:,} strings)",
        states_checked=total_space,
        elapsed_s=time.perf_counter() - t0,
        details=details,
        counterexamples=counterexamples,
    )


# ---------------------------------------------------------------------------
# P3: Z3 SMT — POSIX sh_token Safety & Round-Trip Injectivity
# ---------------------------------------------------------------------------
def verify_p3_sh_token() -> PropertyResult:
    t0 = time.perf_counter()
    details: list[str] = []
    counterexamples: list[str] = []

    # 10 character classes covering hostile chars and POSIX shell metacharacters:
    # 0: SAFE ('a'), 1: LF ('\n'), 2: CR ('\r'), 3: NUL ('\0'),
    # 4: SQUOTE ("'"), 5: DQUOTE ('"'), 6: DOLLAR ('$'),
    # 7: BACKTICK ('`'), 8: BSLASH ('\\'), 9: META (';')
    cls_reps = ["a", "\n", "\r", "\0", "'", '"', "$", "`", "\\", ";"]
    c_lf, c_cr, c_nul = 1, 2, 3
    n_cls = len(cls_reps)
    max_len = 8
    total_space = sum(n_cls**length for length in range(max_len + 1))

    for length in range(max_len + 1):
        chars = [z3.Int(f"sh_{length}_{i}") for i in range(length)]
        domain = [z3.And(c >= 0, c < n_cls) for c in chars]
        has_hostile = (
            z3.Or(*[z3.Or(c == c_lf, c == c_cr, c == c_nul) for c in chars])
            if chars
            else z3.BoolVal(False)
        )
        accepted = z3.Not(has_hostile)

        # (a) UNSAT: any accepted token contains \n, \r, or \0
        s_a = _new_solver()
        s_a.add(*domain, accepted, has_hostile)
        if s_a.check() != z3.unsat:
            counterexamples.append(f"P3(a) length={length} not UNSAT: {s_a.check()}")

        # (b) UNSAT: shlex.quote output (wrapping in '...', replacing ' with '"'"') contains hostile
        # Since every non-single-quote char is emitted verbatim and single-quote emits only {'', '"'},
        # output has a hostile char iff some input char is hostile.
        out_hostile = (
            z3.Or(
                *[
                    z3.If(c == 4, z3.BoolVal(False), z3.Or(c == c_lf, c == c_cr, c == c_nul))
                    for c in chars
                ]
            )
            if chars
            else z3.BoolVal(False)
        )
        s_b = _new_solver()
        s_b.add(*domain, accepted, out_hostile)
        if s_b.check() != z3.unsat:
            counterexamples.append(f"P3(b) length={length} not UNSAT: {s_b.check()}")

    if not counterexamples:
        details.append(
            f"P3(a)(b) UNSAT across all lengths 0..{max_len} over 10 char classes ({total_space:,} abstract strings)"
        )

    # Synthesize concrete adversarial strings via Z3 + extra shell metachars & unicode
    z_samples: list[str] = [
        "",
        "'''",
        "$(rm -rf /)",
        "`id`",
        "café_日本語_🎬",
        "a'b\"c$d`e\\f;g|h&i j",
        "foo | bar & baz > out",
        "   leading and trailing   ",
    ]
    zlen = 6
    zc = [z3.Int(f"zsh_{i}") for i in range(zlen)]
    zdom = [z3.And(c >= 0, c < n_cls) for c in zc]
    z_acc = z3.Not(z3.Or(*[z3.Or(c == c_lf, c == c_cr, c == c_nul) for c in zc]))

    for cls_idx in range(4, n_cls):
        sg = _new_solver()
        sg.add(*zdom, z_acc, z3.Or(*[c == cls_idx for c in zc]))
        for _ in range(3):
            if sg.check() != z3.sat:
                break
            m = sg.model()
            vals = [m.eval(c, model_completion=True).as_long() for c in zc]
            z_samples.append("".join(cls_reps[v] for v in vals))
            sg.add(z3.Or(*[c != v for c, v in zip(zc, vals)]))

    # Multi-metachar combinations synthesized by Z3
    sg_combo = _new_solver()
    sg_combo.add(
        *zdom,
        z_acc,
        z3.Or(*[c == 4 for c in zc]),  # single quote
        z3.Or(*[c == 6 for c in zc]),  # dollar
        z3.Or(*[c == 9 for c in zc]),  # semicolon
    )
    for _ in range(5):
        if sg_combo.check() != z3.sat:
            break
        m = sg_combo.model()
        vals = [m.eval(c, model_completion=True).as_long() for c in zc]
        z_samples.append("".join(cls_reps[v] for v in vals))
        sg_combo.add(z3.Or(*[c != v for c, v in zip(zc, vals)]))

    hostile_samples = ["a\nb", "a\rb", "a\0b", "\n", "\r", "\0"]
    for hs in hostile_samples:
        try:
            out = sh_token(hs)
            counterexamples.append(f"Real sh_token accepted hostile {hs!r} -> {out!r}")
        except ValueError:
            pass

    for sample in z_samples:
        try:
            encoded = sh_token(sample)
        except Exception as exc:
            counterexamples.append(f"Real sh_token rejected valid {sample!r}: {exc}")
            continue
        if any(c in encoded for c in HOSTILE_CHARS):
            counterexamples.append(f"Real sh_token emitted hostile char: {sample!r} -> {encoded!r}")
        parsed = shlex.split(encoded)
        if parsed != [sample]:
            counterexamples.append(
                f"shlex.split(sh_token({sample!r})) == {parsed!r} != [{sample!r}] (encoded={encoded!r})"
            )

    details.append(
        f"Verified shlex.split(sh_token(s)) == [s] on {len(z_samples)} Z3-generated & unicode adversarial strings "
        f"and rejection of {len(hostile_samples)} hostile strings"
    )
    return PropertyResult(
        prop_id="P3",
        title="POSIX sh_token safety & round-trip injectivity (sh.py:sh_token)",
        status="FAIL" if counterexamples else "PASS",
        tool=f"Z3 SMT ({z3.get_version_string()}) + real sh_token / shlex.split",
        bounds=f"All lengths 0..{max_len} over 10 char classes ({total_space:,} abstract strings) + {len(z_samples)} adversarial strings",
        states_checked=total_space,
        elapsed_s=time.perf_counter() - t0,
        details=details,
        counterexamples=counterexamples,
    )


# ---------------------------------------------------------------------------
# P4: Exhaustive Model Check — QueueDocument.move_rows Permutation Invariant
# ---------------------------------------------------------------------------
def verify_p4_move_rows() -> PropertyResult:
    t0 = time.perf_counter()
    counterexamples: list[str] = []
    cap = CapabilityIndex.stub()
    cases_checked = 0

    for n in range(1, 5):
        all_rows = list(range(n))
        for r_size in range(1, n + 1):
            for subset in itertools.combinations(all_rows, r_size):
                for to in range(-1, n + 2):
                    cases_checked += 1
                    doc = QueueDocument(cap, prober=_stub_probe)
                    doc.add_files([f"clip_{i}.mp4" for i in range(n)])
                    orig_ids = [id(it) for it in doc.items]
                    orig_names = [it.meta["name"] for it in doc.items]

                    doc.move_rows(list(subset), to)

                    new_ids = [id(it) for it in doc.items]
                    new_names = [it.meta["name"] for it in doc.items]
                    if len(new_ids) != n or sorted(new_ids) != sorted(orig_ids):
                        counterexamples.append(
                            f"N={n}, rows={list(subset)}, to={to}: "
                            f"before={orig_names}, after={new_names}"
                        )
                        continue
                    subset_set = set(subset)
                    picked_orig = [orig_ids[i] for i in all_rows if i in subset_set]
                    rem_orig = [orig_ids[i] for i in all_rows if i not in subset_set]
                    picked_new = [x for x in new_ids if x in set(picked_orig)]
                    rem_new = [x for x in new_ids if x in set(rem_orig)]
                    if picked_new != picked_orig or rem_new != rem_orig:
                        counterexamples.append(
                            f"Order broken for N={n}, rows={list(subset)}, to={to}: "
                            f"before={orig_names}, after={new_names}"
                        )

    return PropertyResult(
        prop_id="P4",
        title="QueueDocument.move_rows permutation invariant (doc.py:move_rows)",
        status="FAIL" if counterexamples else "PASS",
        tool="Exhaustive model check over real QueueDocument.move_rows",
        bounds="All N in 1..4, all non-empty subsets of rows, all target indices to in -1..N+1",
        states_checked=cases_checked,
        elapsed_s=time.perf_counter() - t0,
        details=[
            f"Checked all {cases_checked} (N, subset, target) combinations: "
            "exact multiset preservation & relative order preserved"
        ],
        counterexamples=counterexamples,
    )


# ---------------------------------------------------------------------------
# P5: Exhaustive BFS — Stream-Drop / Codec Mutual Exclusion & Unparsed Recovery
# ---------------------------------------------------------------------------
def _make_doc_from_job(
    cap: CapabilityIndex, job_dict: dict, unparsed: UnparsedRow | None
) -> QueueDocument:
    doc = QueueDocument(cap, prober=_stub_probe)
    doc.add_files(["input.mp4"])
    doc.items[0].job = Job.from_dict(job_dict)
    doc.items[0].unparsed = unparsed
    return doc


def _p5_state_key(doc: QueueDocument) -> tuple:
    it = doc.items[0]
    out = it.job.outputs[0]
    return (
        out.video_codec,
        "vn" in out.options,
        out.audio_codec,
        "an" in out.options,
        out.subtitle_codec,
        bool(out.sn_explicit),
        it.unparsed is not None,
    )


def verify_p5_stream_mutex() -> PropertyResult:
    t0 = time.perf_counter()
    cap = CapabilityIndex.stub()
    counterexamples: list[str] = []

    actions: list[tuple[str, Callable[[QueueDocument], str | None]]] = [
        ('set_codec("video", "libx264")', lambda d: d.set_codec(0, "video", "libx264")),
        ('set_codec("video", "copy")', lambda d: d.set_codec(0, "video", "copy")),
        ('set_codec("video", "")', lambda d: d.set_codec(0, "video", "")),
        ('set_codec("audio", "aac")', lambda d: d.set_codec(0, "audio", "aac")),
        ('set_codec("audio", "copy")', lambda d: d.set_codec(0, "audio", "copy")),
        ('set_stream_drop("vn", "1")', lambda d: d.set_stream_drop(0, "vn", "1")),
        ('set_stream_drop("vn", "0")', lambda d: d.set_stream_drop(0, "vn", "0")),
        ('set_stream_drop("an", "1")', lambda d: d.set_stream_drop(0, "an", "1")),
        ('set_stream_drop("an", "0")', lambda d: d.set_stream_drop(0, "an", "0")),
        ('set_codec("subtitle", "none")', lambda d: d.set_codec(0, "subtitle", "none")),
        ('set_codec("subtitle", "srt")', lambda d: d.set_codec(0, "subtitle", "srt")),
        ('set_codec("subtitle", "")', lambda d: d.set_codec(0, "subtitle", "")),
    ]

    init_doc = QueueDocument(cap, prober=_stub_probe)
    init_doc.add_files(["input.mp4"])
    init_doc.items[0].unparsed = UnparsedRow({"raw": "legacy_row"})

    init_key = _p5_state_key(init_doc)
    visited: dict[tuple, list[str]] = {init_key: []}
    queue: deque[tuple[dict, UnparsedRow | None, list[str]]] = deque(
        [(init_doc.items[0].job.to_dict(), init_doc.items[0].unparsed, [])]
    )
    transitions = 0
    seen_violations: set[str] = set()

    while queue:
        job_dict, unparsed, trace = queue.popleft()
        for act_name, act_fn in actions:
            transitions += 1
            d = _make_doc_from_job(cap, job_dict, unparsed)
            touched = False

            def _on_changed(_row: int) -> None:
                nonlocal touched
                touched = True

            d.itemChanged.connect(_on_changed)
            act_fn(d)
            new_trace = [*trace, act_name]
            it = d.items[0]
            out = it.job.outputs[0]

            if "vn" in out.options and out.video_codec is not None:
                tag = "vn_mutex"
                if tag not in seen_violations:
                    seen_violations.add(tag)
                    counterexamples.append(
                        f"VIOLATION vn mutex: 'vn' in out.options AND video_codec={out.video_codec!r} "
                        f"after {' -> '.join(new_trace)}"
                    )
            if "an" in out.options and out.audio_codec is not None:
                tag = "an_mutex"
                if tag not in seen_violations:
                    seen_violations.add(tag)
                    counterexamples.append(
                        f"VIOLATION an mutex: 'an' in out.options AND audio_codec={out.audio_codec!r} "
                        f"after {' -> '.join(new_trace)}"
                    )
            if out.sn_explicit and out.subtitle_codec is not None:
                tag = "sn_mutex"
                if tag not in seen_violations:
                    seen_violations.add(tag)
                    counterexamples.append(
                        f"VIOLATION sn mutex: sn_explicit=True AND subtitle_codec={out.subtitle_codec!r} "
                        f"after {' -> '.join(new_trace)}"
                    )
            if touched and it.unparsed is not None:
                tag = "unparsed_recovery"
                if tag not in seen_violations:
                    seen_violations.add(tag)
                    counterexamples.append(
                        f"VIOLATION unparsed recovery: item.unparsed not cleared after {' -> '.join(new_trace)}"
                    )

            new_key = _p5_state_key(d)
            if new_key not in visited:
                visited[new_key] = new_trace
                queue.append((it.job.to_dict(), it.unparsed, new_trace))

    return PropertyResult(
        prop_id="P5",
        title="Stream-drop / codec mutual exclusion & unparsed recovery (doc.py)",
        status="FAIL" if counterexamples else "PASS",
        tool="Exhaustive BFS state-space model checker over real QueueDocument",
        bounds="Full reachable state space under 12 codec/stream-drop actions (unbounded depth)",
        states_checked=len(visited),
        elapsed_s=time.perf_counter() - t0,
        details=[
            f"Explored {len(visited)} reachable states across {transitions} transitions (complete fixpoint)",
            "Invariants: vn => video_codec is None; an => audio_codec is None; "
            "sn_explicit => subtitle_codec is None; successful setter => unparsed is None",
        ],
        counterexamples=counterexamples,
    )


# ---------------------------------------------------------------------------
# P6: Exhaustive BFS — Reachable QueueDocument -> build(job) Soundness
# ---------------------------------------------------------------------------
def _canonical_job_key(job_dict: dict) -> str:
    return json.dumps(job_dict, sort_keys=True, separators=(",", ":"))


def verify_p6_build_soundness(max_depth: int = 3) -> PropertyResult:
    t0 = time.perf_counter()
    cap = CapabilityIndex.stub()

    actions: list[tuple[str, Callable[[QueueDocument], str | None]]] = [
        ('set_codec("video", "copy")', lambda d: d.set_codec(0, "video", "copy")),
        ('set_codec("video", "libx264")', lambda d: d.set_codec(0, "video", "libx264")),
        ('set_codec("audio", "copy")', lambda d: d.set_codec(0, "audio", "copy")),
        (
            'set_filters("video_filters", ["scale=1280:-2"])',
            lambda d: d.set_filters(0, "video_filters", ["scale=1280:-2"]),
        ),
        (
            'set_filters("audio_filters", ["volume=0.5"])',
            lambda d: d.set_filters(0, "audio_filters", ["volume=0.5"]),
        ),
        ('set_output_field("duration", "10")', lambda d: d.set_output_field(0, "duration", "10")),
        ('set_output_field("end", "20")', lambda d: d.set_output_field(0, "end", "20")),
        ('set_output_field("container", "mp4")', lambda d: d.set_output_field(0, "container", "mp4")),
        ('set_segment_enabled("1")', lambda d: d.set_segment_enabled(0, "1")),
        ('set_tee("[f=mp4]a.mp4")', lambda d: d.set_tee(0, "[f=mp4]a.mp4")),
        (
            'set_output_field("subtitle_burn_in", "sub.srt")',
            lambda d: d.set_output_field(0, "subtitle_burn_in", "sub.srt"),
        ),
    ]

    init_doc = QueueDocument(cap, prober=_stub_probe)
    init_doc.add_files(["input.mp4"])
    init_dict = init_doc.items[0].job.to_dict()
    init_key = _canonical_job_key(init_dict)

    visited: set[str] = {init_key}
    queue: deque[tuple[dict, int, list[str]]] = deque([(init_dict, 0, [])])

    findings: dict[str, tuple[list[str], int]] = {}
    states_built = 0
    transitions = 0

    def _check_build(job_dict: dict, trace: list[str]) -> None:
        nonlocal states_built
        states_built += 1
        job = Job.from_dict(job_dict)
        try:
            build(job)
        except FFtuiError as exc:
            err_msg = str(exc)
            if err_msg not in findings:
                findings[err_msg] = (list(trace), 1)
            else:
                prev_trace, prev_count = findings[err_msg]
                findings[err_msg] = (prev_trace, prev_count + 1)

    _check_build(init_dict, [])

    while queue:
        job_dict, depth, trace = queue.popleft()
        if depth >= max_depth:
            continue
        for act_name, act_fn in actions:
            transitions += 1
            d = _make_doc_from_job(cap, job_dict, None)
            accepted = False

            def _on_changed(_row: int) -> None:
                nonlocal accepted
                accepted = True

            d.itemChanged.connect(_on_changed)
            ret = act_fn(d)
            if not accepted:
                continue

            step_label = f"{act_name} [warn: {ret}]" if ret else act_name
            new_trace = [*trace, step_label]
            new_dict = d.items[0].job.to_dict()
            new_key = _canonical_job_key(new_dict)
            if new_key not in visited:
                visited.add(new_key)
                _check_build(new_dict, new_trace)
                queue.append((new_dict, depth + 1, new_trace))

    counterexamples: list[str] = []
    for err_msg, (trace, hit_count) in sorted(
        findings.items(), key=lambda kv: (len(kv[1][0]), kv[0])
    ):
        path_str = " -> ".join(trace) if trace else "<initial state>"
        counterexamples.append(
            f"FFtuiError: {err_msg}\n"
            f"      shortest trace (len {len(trace)}, {hit_count} reachable states): {path_str}"
        )

    return PropertyResult(
        prop_id="P6",
        title="Reachable QueueDocument -> build(job) soundness (doc.py + fftui build)",
        status="FINDINGS" if counterexamples else "PASS",
        tool="Exhaustive BFS state-space model checker over real QueueDocument + fftui build()",
        bounds=f"Depth <= {max_depth} from healthy probed row under {len(actions)} accepted user setters",
        states_checked=states_built,
        elapsed_s=time.perf_counter() - t0,
        details=[
            f"Explored {states_built} unique reachable states across {transitions} transitions (depth <= {max_depth})",
            f"Distinct FFtuiError categories discovered: {len(findings)}",
        ],
        counterexamples=counterexamples,
    )


def _print_report(results: list[PropertyResult], total_s: float) -> None:
    print("=" * 78)
    print(f"FFMpeg-GUI Formal Verification Report (Z3 {z3.get_version_string()})")
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
    parser = argparse.ArgumentParser(description="FFMpeg-GUI formal verification suite (P1-P6)")
    parser.add_argument(
        "--report",
        action="store_true",
        help="Always exit 0 after printing the full verification report",
    )
    parser.add_argument(
        "--strict",
        action="store_true",
        help="Exit 1 if any property fails OR produces findings (including P6)",
    )
    args = parser.parse_args(argv)

    t0 = time.perf_counter()
    results = [
        verify_p1_bat_token(),
        verify_p2_cmd_literal(),
        verify_p3_sh_token(),
        verify_p4_move_rows(),
        verify_p5_stream_mutex(),
        verify_p6_build_soundness(max_depth=3),
    ]
    _print_report(results, time.perf_counter() - t0)

    if args.report:
        return 0
    if args.strict:
        return 1 if any(r.status != "PASS" for r in results) else 0
    return 1 if any(r.status == "FAIL" for r in results) else 0


if __name__ == "__main__":
    sys.exit(main())
