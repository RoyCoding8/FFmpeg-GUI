"""Serializer tests: token tables (hand-derived), golden files, real-ffmpeg execution."""

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
from fftui.model import Chapter, Input, Job, Output
from fftui.util.command_builder import build, build_concat_job, sidecars_for

from ffgui.export.bat import BAT_UNSAFE, bat_token
from ffgui.export.orchestrate import script_for_job, script_for_queue
from ffgui.export.sh import sh_token

HASH = "0123456789abcdef"

EXE = r"C:\ffgui-bin\ffmpeg.EXE"
SCRATCH_DIR = r"C:\ffgui-scratch"


def _pin_env(monkeypatch):
    import fftui.util.command_builder as cb
    monkeypatch.setattr("fftui.ffmpeg.runtime.ffmpeg_exe", lambda: EXE)
    real = cb._scratch_path
    monkeypatch.setattr(cb, "_scratch_path",
                        lambda prefix, content, ext:
                        SCRATCH_DIR + "\\" + os.path.basename(real(prefix, content, ext)))


BAT_TOKENS = [
    ("plain", "plain"),
    ("", '""'),
    ("with space", '"with space"'),
    ("C:\\Program Files\\x.mp4", '"C:\\Program Files\\x.mp4"'),
    ("trailing\\", "trailing\\"),
    ("deep\\\\", "deep\\\\"),
    ("trail ing\\", '"trail ing\\\\"'),
    ("50%", '"50%%"'),
    ("out_%03d.png", '"out_%%03d.png"'),
    ("clip_100%.mp4", '"clip_100%%.mp4"'),
    ("a&b", '"a&b"'),
    ("(paren)", '"(paren)"'),
    ("it's.mp4", '"it\'s.mp4"'),
    ("x=y", '"x=y"'),
    ("a,b;c", '"a,b;c"'),
    ("bang!", '"bang!"'),
    ("^caret", '"^caret"'),
    ("%USERPROFILE%", "%USERPROFILE%"),
]

SH_TOKENS = [
    ("plain", "plain"),
    ("", "''"),
    ("with space", "'with space'"),
    ("$(evil)", "'$(evil)'"),
    ("back`tick", "'back`tick'"),
    ("$dollar", "'$dollar'"),
    ("*glob*", "'*glob*'"),
    ("it's", '\'it\'"\'"\'s\''),
    ("semi;colon", "'semi;colon'"),
]


@pytest.mark.parametrize(("token", "want"), BAT_TOKENS)
def test_bat_token(token, want):
    assert bat_token(token) == want


@pytest.mark.parametrize(("token", "want"), SH_TOKENS)
def test_sh_token(token, want):
    assert sh_token(token) == want


@pytest.mark.parametrize("bad", ["a\nb", "a\rb", "a\0b"])
def test_bat_token_rejects_newlines(bad):
    with pytest.raises(ValueError):
        bat_token(bad)


def test_bat_token_rejects_double_quote():
    with pytest.raises(ValueError, match="cannot be represented"):
        bat_token('has"quote')


@pytest.mark.parametrize("bad", ["a\nb", "a\rb", "a\0b"])
def test_sh_token_rejects_newlines(bad):
    with pytest.raises(ValueError, match="token contains"):
        sh_token(bad)


def test_sh_export_rejects_newline_path_cleanly():
    if sys.platform == "win32":
        # fftui rejects Windows paths before ffgui's own quoter runs;
        # either layer must refuse without emitting a script.
        from fftui.errors import FFtuiError
        with pytest.raises(FFtuiError, match="newline"):
            script_for_job(_job("a\ntouch PWNED\nb.mp4", "o.mp4"), "sh", HASH)
    else:
        with pytest.raises(ValueError, match="token contains"):
            script_for_job(_job("a\ntouch PWNED\nb.mp4", "o.mp4"), "sh", HASH)


