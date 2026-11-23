#!/usr/bin/env python3
"""Run every chapter's test suite from the repository root.

Usage:
    python scripts/run_checks.py               # all chapters
    python scripts/run_checks.py --chapter 07  # one chapter
    python scripts/run_checks.py --list         # list chapters and their tests

Each chapter's tests run in that chapter's own ``code/`` directory,
so relative imports and fixture files resolve the same way they do
when you run the tests by hand. A chapter with no test files is
reported as "no tests", not as a failure. A chapter whose tests
cannot even be imported — usually a missing third-party package —
is reported as "blocked", with the missing module named, instead
of being counted as a test failure: install that chapter's
``code/requirements.txt`` and re-run.

Live tests that need a model API key or a service (Chapter 7's
judge and agent tests, Chapter 6's Docker path) skip themselves
when the backend is not configured, so a plain run needs no keys
and no network. To run the live tests as well, configure the
backend first — see Chapter 7's ``code/README.md`` — and re-run.

Exit code 0 means every chapter with tests passed. Anything else
means at least one chapter failed or is blocked.
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_TIMEOUT = 300

_RAN_RE = re.compile(r"^Ran (\d+) tests? in ", re.MULTILINE)
_OK_RE = re.compile(r"^OK(?: \(skipped=(\d+)\))?$", re.MULTILINE)
_FAILED_RE = re.compile(
    r"^FAILED \((?:failures=(\d+))?(?:, )?(?:errors=(\d+))?(?:, )?(?:skipped=(\d+))?\)$",
    re.MULTILINE,
)
_MISSING_RE = re.compile(r"ModuleNotFoundError: No module named '([^']+)'")


def find_chapters() -> list[tuple[str, Path, list[str]]]:
    """Return (chapter number, code dir, test module names) for each chapter."""
    chapters = []
    for chapter_dir in sorted(ROOT.glob("chapter-*")):
        code_dir = chapter_dir / "code"
        if not code_dir.is_dir():
            continue
        number = chapter_dir.name.split("-")[1]
        modules = sorted(p.stem for p in code_dir.glob("test_*.py"))
        chapters.append((number, code_dir, modules))
    return chapters


_CHECKS_RE = re.compile(r"^All (\d+) checks passed\.", re.MULTILINE)


def _parse_unittest(output: str, returncode: int) -> dict | None:
    """Parse unittest output, or return None when it ran no tests."""
    ran = _RAN_RE.search(output)
    ran_count = int(ran.group(1)) if ran else 0
    if "NO TESTS RAN" in output or ran_count == 0 and returncode == 5:
        return None
    if returncode == 0:
        skipped = _OK_RE.search(output)
        return {
            "status": "passed",
            "ran": ran_count,
            "detail": f"skipped={skipped.group(1)}" if skipped and skipped.group(1) else "",
        }
    missing = _MISSING_RE.search(output)
    if missing and ran_count == 0:
        return {"status": "blocked", "ran": 0, "detail": f"missing module '{missing.group(1)}'"}
    failed = _FAILED_RE.search(output)
    detail = ""
    if failed:
        failures = int(failed.group(1) or 0)
        errors = int(failed.group(2) or 0)
        skipped = int(failed.group(3) or 0)
        detail = f"failures={failures} errors={errors} skipped={skipped}"
    return {"status": "failed", "ran": ran_count, "detail": detail}


def _run_script(code_dir: Path, module: str, timeout: int) -> dict:
    """Run a script-style suite (plain functions, run as ``python test_x.py``)."""
    try:
        proc = subprocess.run(
            [sys.executable, f"{module}.py"],
            cwd=code_dir,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        return {"status": "failed", "ran": 0, "detail": f"timed out after {timeout}s"}
    output = proc.stdout + proc.stderr
    checks = _CHECKS_RE.search(output)
    if proc.returncode == 0 and checks:
        return {"status": "passed", "ran": int(checks.group(1)), "detail": "script suite"}
    missing = _MISSING_RE.search(output)
    if missing:
        return {"status": "blocked", "ran": 0, "detail": f"missing module '{missing.group(1)}'"}
    last_error = ""
    for line in reversed(output.strip().splitlines()):
        if line.strip():
            last_error = line.strip()[:160]
            break
    return {"status": "failed", "ran": 0, "detail": last_error}


def _required_packages(code_dir: Path) -> set[str]:
    """Package names from a chapter's requirements.txt, normalised to import names."""
    aliases = {"pillow": "PIL", "opencv-python": "cv2", "pyyaml": "yaml"}
    packages: set[str] = set()
    req_file = code_dir / "requirements.txt"
    if not req_file.is_file():
        return packages
    for line in req_file.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        name = re.split(r"[<>=!~\s\[]", line, maxsplit=1)[0].strip().lower()
        if name:
            packages.add(aliases.get(name, name.replace("-", "_")))
    return packages


