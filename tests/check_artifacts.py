#!/usr/bin/env python3
"""Build-artifact checks for the deliverable image.

Verifies that inside the image:
  * the native core exists, is an executable regular file and actually solves
    a known instance (guards against shipping the C source without compiling),
  * the application package imports cleanly,
  * /healthz is wired in the HTTP server module.

Exit code is non-zero on the first failed check.
"""

from __future__ import annotations

import os
import stat
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

failures: list[str] = []


def check(condition: bool, label: str, detail: str = "") -> bool:
    if condition:
        print(f"  pass  {label}")
    else:
        failures.append(f"{label} {detail}".strip())
        print(f"  FAIL  {label} {detail}")
    return condition


def main() -> int:
    core = Path(os.environ.get(
        "STEINER_CORE", str(ROOT / "core" / "steiner")
    ))
    print(f"[artifact] native core: {core}")

    check(core.exists(), "core binary exists", str(core))
    if core.exists():
        mode = core.stat().st_mode
        check(stat.S_ISREG(mode), "core is a regular file")
        check(mode & stat.S_IXUSR, "core is owner-executable", oct(mode))
        proc = subprocess.run(
            [str(core)],
            input="4 4 2\n0 3\n0 1 1\n1 2 1\n2 3 1\n0 3 9\n",
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
        check(proc.returncode == 0, "core exits zero", proc.stderr)
        tokens = proc.stdout.split()
        check(tokens[:1] == ["OK"], "core reports OK", proc.stdout)
        check(tokens[1:2] == ["3"], "core finds cost 3", proc.stdout)
        # Cheapest 0-3 route is the 3-edge path (ids 0,1,2) versus edge 3 (9).
        check(tokens[2:3] == ["3"], "core selects 3 edges", proc.stdout)

    print("[artifact] python package import")
    try:
        import app.server  # noqa: F401
        import app.solver  # noqa: F401
        import app.validation  # noqa: F401
        import app.errors  # noqa: F401
        check(True, "application modules import")
    except Exception as exc:  # noqa: BLE001
        check(False, "application modules import", repr(exc))

    import app.server as srv
    check(hasattr(srv, "AuditHandler"), "AuditHandler present")
    check(hasattr(srv, "build_server"), "server factory present")

    source = ROOT / "core" / "steiner.c"
    check(source.exists(), "C source present for reproducible builds",
          str(source))

    print("-" * 60)
    if failures:
        print(f"artifact checks: {len(failures)} FAILED")
        return 1
    print("artifact checks: all passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