@pytest.mark.parametrize("content", ["plain", "multi\nline\n"])
def test_sh_sidecar_path_validated_like_argv(content):
    """Sidecar paths rode bare shlex.quote: a newline spliced script lines
    instead of raising like every argv token does."""
    from fftui.util.command_builder import Sidecar

    from ffgui.export.model import ExportBlock, ScriptHeader
    from ffgui.export.sh import sh_script
    header = ScriptHeader(generator="t", created_utc="t",
                          ffmpeg_version_hash=HASH, summary="s")
    car = Sidecar("a\ntouch PWNED\n", content + "\n")
    with pytest.raises(ValueError, match="token contains"):
        sh_script([ExportBlock(["ffmpeg"], [car])], header)


def test_hostile_token_property_no_line_breaks():
    """Every hostile token is either cleanly rejected or rendered single-line."""
    import random
    rng = random.Random(20260906)
    alphabet = [*"aZ0 \t'\"&|<>^()!`,;=%$\\`!\n\r\0\x7féф日", "$(x)", "%PATH%", "..", "-", ""]
    for _ in range(2000):
        tok = "".join(rng.choice(alphabet) for _ in range(rng.randint(0, 12)))
        for fn in (bat_token, sh_token):
            try:
                out = fn(tok)
            except ValueError:
                continue
            assert "\n" not in out and "\r" not in out and "\0" not in out, (fn, tok, out)


def test_bat_unsafe_set():
    assert frozenset(' \t"\'&|<>^()!`,;=%') == BAT_UNSAFE


def test_round_trip_all_hostile_bat_tokens_are_wrapped():
    hostile = ["a&b", "50%", "bang!", "^", "()", "`", ",", ";", "=", " ", "\t", "'"]
    for token in hostile:
        out = bat_token(token)
        assert out.startswith('"') or out == token.replace("%", "%%") or not token


def _job(inp="a.mp4", out="o.mp4", **kw) -> Job:
    return Job(inputs=[Input(path=inp)], outputs=[Output(path=out, **kw)])


GOLDEN_JOBS = {
    "space-amp": lambda: _job("my clip & (cut).mp4", "out put.mp4"),
    "percent-literal": lambda: _job("clip_100%.mp4", "done_50%.mp4"),
    "sequence-pattern": lambda: _job("in.mp4", "frame_%03d.png"),
    "unicode": lambda: _job("видео ファイル.mp4", " выход.mkv", video_codec="libx264"),
    "single-quote": lambda: _job("it's.mp4", "won't.mp4"),
    "two-pass": lambda: _job("in.mp4", "o.mp4", video_codec="libx264",
                             video_options={"b:v": "500k"}, ),
    "concat-sidecar": lambda: build_concat_job(["p1.mp4", "p2 & more.mp4"], "joined.mp4"),
    "chapters-sidecar": lambda: _job("in.mp4", "o.mkv",
                                     chapters=[Chapter(start="0", title="Intro & titles")]),
}


def _golden_dirs() -> list[str]:
    return sorted(p.name.removesuffix(".job.json")
                  for p in (Path(__file__).parent / "golden").glob("*.job.json"))


def _regen() -> bool:
    return os.environ.get("FFGUI_REGEN_GOLDEN") == "1"


@pytest.mark.parametrize("name", _golden_dirs())
def test_golden(name, monkeypatch):
    _pin_env(monkeypatch)
    gdir = Path(__file__).parent / "golden"
    raw = json.loads((gdir / f"{name}.job.json").read_text(encoding="utf-8"))
    job = Job.from_dict(raw)
    for target, ext in (("bat", ".bat"), ("sh", ".sh")):
        text = script_for_job(job, target, HASH, created_utc="2026-09-05 00:00 UTC")
        golden = gdir / f"{name}{ext}"
        if _regen():
            golden.write_text(text, encoding="utf-8", newline="")
        # Fixtures are content-canonical: a checkout may materialize them
        # with LF even when .gitattributes asks for CRLF, so compare text
        # with line endings normalized and pin the ending contract on the
        # generated script instead of on the fixture bytes.
        have = golden.read_bytes().decode("utf-8").replace("\r\n", "\n")
        assert text.replace("\r\n", "\n") == have, f"{name}{ext} diverged"
        if target == "bat":
            assert "\r\n" in text, f"{name}.bat must use CRLF line endings"
        else:
            assert "\r" not in text, f"{name}.sh must be LF-only"