def _find_missing_module(code_dir: Path) -> str | None:
    """Find a missing third-party import among a chapter's modules.

    Some failures hide the real cause: Chapter 2's scoped server
    dies on a missing import in a subprocess whose stderr is
    piped away, so the test output only shows the client giving
    up. Importing each non-test module in turn surfaces the
    missing module by name. Only modules the chapter actually
    requires (its requirements.txt) are reported, so a standalone
    demo file with its own optional import does not mislead.
    Only used as a diagnostic after a chapter has already failed.
    """
    required = _required_packages(code_dir)
    found: list[str] = []
    for path in sorted(code_dir.glob("*.py")):
        if path.stem.startswith("test_"):
            continue
        try:
            proc = subprocess.run(
                [sys.executable, "-c", f"import {path.stem}"],
                cwd=code_dir,
                capture_output=True,
                text=True,
                timeout=60,
            )
        except subprocess.TimeoutExpired:
            continue
        match = _MISSING_RE.search(proc.stdout + proc.stderr)
        if match and match.group(1) not in found:
            found.append(match.group(1))
    for name in found:
        if name in required:
            return name
    return found[0] if found else None


def run_chapter(number: str, code_dir: Path, modules: list[str], timeout: int) -> dict:
    """Run one chapter's tests and summarise the outcome."""
    if not modules:
        return {"number": number, "status": "no tests", "ran": 0, "detail": ""}
    command = [sys.executable, "-m", "unittest", *modules, "-v"]
    try:
        proc = subprocess.run(
            command,
            cwd=code_dir,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        return {
            "number": number,
            "status": "failed",
            "ran": 0,
            "detail": f"timed out after {timeout}s",
        }
    parsed = _parse_unittest(proc.stdout + proc.stderr, proc.returncode)
    if parsed is not None:
        if parsed["status"] == "blocked":
            parsed["detail"] += f" — install {code_dir.relative_to(ROOT)}/requirements.txt"
        if parsed["status"] == "failed" and parsed["ran"] == 0:
            missing = _find_missing_module(code_dir)
            if missing:
                return {
                    "number": number,
                    "status": "blocked",
                    "ran": 0,
                    "detail": (
                        f"missing module '{missing}' — "
                        f"install {code_dir.relative_to(ROOT)}/requirements.txt"
                    ),
                }
        return {"number": number, **parsed}
    # No unittest cases: the suite is a script (Chapter 2 runs as
    # ``python test_server.py``). Run each test file directly.
    results = [_run_script(code_dir, module, timeout) for module in modules]
    total = sum(r["ran"] for r in results)
    blocked = [r for r in results if r["status"] == "blocked"]
    failed = [r for r in results if r["status"] == "failed"]
    if blocked:
        detail = "; ".join(
            f"{r['detail']} — install {code_dir.relative_to(ROOT)}/requirements.txt"
            for r in blocked
        )
        return {"number": number, "status": "blocked", "ran": total, "detail": detail}
    if failed:
        missing = _find_missing_module(code_dir)
        if missing:
            return {
                "number": number,
                "status": "blocked",
                "ran": total,
                "detail": (
                    f"missing module '{missing}' — "
                    f"install {code_dir.relative_to(ROOT)}/requirements.txt"
                ),
            }
        return {"number": number, "status": "failed", "ran": total, "detail": failed[0]["detail"]}
    return {"number": number, "status": "passed", "ran": total, "detail": "script suite"}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--chapter", default=None, help="run one chapter, e.g. 07")
    parser.add_argument("--timeout", type=int, default=DEFAULT_TIMEOUT,
                        help="seconds per chapter (default: 300)")
    parser.add_argument("--list", action="store_true", help="list chapters and exit")
    args = parser.parse_args(argv)

    chapters = find_chapters()
    if args.list:
        for number, code_dir, modules in chapters:
            tests = ", ".join(modules) if modules else "no tests"
            print(f"chapter {number}: {tests}")
        return 0
    if args.chapter:
        chapters = [c for c in chapters if c[0] == args.chapter.lstrip("0") or c[0] == args.chapter]
        if not chapters:
            print(f"No chapter {args.chapter}.", file=sys.stderr)
            return 2

    print(f"Running checks in {ROOT} (Python {sys.version.split()[0]})")
    results = [run_chapter(n, d, m, args.timeout) for n, d, m in chapters]
    print()
    print(f"{'chapter':<10}{'status':<10}{'tests':<8}detail")
    for result in results:
        print(
            f"{result['number']:<10}{result['status']:<10}"
            f"{result['ran']:<8}{result['detail']}"
        )
    failed = [r for r in results if r["status"] in ("failed", "blocked")]
    print()
    if failed:
        print(f"{len(failed)} chapter(s) need attention.")
        return 1
    print("All chapters with tests passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
