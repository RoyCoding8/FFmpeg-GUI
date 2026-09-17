import shutil
import subprocess
import wave

import pytest
from fftui.util.command_builder import build_concat_job

from ffgui.export.orchestrate import script_for_job


@pytest.mark.parametrize("target", ["bat", "sh"])
def test_export_recreates_removed_sidecar_directory(tmp_path, monkeypatch, target):
    interpreter = shutil.which("cmd" if target == "bat" else "sh")
    if interpreter is None or shutil.which("ffmpeg") is None:
        pytest.skip("script interpreter or ffmpeg unavailable")
    import fftui.util.command_builder as builder

    scratch = tmp_path / "temporary files"
    scratch.mkdir()
    monkeypatch.setattr(builder, "_scratch_dir", lambda: str(scratch))
    monkeypatch.setattr("fftui.ffmpeg.runtime.ffmpeg_exe", lambda: "ffmpeg")
    first = tmp_path / "part one.wav"
    second = tmp_path / "part two.wav"

    for path in (first, second):
        with wave.open(str(path), "wb") as audio:
            audio.setparams((1, 2, 8000, 0, "NONE", "not compressed"))
            audio.writeframes(b"\0\0" * 800)
    out = tmp_path / "joined.wav"
    job = build_concat_job([str(first), str(second)], str(out))
    job.noconfirm = True
    script = tmp_path / f"join.{target}"
    script.write_bytes(script_for_job(job, target, "test").encode("utf-8"))
    scratch.rmdir()

    args = [interpreter, "/c", str(script)] if target == "bat" else [interpreter, str(script)]
    for _ in range(2):
        run = subprocess.run(args, stdin=subprocess.DEVNULL, capture_output=True, timeout=30)
        assert run.returncode == 0, (run.stdout, run.stderr)
        assert scratch.is_dir()
        assert len(list(scratch.glob("fftui-concat-*.txt"))) == 1
        with wave.open(str(out), "rb") as audio:
            assert audio.getnframes() == 1600
