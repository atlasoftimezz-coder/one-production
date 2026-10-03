"""Automatic gate check for a phase.

    check.py P3            runs tests\\p0 .. tests\\p3 (every earlier phase re-runs, so a
                           fix can never silently break something already approved)

Writes docs\\gates\\P<n>-auto.md and exits 0 only if every test passed.
"""
from __future__ import annotations

import argparse
import datetime as dt
import os
import platform
import re
import subprocess
import sys
import tempfile
import xml.etree.ElementTree as ET
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def parse_phase(text: str) -> int:
    m = re.fullmatch(r"[Pp]?(\d{1,2})", text.strip())
    if not m:
        raise SystemExit(f"Phase must look like P0, P3, 11 - got {text!r}")
    return int(m.group(1))


def git_commit() -> str:
    try:
        out = subprocess.run(["git", "rev-parse", "--short", "HEAD"], cwd=ROOT,
                             capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=10)
        dirty = subprocess.run(["git", "status", "--porcelain"], cwd=ROOT,
                               capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=10).stdout.strip()
        sha = out.stdout.strip() or "no commits yet"
        return sha + (" (uncommitted changes)" if dirty else "")
    except OSError:
        return "git not available"


def run_pytest(dirs: list[Path], rootdir: Path) -> tuple[list[dict], str]:
    # Every nested Python process (pytest, the app, the browser driver) writes UTF-8, so
    # output containing characters like '·' can't crash a Windows code-page decoder.
    os.environ.setdefault("PYTHONUTF8", "1")
    os.environ.setdefault("PYTHONIOENCODING", "utf-8")
    with tempfile.TemporaryDirectory() as tmp:
        xml_path = Path(tmp) / "results.xml"
        cmd = [sys.executable, "-m", "pytest", *map(str, dirs), "-q", "-p", "no:cacheprovider",
               f"--rootdir={rootdir}", f"--junitxml={xml_path}"]
        proc = subprocess.run(cmd, cwd=rootdir, capture_output=True, text=True, encoding="utf-8", errors="replace")
        if not xml_path.exists():
            return [], proc.stdout + proc.stderr
        tree = ET.parse(xml_path)
    rows = []
    for case in tree.iter("testcase"):
        failure = case.find("failure")
        if failure is None:
            failure = case.find("error")
        skipped = case.find("skipped")
        status = "FAIL" if failure is not None else ("SKIP" if skipped is not None else "PASS")
        detail = ""
        if failure is not None:
            detail = (failure.get("message") or "") + "\n" + (failure.text or "")
        rows.append({
            "file": (case.get("classname") or "").replace(".", "/"),
            "name": case.get("name"),
            "time": float(case.get("time") or 0),
            "status": status,
            "detail": detail.strip(),
        })
    return rows, proc.stdout + proc.stderr


def write_report(path: Path, phase: int, dirs: list[Path], rows: list[dict], raw: str) -> bool:
    passed = sum(r["status"] == "PASS" for r in rows)
    failed = sum(r["status"] == "FAIL" for r in rows)
    skipped = sum(r["status"] == "SKIP" for r in rows)
    ok = bool(rows) and failed == 0
    lines = [
        f"# P{phase} automatic gate check",
        "",
        f"- Date: {dt.datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
        f"- Commit: {git_commit()}",
        f"- Python: {platform.python_version()} on {platform.platform()}",
        f"- Suites: {', '.join(d.name for d in dirs) or 'none found'}",
        "",
        "| Result | Test | Seconds |",
        "|---|---|---|",
    ]
    for r in rows:
        lines.append(f"| {r['status']} | `{r['file']}::{r['name']}` | {r['time']:.2f} |")
    lines += ["", "## Summary", "",
              f"RESULT: {'PASS' if ok else 'FAIL'}  -  {passed} passed, {failed} failed, {skipped} skipped"]
    if not rows:
        lines += ["", "No test results were produced. Raw output:", "", "```", raw[-4000:], "```"]
    fails = [r for r in rows if r["status"] == "FAIL"]
    if fails:
        lines += ["", "## Failures", ""]
        for r in fails:
            lines += [f"### {r['file']}::{r['name']}", "", "```", r["detail"][-3000:], "```", ""]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return ok


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("phase", help="P0 .. P11")
    ap.add_argument("--tests-root", type=Path, default=ROOT / "tests")
    ap.add_argument("--report-dir", type=Path, default=ROOT / "docs" / "gates")
    a = ap.parse_args()

    phase = parse_phase(a.phase)
    dirs = [a.tests_root / f"p{i}" for i in range(phase + 1) if (a.tests_root / f"p{i}").is_dir()]
    if not dirs:
        print(f"No test folders found under {a.tests_root} for P0..P{phase}.")
        return 1
    rows, raw = run_pytest(dirs, a.tests_root.parent)
    report = a.report_dir / f"P{phase}-auto.md"
    ok = write_report(report, phase, dirs, rows, raw)

    for r in rows:
        print(f"  {r['status']:<4}  {r['file']}::{r['name']}")
    print(f"\nP{phase}: {'PASS' if ok else 'FAIL'}  ({sum(r['status'] == 'PASS' for r in rows)}/{len(rows)} passed)")
    print(f"Report: {report}")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