def test_script_header_present():
    text = script_for_job(_job(), "sh", HASH)
    assert "#!/bin/sh" in text and "set -e" in text
    assert "ffgui" in text and HASH in text


def test_two_pass_script_has_two_commands_and_joint(monkeypatch):
    _pin_env(monkeypatch)
    job = _job(video_codec="libx264", video_options={"b:v": "500k"})
    job.two_pass = True
    bat = script_for_job(job, "bat", HASH)
    assert bat.count("if not %errorlevel%==0 goto ffgui_fail") == 2
    assert bat.count("ffmpeg.EXE") == 2


def test_target_guard():
    with pytest.raises(ValueError):
        script_for_job(_job(), "ps1", HASH)
    with pytest.raises(ValueError):
        script_for_queue([_job()], "ps1", HASH, one_file=True)


def test_queue_one_and_n():
    jobs = [_job(), _job("b.mp4", "b_out.mp4")]
    one = script_for_queue(jobs, "bat", HASH, one_file=True)
    assert isinstance(one, str) and one.count("goto ffgui_fail") >= 1
    many = script_for_queue(jobs, "sh", HASH, one_file=False)
    assert isinstance(many, list) and len(many) == 2 and "#!/bin/sh" in many[0]


def test_bat_footer_pauses_on_both_outcomes():
    bat = script_for_job(_job(), "bat", HASH)
    assert bat.endswith("echo One of the commands failed.\r\npause\r\nendlocal & exit /b 1\r\n")
    assert "echo All commands finished.\r\npause\r\nendlocal & exit /b 0\r\n" in bat
    assert ":ffgui_fail" in bat
    assert bat.count("if not %errorlevel%==0 goto ffgui_fail") == 1


def test_sh_footer_reads_via_exit_trap():
    text = script_for_job(_job(), "sh", HASH)
    assert "trap 'printf \"Press Enter to close... \"; read -r dummy || true' EXIT" in text
    assert "#!/bin/sh" in text and "set -e" in text


def test_sh_pause_consumes_a_line_on_dash():
    """The EXIT-trap pause must block for input on dash: bare `read -r` errors
    with 'arg count' without consuming input, so the window closes immediately."""
    probe = subprocess.run(["sh", "-c", "read -r </dev/null"],
                           capture_output=True, timeout=30)
    if probe.returncode == 0:
        pytest.skip("system sh accepts bare read (not dash-like)")
    script = script_for_job(_job(), "sh", HASH)
    trap = next(l for l in script.split("\n") if l.startswith("trap "))
    assert trap.startswith("trap '") and trap.endswith("' EXIT")
    body = trap[len("trap '"): -len("' EXIT")]

    out = subprocess.run(
        ["sh", "-c", body + "; wc -c"],
        input="x\n", capture_output=True, text=True, timeout=30)
    assert out.returncode == 0


    assert out.stdout.strip().rsplit(None, 1)[-1] == "0"


def test_concat_sidecar_referenced_from_argv():
    job = build_concat_job(["p1.mp4", "p2.mp4"], "joined.mp4")
    cars = sidecars_for(job)
    assert len(cars) == 1
    argv = build(job)
    assert cars[0].path in argv
    bat = script_for_job(job, "bat", HASH)
    assert "> " in bat and "echo(" in bat


def test_filter_chain_in_argv():
    job = _job(video_codec="libx264")
    job.video_filters.filters.append("scale=640:360")
    argv = build(job)
    assert "-vf" in argv and "scale=640:360" in argv


def test_header_newlines_collapsed_to_single_line_comments():
    """Every header field interpolates into line comments — a newline in any
    of them must collapse, never splice an executable line into the script."""
    from ffgui.export.bat import bat_script
    from ffgui.export.model import ExportBlock, ScriptHeader
    from ffgui.export.sh import sh_script
    header = ScriptHeader("g\ntouch PWNED", "ha\nsh", "t\ntouch PWNED",
                          "a\ntouch PWNED\nb -> o")
    blocks = [ExportBlock(["ffmpeg", "-i", "a"])]
    sh = sh_script(blocks, header)
    assert not [l for l in sh.split("\n") if l.strip().startswith("touch ")]
    assert len([l for l in sh.split("\n") if l.startswith("# ")]) == 3
    bat = bat_script(blocks, header)
    assert not [l for l in bat.split("\r\n") if l.strip().startswith("touch ")]
    assert len([l for l in bat.split("\r\n") if l.startswith("rem ")]) == 3


