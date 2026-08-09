from __future__ import annotations

import compileall
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def main() -> int:
    sources = [
        ROOT / "packages" / "battle-engine" / "src",
        ROOT / "services" / "api" / "src",
        ROOT / "scripts",
        ROOT / "tests",
    ]
    if not all(compileall.compile_dir(path, quiet=1) for path in sources):
        return 1
    for command in (
        ["node", "--check", "apps/web/app.js"],
        ["node", "--check", "services/showdown-calc/worker.mjs"],
        ["node", "services/showdown-calc/smoke-test.mjs"],
    ):
        result = subprocess.run(command, cwd=ROOT, check=False)
        if result.returncode:
            return result.returncode
    result = subprocess.run(
        [sys.executable, "-m", "unittest", "discover", "-s", "tests", "-v"],
        cwd=ROOT,
        check=False,
    )
    return result.returncode


if __name__ == "__main__":
    raise SystemExit(main())
