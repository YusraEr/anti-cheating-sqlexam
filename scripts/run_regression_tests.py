#!/usr/bin/env python3
"""
Automated Regression Test Suite for Anti-Cheating SQL Exam System.
Runs all static, unit, security, integrity, and operational tests.
Exit code: 0 if all tests pass, 1 otherwise.
"""
import sys
import subprocess
import time

def run_step(title, cmd):
    print(f"\n>> RUNNING: {title}")
    t0 = time.perf_counter()
    res = subprocess.run(cmd, shell=True, capture_output=True, text=True)
    duration = time.perf_counter() - t0
    if res.returncode == 0:
        print(f"   [PASS] {title} ({duration:.2f}s)")
        return True, res.stdout
    else:
        print(f"   [FAIL] {title} ({duration:.2f}s) - Exit code {res.returncode}")
        if res.stdout:
            print("--- Output ---")
            print(res.stdout[:1000])
        if res.stderr:
            print("--- Error ---")
            print(res.stderr[:1000])
        return False, res.stderr

def main():
    print("=" * 75)
    print("ANTI-CHEATING SQL EXAM - AUTOMATED REGRESSION TEST SUITE")
    print("=" * 75)

    py_bin = sys.executable

    steps = [
        ("Python Compilation (main.py, database.py, scripts/*.py)",
         f'"{py_bin}" -m py_compile main.py database.py scripts/reset_exam_session.py scripts/test_exam_submission_suite.py scripts/test_leaderboard_and_zip_export.py scripts/test_score_retention.py scripts/test_latecomer_and_session_lock.py'),
        ("JavaScript Syntax Check (student.js, admin.js)",
         "node --check static/js/student.js && node --check static/js/admin.js"),
        ("Python Dependencies Check (pip check)",
         f'"{py_bin}" -m pip check'),
        ("Git Diff Whitespace & Line Ending Check",
         "git diff --check"),
        ("Health & Readiness Probe Endpoints Verification",
         f'"{py_bin}" -c "from starlette.testclient import TestClient; from main import app; c = TestClient(app); assert c.get(\'/health\').status_code == 200; assert c.get(\'/ready\').status_code == 200"'),
        ("P0 Security Hardening Verification (12 Security Items)",
         f'"{py_bin}" scratch/test_p0_security.py'),
        ("Deep Integrity & System Architecture Audit (86 Checks)",
         f'"{py_bin}" scratch/audit_deep_inspection.py'),
        ("Graceful Shutdown & Session Recovery Verification",
         f'"{py_bin}" scratch/test_session_recovery.py'),
        ("Admin Table Data Viewer & Inspector Verification",
         f'"{py_bin}" scratch/test_table_data_viewer.py'),
        ("Comprehensive Exam Submission & Auto-Grader Test Suite (29 Tests)",
         f'"{py_bin}" scripts/test_exam_submission_suite.py'),
        ("Leaderboard Scores & ZIP Export Auto-Grader Test Suite (8 Tests)",
         f'"{py_bin}" scripts/test_leaderboard_and_zip_export.py'),
        ("Anti-Score-Regression & Best Score Retention Test Suite (7 Tests)",
         f'"{py_bin}" scripts/test_score_retention.py'),
        ("Latecomer Login, Shared NAT IP & Session Lock Test Suite (7 Tests)",
         f'"{py_bin}" scripts/test_latecomer_and_session_lock.py'),
        ("Concurrent Query Load & Connection Pool Stress Test",
         f'"{py_bin}" scratch/test_concurrent_load.py'),
    ]

    passed_count = 0
    total_count = len(steps)
    start_all = time.perf_counter()

    for title, cmd in steps:
        ok, _ = run_step(title, cmd)
        if ok:
            passed_count += 1
        else:
            print(f"\nFATAL: Regression suite failed on '{title}'")
            sys.exit(1)

    total_time = time.perf_counter() - start_all
    print("\n" + "=" * 75)
    print(f"ALL REGRESSION SUITE TESTS PASSED: {passed_count} / {total_count} (100%) in {total_time:.2f}s")
    print("=" * 75)
    sys.exit(0)

if __name__ == "__main__":
    main()