def test_bat_sidecar_rejects_double_quote_in_content():
    """A `"` in sidecar content would toggle cmd.exe's quote state inside the
    `( echo( … )` block and corrupt it silently — fail loudly like bat_token."""
    from fftui.util.command_builder import Sidecar
    from ffgui.export.bat import bat_script
    from ffgui.export.model import ExportBlock, ScriptHeader
    car = Sidecar("C:\\list.txt", "file 'a\"b.mp4'\n")
    with pytest.raises(ValueError, match="cannot be represented"):
        bat_script([ExportBlock(["ffmpeg", "-i", "a"], [car])],
                   ScriptHeader("g", HASH, "t", "s"))


def test_bat_sidecar_path_with_percent_renders_safely():
    """A % in the scratch dir must double exactly once in the sidecar block."""
    from fftui.util.command_builder import Sidecar
    from ffgui.export.bat import bat_script
    from ffgui.export.model import ExportBlock, ScriptHeader
    car = Sidecar("C:\\scratch 100%\\list.txt", "file 'p1.mp4'\n")
    bat = bat_script([ExportBlock(["ffmpeg", "-i", "a"], [car])],
                     ScriptHeader("g", HASH, "t", "s"))
    assert '> "C:\\scratch 100%%\\list.txt" (' in bat
    assert "100%%%%" not in bat
    assert " echo(file 'p1.mp4'" in bat


def test_workdir_note_newlines_collapsed_to_one_comment():
    from ffgui.export.bat import bat_script
    from ffgui.export.model import ExportBlock, ScriptHeader
    from ffgui.export.sh import sh_script
    header = ScriptHeader("g", HASH, "t", "s")
    blocks = [ExportBlock(["ffmpeg", "-i", "a"], [], "run in X\nMALICIOUS();")]
    sh = sh_script(blocks, header)
    assert "# run in X MALICIOUS();\n" in sh
    assert "\nMALICIOUS" not in sh
    bat = bat_script(blocks, header)
    assert "\r\nrem run in X MALICIOUS^(^);\r\n" in bat
    assert "\r\nMALICIOUS" not in bat


def test_bat_rem_lines_neutralize_metacharacters():
    """cmd parses `&|>`, `<`, and `%`-expansion before `rem` runs: a bare
    `&`/`>` in a provenance comment would execute or redirect, and `%0`
    would expand to the script name. `rem` bodies get the same escaping as
    `echo(` sidecar lines."""
    import re
    from ffgui.export.bat import bat_script
    from ffgui.export.model import ExportBlock, ScriptHeader
    header = ScriptHeader("g", HASH, "t", "my clip & (cut).mp4 -> frame_%03d.png")
    note = "run here & now | fast > out < in ^ up % done"
    bat = bat_script([ExportBlock(["ffmpeg"], [], note)], header)
    rems = [l for l in bat.split("\r\n") if l.startswith("rem ")]
    assert len(rems) == 4
    assert "rem my clip ^& ^(cut^).mp4 -^> frame_%%03d.png" in rems
    assert "rem run here ^& now ^| fast ^> out ^< in ^^ up %% done" in rems
    for line in rems:
        body = line[len("rem "):]
        body = re.sub(r"\^.", "", body).replace("%%", "")
        assert not re.search(r"[&|<>%]", body), line


def test_bat_sidecar_rejects_carriage_return():
    """A raw CR inside sidecar content would split the `echo(` block into
    extra physical lines — unrepresentable like `"` (sh heredocs carry CR
    byte-exact, so only .bat refuses)."""
    from fftui.util.command_builder import Sidecar
    from ffgui.export.bat import bat_script
    from ffgui.export.model import ExportBlock, ScriptHeader
    car = Sidecar("C:\\list.txt", "title=a\rb\n")
    with pytest.raises(ValueError, match="cannot be represented"):
        bat_script([ExportBlock(["ffmpeg", "-i", "a"], [car])],
                   ScriptHeader("g", HASH, "t", "s"))


