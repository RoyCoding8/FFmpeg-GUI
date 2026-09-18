from __future__ import annotations

import re
import sys
from pathlib import Path
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parents[1]
README = ROOT / "README.md"


def local_targets(text: str) -> set[str]:
    targets = set(re.findall(r"!?(?:\[[^]]*\])\(([^)]+)\)", text))
    targets.update(re.findall(r"(?:src|href)=\"([^\"]+)\"", text))
    return {
        target.split("#", 1)[0].split("?", 1)[0]
        for target in targets
        if target and not target.startswith("#")
        and urlsplit(target).scheme == ""
        and not target.startswith("//")
    }


def main() -> int:
    targets = local_targets(README.read_text(encoding="utf-8"))
    missing = sorted(
        target for target in targets if not (ROOT / Path(*target.split("/"))).exists()
    )
    if missing:
        print("Missing local README targets:")
        print("\n".join(f"- {target}" for target in missing))
        return 1
    print(f"README asset check passed for {len(targets)} local targets.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
