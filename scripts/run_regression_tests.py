"""Run tracked regression checks against disposable databases."""

import argparse
import os
from pathlib import Path
import subprocess
import sys
import time

from test_support import ROOT, isolated_environment


def run_step(title, command):
    started = time.perf_counter()
    print(f"\n>> {title}", flush=True)
    try:
        result = subprocess.run(command, cwd=ROOT, env=os.environ.copy(), timeout=180,
                                stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                text=True, encoding="utf-8", errors="replace")
    except (OSError, subprocess.TimeoutExpired) as error:
        print(f"[FAIL] {error}")
        return False
    print(result.stdout.rstrip())
    ok = result.returncode == 0
    print(f"[{'PASS' if ok else 'FAIL'}] {title} ({time.perf_counter() - started:.2f}s)", flush=True)
    return ok


def main():
    sys.stdout.reconfigure(errors="backslashreplace")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sqlite-only", action="store_true",
                        help="Run migration checks without PostgreSQL; not the full regression suite.")
    args = parser.parse_args()
    py = sys.executable
    files = ["main.py", "database.py", "sql_policy.py", "sandbox_roles.py", "query_runner.py", "grading_engine.py", "grading_workspace.py", "grading_results.py", "grading_values.py", "admin_security.py", "admin_practice.py", "question_uploads.py", "exam_backup.py", "grading_queue.py"] + [str(p.relative_to(ROOT)) for p in sorted((ROOT / "scripts").glob("*.py"))]
    steps = [
        ("Python syntax", [py, "-m", "py_compile", *files]),
        ("Student JavaScript syntax", ["node", "--check", "static/js/student.js"]),
        ("Admin JavaScript syntax", ["node", "--check", "static/js/admin.js"]),
        ("Frontend runtime behaviors", ["node", "--test", "scripts/test_frontend_behaviors.cjs"]),
        ("Admin rejection, injection, and polling behaviors", ["node", "--test", "scripts/test_admin_behaviors.cjs"]),
        ("Dependency consistency", [py, "-m", "pip", "check"]),
        ("Diff whitespace", ["git", "diff", "--check"]),
        ("Fresh installation and legacy migrations", [py, "scripts/test_system_migrations.py"]),
        ("Exam access and session lifecycle", [py, "scripts/test_exam_access.py"]),
        ("Prepared exam readiness and invalidation", [py, "scripts/test_exam_readiness.py"]),
        ("Exam start races and lost-response recovery", [py, "scripts/test_exam_start_failures.py"]),
        ("Admin sessions, CSRF, credentials, and telemetry", [py, "scripts/test_admin_security.py"]),
        ("Admin practice and student IDE isolation", [py, "scripts/test_admin_practice.py"]),
        ("Admin audit fixes and upload limits", [py, "scripts/test_admin_fixes.py"]),
        ("Offline backup integrity and recovery", [py, "scripts/test_backup.py"]),
        ("Operational controls and NAT admission", [py, "scripts/test_operational_controls.py"]),
        ("Persistent grading queue and manual decisions", [py, "scripts/test_grading_queue.py"]),
        ("Final-test numbering and conflicts", [py, "scripts/test_minutes_fixes.py"]),
        ("SQLite setup and upgrade", [py, "scripts/test_setup_sqlite.py"]),
        ("SQL parser policy", [py, "scripts/test_sql_policy.py"]),
        ("Typed full result comparison", [py, "scripts/test_grading_results.py"]),
    ]
    results = [run_step(title, command) for title, command in steps]
    if not args.sqlite_only:
        try:
            with isolated_environment() as root:
                print(f"\nDisposable PostgreSQL: 127.0.0.1:{os.environ['PG_PORT']}; fixture: {root}", flush=True)
                for script in (
                    "test_exam_submission_suite.py", "test_leaderboard_and_zip_export.py",
                    "test_score_retention.py", "test_latecomer_and_session_lock.py",
                    "test_operations.py", "test_backup_postgres.py", "test_sandbox_security.py", "test_grading_engine.py", "test_grading_fairness.py", "test_grading_queue_postgres.py", "test_admin_practice_postgres.py", "test_known_risks.py",
                ):
                    os.environ["EXAM_SQLITE_PATH"] = str(root / (Path(script).stem + ".db"))
                    results.append(run_step(script, [py, str(ROOT / "scripts" / script)]))
        except Exception as error:
            print(f"[FAIL] Disposable PostgreSQL setup/cleanup: {error}")
            results.append(False)
    print(f"\n{'SQLite-only checks' if args.sqlite_only else 'Full regression'}: "
          f"{sum(results)}/{len(results)} checks passed.")
    if not args.sqlite_only:
        print("Tracked risk regressions execute as ordinary tests.")
    return 0 if all(results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