def test_sh_sidecar_preserves_carriage_return_byte_exact(tmp_path):
    """The .sh heredoc carries CR bytes into the file faithfully (verified by
    executing the sidecar prelude with real sh)."""
    from fftui.util.command_builder import Sidecar
    from ffgui.export.model import ExportBlock, ScriptHeader
    from ffgui.export.sh import sh_script
    target = tmp_path / "c.ffm"
    content = "title=a\rb\nlanguage=eng\n"
    scr = sh_script([ExportBlock(["ffmpeg", "-i", "a"],
                                 [Sidecar(str(target), content)])],
                    ScriptHeader("g", HASH, "t", "s"))
    if sys.platform == "win32":
        # No POSIX-faithful sh here to execute the prelude (CR does not
        # survive the trip); verify at the generator seam instead.
        assert "title=a\rb" in scr
        return
    prelude = "\n".join(scr.split("\n")[:-2]) + "\n"
    subprocess.run(["sh", "-c", prelude], check=True,
                   capture_output=True, timeout=30)
    assert target.read_bytes().decode("utf-8") == content


def test_two_pass_sidecar_emitted_once():
    """Both passes share one content-addressed sidecar: render it once, not
    once per pass block (identical path+content dedupes; differing content
    never collapses)."""
    job = _job("in.mp4", "o.mkv",
               chapters=[Chapter(start="0", title="Intro & titles")])
    job.two_pass = True
    bat = script_for_job(job, "bat", HASH)
    assert bat.count("echo(;FFMETADATA1") == 1
    assert bat.count("-hide_banner") == 2
    sh = script_for_job(job, "sh", HASH)
    assert sh.count(";FFMETADATA1") == 1
    assert sh.count("-hide_banner") == 2


def test_two_pass_sidecar_pruned_upstream(monkeypatch):
    """Pruning lives in the model, not the serializers: blocks_for_job emits
    the second pass with no sidecars, so renderers stay dumb loops."""
    from ffgui.export.orchestrate import blocks_for_job
    _pin_env(monkeypatch)
    job = _job("in.mp4", "o.mkv", chapters=[Chapter(start="0", title="Intro")])
    job.two_pass = True
    pass1, pass2 = blocks_for_job(job)
    assert len(pass1.sidecars) == 1 and pass2.sidecars == []


def test_queue_one_file_embeds_shared_sidecar_once(monkeypatch):
    """Content-identical sidecars across queued jobs share one path: the
    one_file script must write it once, ahead of its first use."""
    _pin_env(monkeypatch)
    jobs = [build_concat_job(["p1.mp4", "p2.mp4"], "j1.mp4"),
            build_concat_job(["p1.mp4", "p2.mp4"], "j2.mp4")]
    bat = script_for_queue(jobs, "bat", HASH, one_file=True,
                           created_utc="2026-09-05 00:00 UTC")
    assert isinstance(bat, str)
    assert bat.count("ffmpeg.EXE") == 2
    assert bat.count(".txt (") == 1
    sh = script_for_queue(jobs, "sh", HASH, one_file=True,
                          created_utc="2026-09-05 00:00 UTC")
    assert sh.count("<<'FFGUI_EOF'") == 1


def test_prune_never_collapses_differing_content():
    """Same path, different bytes must survive pruning: collapsing them
    would serve one command another command's file."""
    from fftui.util.command_builder import Sidecar

    from ffgui.export.model import ExportBlock, prune_repeated_sidecars
    blocks = [ExportBlock(["a"], [Sidecar("x.txt", "one\n")]),
              ExportBlock(["b"], [Sidecar("x.txt", "two\n")])]
    pruned = prune_repeated_sidecars(blocks)
    assert [c.content for b in pruned for c in b.sidecars] == ["one\n", "two\n"]


