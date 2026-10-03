import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
CHECK = ROOT / "tools" / "check.py"


def _run(tmp_path, body):
    suite = tmp_path / "tests" / "p0"
    suite.mkdir(parents=True)
    (suite / "test_dummy.py").write_text(body, encoding="utf-8")
    return subprocess.run(
        [sys.executable, str(CHECK), "P0",
         "--tests-root", str(tmp_path / "tests"), "--report-dir", str(tmp_path / "gates")],
        cwd=ROOT, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=120)


def test_check_writes_a_passing_report(tmp_path):
    r = _run(tmp_path, "def test_ok():\n    assert True\n")
    assert r.returncode == 0, r.stdout + r.stderr
    report = (tmp_path / "gates" / "P0-auto.md").read_text(encoding="utf-8")
    assert "| PASS |" in report and "test_ok" in report
    assert "RESULT: PASS" in report


def test_check_fails_on_a_failing_test(tmp_path):
    r = _run(tmp_path, "def test_bad():\n    assert False\n")
    assert r.returncode == 1
    report = (tmp_path / "gates" / "P0-auto.md").read_text(encoding="utf-8")
    assert "| FAIL |" in report and "RESULT: FAIL" in report
