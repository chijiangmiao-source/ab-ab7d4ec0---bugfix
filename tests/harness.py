"""Minimal zero-dependency test harness (no third-party pytest needed).

Provides just enough for the solver unit tests: an ``assert_raises`` context
manager and a ``main()`` that discovers ``test_*`` functions on ``Test*``
classes in the modules passed on the command line (defaults to test_solver).
"""

from __future__ import annotations

import contextlib
import importlib
import sys
import traceback
from types import ModuleType
from typing import Type


class assert_raises(contextlib.AbstractContextManager):
    def __init__(self, expected: Type[BaseException]) -> None:
        self.expected = expected
        self.value: BaseException | None = None

    def __exit__(self, exc_type, exc, tb) -> bool:
        if exc is None:
            raise AssertionError(
                f"expected {self.expected.__name__} to be raised"
            )
        if not isinstance(exc, self.expected):
            return False
        self.value = exc
        return True


def _run_class(cls: type) -> tuple[int, int]:
    case = cls()
    methods = [
        (name, getattr(case, name))
        for name in dir(cls)
        if name.startswith("test_")
    ]
    methods.sort(key=lambda item: item[0])
    passed = failed = 0
    for name, fn in methods:
        setup = getattr(case, "setup_method", None)
        try:
            if setup is not None:
                setup(name)
            fn()
        except Exception:  # noqa: BLE001 - report every failure
            failed += 1
            print(f"FAIL  {cls.__name__}.{name}")
            traceback.print_exc(file=sys.stdout)
        else:
            passed += 1
            print(f"pass  {cls.__name__}.{name}")
    return passed, failed


def run_module(module: ModuleType) -> tuple[int, int]:
    total_passed = total_failed = 0
    classes = [
        getattr(module, name)
        for name in dir(module)
        if name.startswith("Test")
    ]
    for cls in sorted(classes, key=lambda c: c.__name__):
        p, f = _run_class(cls)
        total_passed += p
        total_failed += f

    functions = [
        (name, getattr(module, name))
        for name in dir(module)
        if name.startswith("test_") and callable(getattr(module, name))
    ]
    for name, fn in sorted(functions):
        try:
            fn()
        except Exception:  # noqa: BLE001
            total_failed += 1
            print(f"FAIL  {name}")
            traceback.print_exc(file=sys.stdout)
        else:
            total_passed += 1
            print(f"pass  {name}")
    return total_passed, total_failed


def main(argv: list[str] | None = None) -> int:
    names = argv if argv else ["test_solver"]
    passed = failed = 0
    for name in names:
        module = importlib.import_module(f"{name}")
        p, f = run_module(module)
        passed += p
        failed += f
    print("-" * 60)
    print(f"unit tests: {passed} passed, {failed} failed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
