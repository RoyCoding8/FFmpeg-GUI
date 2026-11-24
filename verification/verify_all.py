"""Unified runner for the full FFMpeg-GUI formal verification suite."""

from __future__ import annotations

import subprocess
import sys
import time
from pathlib import Path

MODULES = (
    "verify_formal.py",
    "verify_export_sidecars.py",
    "verify_store_crash.py",
    "verify_probe_ui.py",
)


def main(argv: list[str] | None = None) -> int:
    flags = argv if argv is not None else sys.argv[1:]
    root = Path(__file__).resolve().parent
    t0 = time.perf_counter()
    worst = 0
    for name in MODULES:
        proc = subprocess.run([sys.executable, str(root / name), *flags])
        if proc.returncode != 0:
            worst = proc.returncode
    elapsed = time.perf_counter() - t0
    print(f"All {len(MODULES)} formal verification modules finished in {elapsed:.3f}s (exit={worst})")
    return worst


if __name__ == "__main__":
    raise SystemExit(main())