def test_rendering_never_mutates_jobs(monkeypatch):
    """Preview/export share the live Job: if a build injected pass flags or
    prune dropped sidecars in place, the queue would corrupt itself by
    displaying. Render single, concat-sidecar, and two-pass shapes in both
    targets and diff the job dict after every render."""
    from fftui.util.command_builder import build_concat_job

    _pin_env(monkeypatch)
    two = _job(out="o.mp4", video_codec="libx264")
    two.two_pass = True
    jobs = [two, build_concat_job(["p1.mp4", "p2.mp4"], "j.mp4")]
    for job in jobs:
        before = job.to_dict()
        first = script_for_job(job, "sh", HASH,
                               created_utc="2026-09-05 00:00 UTC")
        assert job.to_dict() == before
        bat = script_for_job(job, "bat", HASH,
                             created_utc="2026-09-05 00:00 UTC")
        assert job.to_dict() == before
        assert bat
        assert script_for_job(job, "sh", HASH,
                              created_utc="2026-09-05 00:00 UTC") == first


def test_concat_summary_names_inputs_not_scratch(monkeypatch):
    """The header summary is provenance: it must name the joined inputs, never
    the content-addressed temp list (which may not exist where it runs, and
    leaks the builder account name). `->` is cmd-escaped at render."""
    _pin_env(monkeypatch)
    job = build_concat_job(["p1.mp4", "p2 & more.mp4"], "joined.mp4")
    bat = script_for_job(job, "bat", HASH, created_utc="2026-09-05 00:00 UTC")
    assert "rem p1.mp4 ^(+1 more^) -^> joined.mp4" in bat
    assert "fftui-concat-" not in next(
        line for line in bat.split("\r\n") if line.startswith("rem "))
    sh = script_for_job(job, "sh", HASH, created_utc="2026-09-05 00:00 UTC")
    assert "# p1.mp4 (+1 more) -> joined.mp4" in sh


def test_export_empty_io_raises_loudly():
    """A job with no inputs or no outputs cannot produce a command — the
    domain error must surface, never an empty executable line."""
    from fftui.errors import FFtuiError
    with pytest.raises(FFtuiError):
        script_for_job(Job(inputs=[], outputs=[Output(path="o.mp4")]), "sh", HASH)
    with pytest.raises(FFtuiError):
        script_for_job(Job(inputs=[Input(path="i.mp4")], outputs=[]), "sh", HASH)


ffmpeg = shutil.which("ffmpeg")
ffprobe = shutil.which("ffprobe")
_needs_ffmpeg = pytest.mark.skipif(ffmpeg is None, reason="ffmpeg not on PATH")
# Script execution needs the target interpreter, independent of the OS under
# test: a .bat test without cmd.exe is untestable there, not broken — the
# rendering itself is covered everywhere by the golden tests.
_needs_cmd = pytest.mark.skipif(shutil.which("cmd") is None, reason="no cmd.exe")
_needs_sh = pytest.mark.skipif(shutil.which("sh") is None, reason="no POSIX sh")


def _run_script(script_path: Path) -> None:
    if script_path.suffix == ".bat":
        subprocess.run(["cmd", "/c", str(script_path)], check=True,
                       capture_output=True, timeout=120, stdin=subprocess.DEVNULL)
    else:
        subprocess.run(["sh", str(script_path)], check=True,
                       capture_output=True, timeout=120, stdin=subprocess.DEVNULL)


def _probe_duration(path: Path) -> float:
    out = subprocess.run(
        [ffprobe, "-v", "error", "-show_entries", "format=duration", "-of",
         "csv=p=0", str(path)], check=True, capture_output=True, text=True)
    return float(out.stdout.strip())


def _make_clip(path: Path, seconds: float = 0.5) -> None:
    subprocess.run(
        [ffmpeg, "-hide_banner", "-y",
         "-f", "lavfi", "-i", f"testsrc=duration={seconds}:size=128x96:rate=10",
         "-f", "lavfi", "-i", f"sine=duration={seconds}:frequency=440",
         "-c:v", "libx264", "-c:a", "aac", "-shortest", str(path)],
        check=True, capture_output=True, timeout=60)


