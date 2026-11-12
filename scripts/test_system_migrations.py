from contextlib import closing
"""Fresh-install, upgrade, rollback, and data-retention checks for SQLite."""

import os
from pathlib import Path
import sqlite3
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import database


class SystemMigrationTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory(prefix="sqlexam_migration_")
        self.addCleanup(self.directory.cleanup)
        self.path = Path(self.directory.name) / "exam.db"
        self.env = patch.dict(os.environ, {"EXAM_SQLITE_PATH": str(self.path),
                                          "ADMIN_MASTER_KEY": "migration-test"})
        self.env.start()
        self.addCleanup(self.env.stop)

    def legacy_database(self, scored=False):
        conn = sqlite3.connect(self.path)
        conn.executescript("""
            CREATE TABLE users (
                id INTEGER PRIMARY KEY, nim TEXT UNIQUE NOT NULL, name TEXT NOT NULL,
                ip_address TEXT, session_token TEXT, status TEXT DEFAULT 'offline',
                last_active TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP);
            CREATE TABLE questions (
                id INTEGER PRIMARY KEY, category TEXT DEFAULT 'A', question_number INTEGER,
                title TEXT, description TEXT, score_weight INTEGER DEFAULT 10,
                expected_query TEXT, created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(category, question_number));
            CREATE TABLE submissions (
                id INTEGER PRIMARY KEY, user_id INTEGER REFERENCES users(id),
                question_id INTEGER REFERENCES questions(id), submitted_query TEXT,
                submitted_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP, status TEXT DEFAULT 'submitted',
                UNIQUE(user_id, question_id));
            CREATE TABLE system_settings (key TEXT PRIMARY KEY, value TEXT NOT NULL);
            INSERT INTO system_settings VALUES ('initial_seed_completed', '1');
            INSERT INTO users (id, nim, name) VALUES (7, 'LEGACY001', 'Legacy Student');
            INSERT INTO questions (id, category, question_number, title, description)
                VALUES (9, 'LEGACY', 1, 'Existing question', 'Keep this question');
            INSERT INTO submissions (id, user_id, question_id, submitted_query)
                VALUES (11, 7, 9, 'SELECT 42;');
        """)
        if scored:
            conn.executescript("""
                ALTER TABLE submissions ADD COLUMN score INTEGER DEFAULT 0;
                ALTER TABLE submissions ADD COLUMN is_correct INTEGER DEFAULT 0;
                ALTER TABLE submissions ADD COLUMN eval_status TEXT DEFAULT 'pending';
                ALTER TABLE submissions ADD COLUMN feedback TEXT DEFAULT '';
                UPDATE submissions SET score = 10, is_correct = 1,
                    eval_status = 'correct', feedback = 'Keep feedback';
            """)
        conn.commit()
        conn.close()

    def snapshot(self):
        conn = database.get_sqlite_conn()
        try:
            return {table: [tuple(row) for row in conn.execute(f"SELECT * FROM {table} ORDER BY 1")]
                    for table in ("users", "questions", "submissions", "system_settings", "exam_logs", "exam_packages")}
        finally:
            conn.close()

    def test_fresh_install_saves_scores_and_exports(self):
        database.init_system_db()
        ok, user, _ = database.login_student("FRESH001", "Fresh Student", "testclient", "fresh-token")
        self.assertTrue(ok)
        question = database.get_all_questions()[0]
        database.lock_student_category("FRESH001", question["category"])
        result = database.save_submission(user["id"], question["id"], "SELECT 1;", 1, 10, "correct", "Saved")
        self.assertEqual(result["score"], 10)
        submissions = database.get_student_submissions(user["id"])
        self.assertEqual(submissions[question["id"]]["score"], 10)
        self.assertEqual(database.get_all_students_progress()[0]["total_score"], 10)
        self.assertIn("Fresh Student", database.export_all_submissions_csv())
        self.assertTrue(database.export_submissions_zip_bytes().startswith(b"PK"))

    def test_legacy_upgrade_preserves_answers_and_assigns_pending_defaults(self):
        self.legacy_database()
        database.init_system_db()
        conn = database.get_sqlite_conn()
        try:
            row = conn.execute("SELECT * FROM submissions WHERE id=11").fetchone()
            self.assertEqual((row["user_id"], row["question_id"], row["submitted_query"]), (7, 9, "SELECT 42;"))
            self.assertEqual((row["score"], row["is_correct"], row["eval_status"], row["feedback"]), (0, 0, "pending", ""))
            self.assertEqual(conn.execute("PRAGMA user_version").fetchone()[0], database.SQLITE_SCHEMA_VERSION)
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM questions").fetchone()[0], 1)
            self.assertEqual(conn.execute("SELECT name FROM users WHERE id=7").fetchone()[0], "Legacy Student")
        finally:
            conn.close()

    def test_existing_scores_and_feedback_are_preserved(self):
        self.legacy_database(scored=True)
        database.init_system_db()
        row = self.snapshot()["submissions"][0]
        self.assertEqual(row[-5:-1], (10, 1, "correct", "Keep feedback"))

    def test_phase4_imports_legacy_best_once_into_history(self):
        self.legacy_database(scored=True)
        database.init_system_db()
        database.init_system_db()
        with closing(database.get_sqlite_conn()) as conn, conn:
            attempts = conn.execute('SELECT * FROM submission_attempts').fetchall()
        self.assertEqual(len(attempts), 1)
        self.assertEqual(attempts[0]['source'], 'legacy_best')
        self.assertEqual(attempts[0]['score'], 10)
        self.assertEqual(attempts[0]['feedback'], 'Keep feedback')

    def test_phase4_missing_attempts_table_is_inconsistent(self):
        database.init_system_db()
        with closing(database.get_sqlite_conn()) as conn, conn:
            conn.execute('DROP TABLE submission_attempts')
        with self.assertRaisesRegex(RuntimeError, 'submission_attempts'):
            database.init_system_db()

    def test_partial_manual_upgrade_preserves_existing_score(self):
        self.legacy_database()
        conn = sqlite3.connect(self.path)
        conn.execute("ALTER TABLE submissions ADD COLUMN score INTEGER DEFAULT 0")
        conn.execute("UPDATE submissions SET score=7")
        conn.commit()
        conn.close()
        database.init_system_db()
        conn = database.get_sqlite_conn()
        try:
            self.assertEqual(conn.execute("SELECT score, eval_status FROM submissions").fetchone()[:], (7, "pending"))
        finally:
            conn.close()

    def test_repeated_initialization_does_not_change_data(self):
        self.legacy_database(scored=True)
        database.init_system_db()
        before = self.snapshot()
        database.init_system_db()
        self.assertEqual(self.snapshot(), before)

    def test_migration_failure_rolls_back_columns_and_version(self):
        self.legacy_database()
        original = database._add_missing_columns

        def fail_second_version(conn, columns):
            original(conn, columns)
            if columns[0][0] == "submissions":
                raise sqlite3.OperationalError("simulated migration failure")

        with patch.object(database, "_add_missing_columns", side_effect=fail_second_version):
            with self.assertRaisesRegex(sqlite3.OperationalError, "simulated"):
                database.init_system_db()
        conn = sqlite3.connect(self.path)
        try:
            self.assertEqual(conn.execute("PRAGMA user_version").fetchone()[0], 0)
            self.assertNotIn("score", [row[1] for row in conn.execute("PRAGMA table_info(submissions)")])
            self.assertNotIn("virtual_db", [row[1] for row in conn.execute("PRAGMA table_info(users)")])
            self.assertEqual(conn.execute("SELECT submitted_query FROM submissions").fetchone()[0], "SELECT 42;")
        finally:
            conn.close()
        database.init_system_db()

    def test_newer_version_is_rejected_without_schema_changes(self):
        self.legacy_database()
        conn = sqlite3.connect(self.path)
        conn.execute("PRAGMA user_version=999")
        before = conn.execute("SELECT sql FROM sqlite_master ORDER BY name").fetchall()
        conn.close()
        with self.assertRaisesRegex(RuntimeError, "Unsupported"):
            database.init_system_db()
        conn = sqlite3.connect(self.path)
        try:
            self.assertEqual(conn.execute("SELECT sql FROM sqlite_master ORDER BY name").fetchall(), before)
        finally:
            conn.close()

    def test_inconsistent_version_is_reported(self):
        self.legacy_database()
        conn = sqlite3.connect(self.path)
        conn.execute(f"PRAGMA user_version={database.SQLITE_SCHEMA_VERSION}")
        conn.close()
        with self.assertRaisesRegex(RuntimeError, "inconsistent"):
            database.init_system_db()


if __name__ == "__main__":
    unittest.main(verbosity=2)
