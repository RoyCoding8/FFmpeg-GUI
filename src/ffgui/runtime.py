from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

MEDIA_SUFFIXES = {".mp4", ".mkv", ".webm", ".mov", ".avi", ".ts", ".m4v", ".mpg",
                  ".mp3", ".m4a", ".wav", ".flac", ".ogg", ".opus", ".aac", ".wma",
                  ".srt", ".ass", ".vtt", ".jpg", ".png"}
TARGET_EXT = {"bat": ".bat", "sh": ".sh"}
_LINUX_TERMINALS = (("x-terminal-emulator", "-e"), ("gnome-terminal", "--"),
                    ("konsole", "-e"), ("xfce4-terminal", "-x"),
                    ("mate-terminal", "--"), ("xterm", "-e"))


def terminal_argv(path: str, *, platform: str | None = None) -> list[str] | None:
    plat = sys.platform if platform is None else platform
    if plat == "darwin":
        return ["osascript", "-e",
                'on run argv\ntell app "Terminal" to do script '
                '"sh -- " & quoted form of (item 1 of argv)\nend run',
                path]
    for exe, flag in _LINUX_TERMINALS:
        if shutil.which(exe):
            return [exe, flag, "sh", "--", path]
    return None


def launch_script(path: str, *, platform: str | None = None, resolver=None) -> None:
    plat = sys.platform if platform is None else platform
    if Path(path).suffix.lower() == ".bat" and plat != "win32":
        raise RuntimeError(
            f"cannot launch {path} here — .bat scripts need Windows "
            "(export a .sh script to run on this machine)")
    if plat == "win32":
        os.startfile(path)
        return
    argv = terminal_argv(path, platform=plat) if resolver is None else resolver(path)
    if argv is None:
        raise RuntimeError("no terminal emulator found — run the saved script manually")
    subprocess.Popen(argv, start_new_session=True, stdin=subprocess.DEVNULL,
                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def locate_ffmpeg(settings, *, executable: str | None = None) -> tuple[str | None, bool]:
    for candidate in (os.environ.get("FFMPEG_PATH", "").strip(),
                      str(settings.value("ffmpeg_path", "") or "").strip()):
        if candidate:
            resolved = Path(os.path.expanduser(os.path.expandvars(candidate)))
            if resolved.is_file():
                return str(resolved), True
    if shutil.which("ffmpeg"):
        return None, True
    sibling_dir = Path(sys.executable if executable is None else executable).parent
    for name in ("ffmpeg.exe", "ffmpeg"):
        sibling = sibling_dir / name
        if sibling.is_file():
            return str(sibling), True
    return None, False


def aim_ffmpeg(path: str | None) -> None:
    if path:
        os.environ["FFTUI_FFMPEG_DIR"] = str(Path(path).parent)