@pytest.fixture(scope="module")
def clip_source(tmp_path_factory) -> Path:
    src = tmp_path_factory.mktemp("media") / "tiny.mp4"
    _make_clip(src)
    return src


@_needs_ffmpeg
@pytest.mark.parametrize("target", [pytest.param("bat", marks=_needs_cmd),
                                    pytest.param("sh", marks=_needs_sh)])
def test_exec_hostile_name(tmp_path, clip_source, target):
    hostile = tmp_path / "a clip & 100% (x)! it's.mp4"
    shutil.copy(clip_source, hostile)
    out = tmp_path / "out put & 50% (ok).mp4"
    job = Job(inputs=[Input(path=str(hostile))],
              outputs=[Output(path=str(out), video_codec="libx264")], noconfirm=True)
    ext = ".bat" if target == "bat" else ".sh"
    script = tmp_path / f"run{ext}"
    script.write_text(script_for_job(job, target, HASH), encoding="utf-8", newline="")
    _run_script(script)
    assert out.is_file() and out.stat().st_size > 0
    assert _probe_duration(out) == pytest.approx(0.5, abs=0.2)


@_needs_ffmpeg
@_needs_cmd
def test_exec_sequence(tmp_path, clip_source):
    job = Job(inputs=[Input(path=str(clip_source))],
              outputs=[Output(path=str(tmp_path / "frame_%03d.png"))], noconfirm=True)
    script = tmp_path / "seq.bat"
    script.write_text(script_for_job(job, "bat", HASH), encoding="utf-8", newline="")
    _run_script(script)
    frames = sorted(tmp_path.glob("frame_*.png"))
    assert frames and frames[0].name == "frame_001.png"


@_needs_ffmpeg
@pytest.mark.parametrize("target", [pytest.param("bat", marks=_needs_cmd),
                                    pytest.param("sh", marks=_needs_sh)])
def test_exec_concat_with_embedded_sidecar(tmp_path, clip_source, target):
    p1 = tmp_path / "p1.mp4"
    p2 = tmp_path / "p2 & more.mp4"
    shutil.copy(clip_source, p1)
    shutil.copy(clip_source, p2)
    job = build_concat_job([str(p1), str(p2)], str(tmp_path / "joined.mp4"))
    job.noconfirm = True
    ext = ".bat" if target == "bat" else ".sh"
    script = tmp_path / f"concat{ext}"
    script.write_text(script_for_job(job, target, HASH), encoding="utf-8", newline="")
    _run_script(script)
    out = tmp_path / "joined.mp4"
    assert out.is_file()
    assert _probe_duration(out) == pytest.approx(1.0, abs=0.3)


@_needs_ffmpeg
@_needs_cmd
def test_exec_bat_fail_reaches_pause_footer(tmp_path):
    job = Job(inputs=[Input(path=str(tmp_path / "missing.mp4"))],
              outputs=[Output(path=str(tmp_path / "out.mp4"))], noconfirm=True)
    script = tmp_path / "fail.bat"
    script.write_text(script_for_job(job, "bat", HASH), encoding="utf-8", newline="")
    out = subprocess.run(["cmd", "/c", str(script)], capture_output=True,
                         timeout=60, stdin=subprocess.DEVNULL)
    assert out.returncode == 1
    assert "One of the commands failed." in out.stdout.decode("utf-8", "replace")


@_needs_ffmpeg
@_needs_cmd
def test_exec_chapters_sidecar(tmp_path, clip_source):
    job = Job(inputs=[Input(path=str(clip_source))],
              outputs=[Output(path=str(tmp_path / "ch.mkv"),
                              chapters=[Chapter(start="0", title="Intro & 开场")])],
              noconfirm=True)
    script = tmp_path / "ch.bat"
    script.write_text(script_for_job(job, "bat", HASH), encoding="utf-8", newline="")
    _run_script(script)
    out = subprocess.run(
        [ffprobe, "-v", "error", "-show_chapters", "-of", "json", str(tmp_path / "ch.mkv")],
        check=True, capture_output=True, encoding="utf-8")
    assert "Intro & 开场" in out.stdout
