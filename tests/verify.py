#!/usr/bin/env python3
"""One-shot verification entrypoint for the `verify` compose service.

Runs, in order:
  1. solver/validation unit tests (tests/test_solver.py),
  2. build-artifact checks (tests/check_artifacts.py),
  3. HTTP smoke tests against the running API (tests/smoke_http.py),
     including canonical tie arbitration and infeasibility boundaries.

The process prints one summary block and exits non-zero if any stage fails,
so Compose marks the run by its exit code.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TESTS = ROOT / "tests"
PYTHON = sys.executable

stages: list[tuple[str, list[str], dict[str, str]]] = []

stages.append((
    "solver unit tests",
    [PYTHON, "-u", str(TESTS / "harness.py"), "test_solver"],
    {"PYTHONPATH": str(ROOT)},
))
stages.append((
    "build artifact checks",
    [PYTHON, "-u", str(TESTS / "check_artifacts.py")],
    {"PYTHONPATH": str(ROOT)},
))

env = dict(os.environ)
env.setdefault("AUDIT_BASE_URL", "http://api:8080")
stages.append((
    "HTTP smoke tests",
    [PYTHON, "-u", str(TESTS / "smoke_http.py")],
    env,
))


def main() -> int:
    results: list[tuple[str, int]] = []
    for name, cmd, stage_env in stages:
        print("=" * 68)
        print(f"STAGE: {name}")
        print("=" * 68)
        merged = dict(os.environ)
        merged.update(stage_env)
        completed = subprocess.run(cmd, cwd=ROOT, env=merged, check=False)
        results.append((name, completed.returncode))

    print("=" * 68)
    print("VERIFICATION SUMMARY")
    print("=" * 68)
    failed = False
    for name, code in results:
        status = "PASS" if code == 0 else f"FAIL(exit={code})"
        if code != 0:
            failed = True
        print(f"  {status:14s} {name}")
    print("-" * 68)
    if failed:
        print("RESULT: FAILED")
        return 1
    print("RESULT: ALL CHECKS PASSED")
    return 0


if __name__ == "__main__":
    sys.exit(main())
