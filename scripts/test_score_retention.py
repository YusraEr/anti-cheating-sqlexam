#!/usr/bin/env python3
"""
Comprehensive Test Suite for Anti-Score-Regression (Best Score Retention)
and Frontend showToast Global Safety.
Verifies that once a student earns full points on a question, accidental
re-submissions with incorrect or broken queries will NEVER reduce their score.
"""

import os
import sys
import io
import csv
import zipfile
import unittest
from typing import Dict, Any

# Ensure project root is in sys.path
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

import database
import main
from fastapi.testclient import TestClient


class ScoreRetentionTestSuite(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        database.init_system_db()
        cls.client = TestClient(main.app)

        cls.test_nim = "nim_test_retention_99"
        cls.test_name = "Mahasiswa Proteksi Nilai"
        cls.test_token = "token_retention_99"
        cls.test_category = "FINAL-A"

        # Clean up any leftover test data
        conn = database.get_sqlite_conn()
        with conn:
            user = conn.execute("SELECT id FROM users WHERE nim = ?;", (cls.test_nim,)).fetchone()
            if user:
                uid = user["id"]
                conn.execute("DELETE FROM submissions WHERE user_id = ?;", (uid,))
                conn.execute("DELETE FROM exam_logs WHERE user_id = ?;", (uid,))
                conn.execute("DELETE FROM users WHERE id = ?;", (uid,))

            # Register fresh student
            conn.execute(
                """
                INSERT INTO users (nim, name, ip_address, session_token, status, selected_category, last_active)
                VALUES (?, ?, 'testclient', ?, 'active', ?, CURRENT_TIMESTAMP);
                """,
                (cls.test_nim, cls.test_name, cls.test_token, cls.test_category)
            )
            created_user = conn.execute("SELECT id FROM users WHERE nim = ?;", (cls.test_nim,)).fetchone()
            cls.user_id = created_user["id"]
        conn.close()

        # Clean student sandbox
        database.cleanup_student_sandbox_schema(cls.test_nim)

        # Set up explicit test questions for FINAL-A (5 standard questions, total 45 pts)
        test_fixture_questions = [
            (1, "Membuat Tabel Pemilik dan Hewan", "Buat tabel...", 5,
             "CREATE TABLE pemilik (id_pemilik INT PRIMARY KEY, nama_pemilik VARCHAR(50) NOT NULL, no_telepon VARCHAR(15) UNIQUE NOT NULL); CREATE TABLE hewan (id_hewan INT PRIMARY KEY, nama_hewan VARCHAR(50) NOT NULL, spesies VARCHAR(30) NOT NULL, id_pemilik INT, CONSTRAINT fk_pemilik FOREIGN KEY (id_pemilik) REFERENCES pemilik(id_pemilik));",
             "sandbox_pribadi", "Easy"),
            (2, "Memasukkan Data Pemilik dan Hewan", "Masukkan data...", 5,
             "INSERT INTO pemilik (id_pemilik, nama_pemilik, no_telepon) VALUES (1, 'Budi Santoso', '081234567890');",
             "sandbox_pribadi", "Easy"),
            (3, "Menampilkan Hewan dan Pemilik", "Tampilkan hewan...", 5,
             "SELECT 1;", "sandbox_pribadi", "Easy"),
            (4, "Aktor Terproduktif", "Tampilkan aktor...", 10,
             "SELECT a.actor_id, CONCAT(a.first_name, ' ', a.last_name) AS nama_lengkap_aktor, COUNT(f.film_id) AS total_film FROM actor a JOIN film_actor fa ON a.actor_id = fa.actor_id JOIN film f ON fa.film_id = f.film_id GROUP BY a.actor_id, a.first_name, a.last_name HAVING COUNT(f.film_id) > 35 ORDER BY total_film DESC;",
             "pagila", "Medium"),
            (5, "Pelanggan Rental di Atas Rata-Rata", "Tampilkan pelanggan...", 20,
             "SELECT 1;", "pagila", "Hard")
        ]
        conn_seed = database.get_sqlite_conn()
        with conn_seed:
            conn_seed.execute("INSERT OR IGNORE INTO exam_packages (code, name, description, is_active) VALUES ('FINAL-A', 'Paket Final A', 'Paket Final A Testing', 1);")
            for qnum, title, desc, weight, query, target_db, diff in test_fixture_questions:
                conn_seed.execute("""
                    INSERT OR REPLACE INTO questions (question_number, title, description, score_weight, expected_query, category, target_database, difficulty_level)
                    VALUES (?, ?, ?, ?, ?, 'FINAL-A', ?, ?);
                """, (qnum, title, desc, weight, query, target_db, diff))
        conn_seed.close()
        questions = database.get_questions_by_category("FINAL-A")
        cls.q1 = next((q for q in questions if q.get("question_number") == 1), None)
        cls.q4 = next((q for q in questions if q.get("question_number") == 4), None)
        assert cls.q1 is not None, "Soal No. 1 Paket FINAL-A harus ada"
        assert cls.q4 is not None, "Soal No. 4 Paket FINAL-A harus ada"

    @classmethod
    def tearDownClass(cls):
        database.cleanup_student_sandbox_schema(cls.test_nim)
        conn = database.get_sqlite_conn()
        with conn:
            conn.execute("DELETE FROM submissions WHERE user_id = ?;", (cls.user_id,))
            conn.execute("DELETE FROM exam_logs WHERE user_id = ?;", (cls.user_id,))
            conn.execute("DELETE FROM users WHERE id = ?;", (cls.user_id,))
            conn.execute("DELETE FROM questions WHERE category = 'FINAL-A';")
            conn.execute("DELETE FROM exam_packages WHERE code = 'FINAL-A';")
        conn.close()

    def test_tc_sr_01_first_submission_correct(self):
        """TC-SR-01: First submission with correct answer grants full score."""
        correct_query = """
        CREATE TABLE pemilik(
            id_pemilik INT PRIMARY KEY,
            nama_pemilik VARCHAR(50) NOT NULL,
            no_telepon VARCHAR(15) NOT NULL UNIQUE
        );
        CREATE TABLE hewan(
            id_hewan INT PRIMARY KEY,
            nama_hewan VARCHAR(50) NOT NULL,
            spesies VARCHAR(30) NOT NULL,
            id_pemilik INT,
            FOREIGN KEY (id_pemilik) REFERENCES pemilik(id_pemilik)
        );
        SELECT * FROM pemilik;
        """
        payload = {
            "nim": self.test_nim,
            "session_token": self.test_token,
            "question_id": self.q1["id"],
            "query": correct_query,
            "active_database": "sandbox_pribadi"
        }
        res = self.client.post("/api/student/submit", json=payload)
        self.assertEqual(res.status_code, 200)
        data = res.json()

        self.assertTrue(data.get("is_correct"))
        self.assertEqual(data.get("score"), 5)
        self.assertFalse(data.get("score_retained"))

        # Verify in database
        sub = database.get_student_submissions(self.user_id).get(self.q1["id"])
        self.assertIsNotNone(sub)
        self.assertEqual(sub["score"], 5)
        self.assertEqual(sub["is_correct"], 1)

    def test_tc_sr_02_resubmission_incorrect_retains_best_score(self):
        """TC-SR-02: Re-submitting an incorrect query preserves previous full score."""
        incorrect_query = "SELECT 'jawaban salah' AS hasil;"
        payload = {
            "nim": self.test_nim,
            "session_token": self.test_token,
            "question_id": self.q1["id"],
            "query": incorrect_query,
            "active_database": "sandbox_pribadi"
        }
        res = self.client.post("/api/student/submit", json=payload)
        self.assertEqual(res.status_code, 200)
        data = res.json()

        # Score must remain 5, is_correct must remain True!
        self.assertTrue(data.get("is_correct"), "is_correct harus tetap True setelah kueri salah")
        self.assertEqual(data.get("score"), 5, "Skor harus tetap 5 poin")
        self.assertTrue(data.get("score_retained"), "Flag score_retained harus bernilai True")
        self.assertIn("tetap aman dipertahankan", data.get("retained_message", ""))

        # Verify in database
        sub = database.get_student_submissions(self.user_id).get(self.q1["id"])
        self.assertIsNotNone(sub)
        self.assertEqual(sub["score"], 5)
        self.assertEqual(sub["is_correct"], 1)
        # Winning query must be preserved
        self.assertIn("CREATE TABLE pemilik", sub["submitted_query"])

    def test_tc_sr_03_resubmission_syntax_error_retains_best_score(self):
        """TC-SR-03: Re-submitting a query with syntax error preserves previous score."""
        broken_query = "THIS IS A COMPLETE SYNTAX ERROR AND BROKEN SQL;"
        payload = {
            "nim": self.test_nim,
            "session_token": self.test_token,
            "question_id": self.q1["id"],
            "query": broken_query,
            "active_database": "sandbox_pribadi"
        }
        res = self.client.post("/api/student/submit", json=payload)
        self.assertEqual(res.status_code, 200)
        data = res.json()

        self.assertTrue(data.get("is_correct"))
        self.assertEqual(data.get("score"), 5)
        self.assertTrue(data.get("score_retained"))

        # Verify audit log in exam_logs
        conn = database.get_sqlite_conn()
        with conn:
            log_row = conn.execute(
                "SELECT details FROM exam_logs WHERE user_id = ? AND action_type = 'submit' ORDER BY id DESC LIMIT 1;",
                (self.user_id,)
            ).fetchone()
            self.assertIsNotNone(log_row)
            self.assertIn("Skor tertinggi 5 Poin tetap dipertahankan", log_row["details"])
        conn.close()

    def test_tc_sr_04_score_upgrade_from_lower_to_higher(self):
        """TC-SR-04: Submitting a higher score updates the record, then regressing retains highest."""
        q4_id = self.q4["id"]

        # 1. First submit a partial/low score (e.g. 3) directly via save_submission
        res1 = database.save_submission(
            user_id=self.user_id,
            question_id=q4_id,
            submitted_query="-- Percobaan pertama (Skor rendah)\nSELECT 1;",
            is_correct=0,
            score=3,
            eval_status="partial_score",
            feedback="Hasil sebagian benar."
        )
        self.assertEqual(res1["score"], 3)
        self.assertFalse(res1["score_retained"])

        # 2. Next submit a higher score (10)
        res2 = database.save_submission(
            user_id=self.user_id,
            question_id=q4_id,
            submitted_query="-- Percobaan kedua (Skor maksimal)\nSELECT 1, 2, 3;",
            is_correct=1,
            score=10,
            eval_status="correct",
            feedback="Jawaban benar sempurna."
        )
        self.assertEqual(res2["score"], 10)
        self.assertTrue(res2["is_correct"])
        self.assertFalse(res2["score_retained"])

        # 3. Next submit a broken query (0)
        res3 = database.save_submission(
            user_id=self.user_id,
            question_id=q4_id,
            submitted_query="-- Percobaan ketiga (Rusak)\nSELECT ERROR;",
            is_correct=0,
            score=0,
            eval_status="syntax_error",
            feedback="Error sintaks."
        )
        self.assertEqual(res3["score"], 10, "Skor harus tetap 10 poin")
        self.assertTrue(res3["is_correct"], "is_correct harus tetap True")
        self.assertTrue(res3["score_retained"], "score_retained harus True")
        self.assertEqual(res3["previous_score"], 10)
        self.assertEqual(res3["new_score"], 0)
        self.assertIn("Percobaan kedua", res3["retained_query"])

    def test_tc_sr_05_leaderboard_progress_reflects_retained_scores(self):
        """TC-SR-05: Leaderboard displays total_score calculated from highest retained scores."""
        progress_list = database.get_all_students_progress()
        student_prog = next((p for p in progress_list if p["nim"] == self.test_nim), None)
        self.assertIsNotNone(student_prog, "Mahasiswa harus ada di scoreboard admin")

        # Q1 has 5 points, Q4 has 10 points -> Total = 15 points
        self.assertEqual(student_prog["total_score"], 15)
        self.assertEqual(student_prog["correct_count"], 2)

    def test_tc_sr_06_zip_and_sql_export_contains_winning_queries(self):
        """TC-SR-06: Exported ZIP and student .sql files contain the winning query and best scores."""
        zip_bytes = database.export_submissions_zip_bytes()
        self.assertTrue(len(zip_bytes) > 0)

        with zipfile.ZipFile(io.BytesIO(zip_bytes), "r") as zf:
            namelist = zf.namelist()

            # 1. Check rekap_nilai_ujian.csv
            self.assertIn("rekap_nilai_ujian.csv", namelist)
            csv_content = zf.read("rekap_nilai_ujian.csv").decode("utf-8-sig")
            reader = csv.DictReader(io.StringIO(csv_content))
            student_csv_row = next((r for r in reader if r["NIM"] == self.test_nim), None)
            self.assertIsNotNone(student_csv_row)
            self.assertEqual(int(student_csv_row["Total Skor"]), 15)

            # 2. Check student's individual .sql file
            student_sql_file = next((name for name in namelist if name.startswith(self.test_nim)), None)
            self.assertIsNotNone(student_sql_file, f"File .sql untuk {self.test_nim} harus ada di ZIP")

            sql_content = zf.read(student_sql_file).decode("utf-8")
            self.assertIn("Total Nilai", sql_content)
            self.assertIn("15 / 45 Poin", sql_content)
            # Winning query must be present
            self.assertIn("CREATE TABLE pemilik", sql_content)
            self.assertNotIn("jawaban salah", sql_content)

    def test_tc_sr_07_frontend_assets_and_toast_safety(self):
        """TC-SR-07: Verify student.js has showToast hoisted at the top and student.html has cache-buster."""
        student_js_path = os.path.join(PROJECT_ROOT, "static", "js", "student.js")
        with open(student_js_path, "r", encoding="utf-8") as f:
            js_lines = f.readlines()

        # Find line number where function showToast is declared
        toast_decl_line = None
        for i, line in enumerate(js_lines):
            if "function showToast(" in line:
                toast_decl_line = i + 1
                break

        self.assertIsNotNone(toast_decl_line, "function showToast harus dideklarasikan di student.js")
        self.assertLess(toast_decl_line, 80, f"showToast harus dideklarasikan di atas (baris {toast_decl_line} < 80)")

        # Verify student.html cache buster
        student_html_path = os.path.join(PROJECT_ROOT, "templates", "student.html")
        with open(student_html_path, "r", encoding="utf-8") as f:
            html_content = f.read()

        self.assertIn("student.js?v=20261104_v1", html_content)
        self.assertIn("style.css?v=20261104_v1", html_content)
        self.assertIn("window.showToast = window.showToast ||", html_content)


if __name__ == "__main__":
    unittest.main(verbosity=2)
