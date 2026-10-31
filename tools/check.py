#!/usr/bin/env python3
"""Lightweight static check for the backend.

Compiles every .py file under app/ and tests/ with py_compile. This catches syntax
errors and is deliberately dependency-free, so it works even before `pip install -r
requirements.txt` (handy for a quick sanity check in CI or a sandbox with no network).
It does NOT catch import errors, type errors, or logic bugs — run `pytest` and start
the server for that.

Usage:  python tools/check.py
"""
import py_compile
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def main() -> int:
    targets = sorted((ROOT / "app").rglob("*.py")) + sorted((ROOT / "tests").rglob("*.py"))
    failures = []
    for path in targets:
        try:
            py_compile.compile(str(path), doraise=True)
        except py_compile.PyCompileError as err:
            failures.append((path, str(err)))

    for path, message in failures:
        print(f"FAIL  {path.relative_to(ROOT)}\n{message}\n")

    print(f"Checked {len(targets)} file(s), {len(failures)} failure(s).")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
