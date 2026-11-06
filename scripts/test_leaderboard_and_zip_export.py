#!/usr/bin/env python3
"""
Comprehensive Test Suite for Autograde Scores:
Leaderboard (Real-Time Admin Monitor) & ZIP Archive Export.
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


class LeaderboardAndZipExportTestSuite(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        database.init_system_db()
        cls.client = TestClient(main.app)

        # Setup 3 mock test students
        cls.students_data = [
            {
                "nim": "nim_test_perf_1",
                "name": "Budi Sempurna",
                "ip": "testclient",
                "token": "token_perf_1",
                "category": "FINAL-A",
                "submissions": [
                    (1, "CREATE TABLE ...", 1, 5),
                    (2, "INSERT INTO ...", 1, 5),
                    (3, "SELECT ...", 1, 5),
                    (4, "SELECT ...", 1, 10),
                    (5, "SELECT ...", 1, 20)
                ],
                "expected_total_score": 45,
                "expected_correct_count": 5
            },
            {
                "nim": "nim_test_part_2",
                "name": "Siti Parsial, S.Kom.",
                "ip": "testclient",
                "token": "token_part_2",
                "category": "FINAL-A",
                "submissions": [
                    (1, "CREATE TABLE ...", 1, 5),
                    (2, "INSERT INTO ...", 0, 0),
                    (3, "SELECT ...", 1, 5)
                ],
                "expected_total_score": 10,
                "expected_correct_count": 2
            },
            {
                "nim": "nim_test_zero_3",
                "name": "Joko Kosong",
                "ip": "testclient",
                "token": "token_zero_3",
                "category": "FINAL-A",
                "submissions": [],
                "expected_total_score": 0,
                "expected_correct_count": 0
            }
        ]

        # Clean existing test students
        test_nims = [s["nim"] for s in cls.students_data]
        conn = database.get_sqlite_conn()
        with conn:
            cur = conn.cursor()
            cur.execute(f"DELETE FROM submissions WHERE user_id IN (SELECT id FROM users WHERE nim IN ({','.join('?' for _ in test_nims)}));", test_nims)
            cur.execute(f"DELETE FROM users WHERE nim IN ({','.join('?' for _ in test_nims)});", test_nims)

            # Ensure questions for FINAL-A only include the 5 standard test questions (total 45 pts)
            test_fixture_questions = [
                (1, "Membuat Database & Tabel Master Karyawan", "Buat tabel...", 5, "CREATE TABLE ...;", "sandbox_pribadi", "Easy"),
                (2, "Pemasukan Data Karyawan", "Masukkan data...", 5, "INSERT INTO ...;", "sandbox_pribadi", "Easy"),
                (3, "Pembersihan Tabel Master Karyawan", "Hapus tabel...", 5, "SELECT ...;", "sandbox_pribadi", "Easy"),
                (4, "Film Durasi Panjang dengan Biaya Ganti Ekonomis", "Tampilkan...", 10, "SELECT ...;", "pagila", "Medium"),
                (5, "Pelanggan di Kota Berawalan Huruf A", "Tampilkan...", 20, "SELECT ...;", "pagila", "Hard")
            ]
            cur.execute("INSERT OR IGNORE INTO exam_packages (code, name, description, is_active) VALUES ('FINAL-A', 'Paket Final A', 'Paket Final A Testing', 1);")
            cur.execute("DELETE FROM questions WHERE category = 'FINAL-A';")
            for qnum, title, desc, weight, query, target_db, diff in test_fixture_questions:
                cur.execute("""
                    INSERT OR REPLACE INTO questions (question_number, title, description, score_weight, expected_query, category, target_database, difficulty_level)
                    VALUES (?, ?, ?, ?, ?, 'FINAL-A', ?, ?);
                """, (qnum, title, desc, weight, query, target_db, diff))
            conn.commit()
            cur.execute("SELECT id, question_number, score_weight FROM questions WHERE category = 'FINAL-A' ORDER BY question_number ASC;")
            cls.q_map = {r["question_number"]: dict(r) for r in cur.fetchall()}

            # Insert users and submissions
            cls.user_ids = {}
            for s in cls.students_data:
                cur.execute(
                    "INSERT INTO users (nim, name, ip_address, session_token, status, selected_category) VALUES (?, ?, ?, ?, 'online', ?);",
                    (s["nim"], s["name"], s["ip"], s["token"], s["category"])
                )
                uid = cur.lastrowid
                cls.user_ids[s["nim"]] = uid

                # Insert mock tab switches for user 2
                if s["nim"] == "nim_test_part_2":
                    cur.execute("INSERT INTO exam_logs (user_id, action_type, details) VALUES (?, 'blur_tab', 'Tab switched');", (uid,))
                    cur.execute("INSERT INTO exam_logs (user_id, action_type, details) VALUES (?, 'blur_tab', 'Tab switched');", (uid,))

                for q_num, q_query, is_corr, sc in s["submissions"]:
                    q_info = cls.q_map.get(q_num)
                    if q_info:
                        cur.execute(
                            "INSERT INTO submissions (user_id, question_id, submitted_query, is_correct, score, eval_status, feedback) VALUES (?, ?, ?, ?, ?, ?, ?);",
                            (uid, q_info["id"], q_query, is_corr, sc, "correct" if is_corr else "wrong_data", "Test feedback")
                        )
        conn.close()

    @classmethod
    def tearDownClass(cls):
        test_nims = [s["nim"] for s in cls.students_data]
        conn = database.get_sqlite_conn()
        with conn:
            cur = conn.cursor()
            cur.execute(f"DELETE FROM submissions WHERE user_id IN (SELECT id FROM users WHERE nim IN ({','.join('?' for _ in test_nims)}));", test_nims)
            cur.execute(f"DELETE FROM exam_logs WHERE user_id IN (SELECT id FROM users WHERE nim IN ({','.join('?' for _ in test_nims)}));", test_nims)
            cur.execute(f"DELETE FROM users WHERE nim IN ({','.join('?' for _ in test_nims)});", test_nims)
            cur.execute("DELETE FROM questions WHERE category = 'FINAL-A';")
            cur.execute("DELETE FROM exam_packages WHERE code = 'FINAL-A';")
        conn.close()

    # =========================================================================
    # LEADERBOARD TESTS (/api/admin/students)
    # =========================================================================

    def test_tc_lb_01_total_scores_and_correct_counts(self):
        """TC-LB-01: Leaderboard returns accurate total_score and correct_count."""
        res = self.client.get(
            "/api/admin/students",
            cookies={"admin_session": main.CURRENT_ADMIN_SESSION_TOKEN}
        )
        self.assertEqual(res.status_code, 200)
        data = res.json()
        self.assertIn("students", data)

        students_by_nim = {s["nim"]: s for s in data["students"] if s["nim"] in self.user_ids}

        # 1. Perfect student
        s1 = students_by_nim["nim_test_perf_1"]
        self.assertEqual(s1["total_score"], 45)
        self.assertEqual(s1["correct_count"], 5)
        self.assertEqual(s1["total_submitted"], 5)
        self.assertEqual(s1["max_score"], 45)

        # 2. Partial student
        s2 = students_by_nim["nim_test_part_2"]
        self.assertEqual(s2["total_score"], 10)
        self.assertEqual(s2["correct_count"], 2)
        self.assertEqual(s2["total_submitted"], 3)
        self.assertEqual(s2["max_score"], 45)

        # 3. Zero submission student
        s3 = students_by_nim["nim_test_zero_3"]
        self.assertEqual(s3["total_score"], 0)
        self.assertEqual(s3["correct_count"], 0)
        self.assertEqual(s3["total_submitted"], 0)
        self.assertEqual(s3["max_score"], 45)

    def test_tc_lb_02_leaderboard_ranking_order(self):
        """TC-LB-02: Leaderboard sorts students by total_score descending (Ranking)."""
        res = self.client.get(
            "/api/admin/students",
            cookies={"admin_session": main.CURRENT_ADMIN_SESSION_TOKEN}
        )
        self.assertEqual(res.status_code, 200)
        test_students = [s for s in res.json()["students"] if s["nim"] in self.user_ids]
        scores = [s["total_score"] for s in test_students]

        # Verify sorted in descending order
        self.assertEqual(scores, sorted(scores, reverse=True))
        self.assertEqual(test_students[0]["nim"], "nim_test_perf_1")
        self.assertEqual(test_students[1]["nim"], "nim_test_part_2")
        self.assertEqual(test_students[2]["nim"], "nim_test_zero_3")

    def test_tc_lb_03_warning_counter_accuracy(self):
        """TC-LB-03: Tab switches counter accurately reflected on leaderboard."""
        res = self.client.get(
            "/api/admin/students",
            cookies={"admin_session": main.CURRENT_ADMIN_SESSION_TOKEN}
        )
        self.assertEqual(res.status_code, 200)
        students_by_nim = {s["nim"]: s for s in res.json()["students"] if s["nim"] in self.user_ids}
        self.assertEqual(students_by_nim["nim_test_perf_1"]["tab_switches"], 0)
        self.assertEqual(students_by_nim["nim_test_part_2"]["tab_switches"], 2)

    # =========================================================================
    # ZIP ARCHIVE EXPORT TESTS (/api/admin/export/zip)
    # =========================================================================

    def test_tc_zip_01_valid_zip_archive_and_magic_bytes(self):
        """TC-ZIP-01: Export returns valid ZIP file with PK\\x03\\x04 header."""
        res = self.client.get(
            "/api/admin/export/zip",
            cookies={"admin_session": main.CURRENT_ADMIN_SESSION_TOKEN}
        )
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.headers.get("content-type"), "application/zip")
        self.assertTrue(res.content.startswith(b"PK\x03\x04"), "Must start with ZIP magic bytes")

        # Open in-memory zip archive
        zip_buf = io.BytesIO(res.content)
        with zipfile.ZipFile(zip_buf, "r") as zf:
            file_list = zf.namelist()
            self.assertGreater(len(file_list), 0)

    def test_tc_zip_02_contains_sql_files_for_all_students(self):
        """TC-ZIP-02: ZIP archive contains individual SQL file per student."""
        res = self.client.get(
            "/api/admin/export/zip",
            cookies={"admin_session": main.CURRENT_ADMIN_SESSION_TOKEN}
        )
        self.assertEqual(res.status_code, 200)

        with zipfile.ZipFile(io.BytesIO(res.content), "r") as zf:
            names = zf.namelist()
            self.assertTrue(any("nim_test_perf_1" in n for n in names))
            self.assertTrue(any("nim_test_part_2" in n for n in names))
            self.assertTrue(any("nim_test_zero_3" in n for n in names))

    def test_tc_zip_03_score_headers_in_student_sql(self):
        """TC-ZIP-03: Student SQL files contain total score, correct count, and package header."""
        res = self.client.get(
            "/api/admin/export/zip",
            cookies={"admin_session": main.CURRENT_ADMIN_SESSION_TOKEN}
        )
        self.assertEqual(res.status_code, 200)

        with zipfile.ZipFile(io.BytesIO(res.content), "r") as zf:
            # Check Student 1 (Perfect)
            file_s1 = [n for n in zf.namelist() if "nim_test_perf_1" in n][0]
            content_s1 = zf.read(file_s1).decode("utf-8")
            self.assertIn("Total Nilai   : 45 / 45 Poin (100.0%)", content_s1)
            self.assertIn("Jawaban Benar : 5 dari 5 Soal", content_s1)
            self.assertIn("Paket Soal    : FINAL-A", content_s1)
            self.assertIn("Status: BENAR", content_s1)

            # Check Student 2 (Partial)
            file_s2 = [n for n in zf.namelist() if "nim_test_part_2" in n][0]
            content_s2 = zf.read(file_s2).decode("utf-8")
            self.assertIn("Total Nilai   : 10 / 45 Poin (22.2%)", content_s2)
            self.assertIn("Jawaban Benar : 2 dari 5 Soal", content_s2)
            self.assertIn("Status: BELUM TEPAT", content_s2)

            # Check Student 3 (Zero)
            file_s3 = [n for n in zf.namelist() if "nim_test_zero_3" in n][0]
            content_s3 = zf.read(file_s3).decode("utf-8")
            self.assertIn("Total Nilai   : 0 / 45 Poin (0.0%)", content_s3)
            self.assertIn("Jawaban Benar : 0 dari 5 Soal", content_s3)
            self.assertIn("Status: BELUM DIJAWAB", content_s3)

    def test_tc_zip_04_summary_csv_included_in_zip(self):
        """TC-ZIP-04: ZIP includes rekap_nilai_ujian.csv with all student records."""
        res = self.client.get(
            "/api/admin/export/zip",
            cookies={"admin_session": main.CURRENT_ADMIN_SESSION_TOKEN}
        )
        self.assertEqual(res.status_code, 200)

        with zipfile.ZipFile(io.BytesIO(res.content), "r") as zf:
            self.assertIn("rekap_nilai_ujian.csv", zf.namelist())
            csv_raw = zf.read("rekap_nilai_ujian.csv").decode("utf-8-sig")

            reader = csv.DictReader(io.StringIO(csv_raw))
            rows = {r["NIM"]: r for r in reader if r["NIM"] in self.user_ids}

            # Verify row 1
            r1 = rows["nim_test_perf_1"]
            self.assertEqual(r1["Total Skor"], "45")
            self.assertEqual(r1["Skor Maksimal"], "45")
            self.assertEqual(r1["Soal Benar"], "5")
            self.assertEqual(r1["Persentase Nilai (%)"], "100.0%")

            # Verify row 2
            r2 = rows["nim_test_part_2"]
            self.assertEqual(r2["Total Skor"], "10")
            self.assertEqual(r2["Skor Maksimal"], "45")
            self.assertEqual(r2["Soal Benar"], "2")
            self.assertEqual(r2["Persentase Nilai (%)"], "22.2%")

            # Verify row 3
            r3 = rows["nim_test_zero_3"]
            self.assertEqual(r3["Total Skor"], "0")
            self.assertEqual(r3["Soal Benar"], "0")
            self.assertEqual(r3["Persentase Nilai (%)"], "0.0%")

    def test_tc_zip_05_filename_sanitization(self):
        """TC-ZIP-05: Special characters in student names are safely sanitized."""
        res = self.client.get(
            "/api/admin/export/zip",
            cookies={"admin_session": main.CURRENT_ADMIN_SESSION_TOKEN}
        )
        self.assertEqual(res.status_code, 200)

        with zipfile.ZipFile(io.BytesIO(res.content), "r") as zf:
            file_s2 = [n for n in zf.namelist() if "nim_test_part_2" in n][0]
            # Must not contain comma, period, or spaces
            self.assertNotIn(",", file_s2)
            self.assertNotIn(" ", file_s2)
            self.assertTrue(file_s2.endswith(".sql"))


def run_suite():
    print("=" * 80)
    print("RUNNING LEADERBOARD & ZIP EXPORT AUTOGRADE TEST SUITE")
    print("=" * 80)
    suite = unittest.TestLoader().loadTestsFromTestCase(LeaderboardAndZipExportTestSuite)
    runner = unittest.TextTestRunner(verbosity=2)
    result = runner.run(suite)
    print("\n" + "=" * 80)
    return 0 if result.wasSuccessful() else 1


if __name__ == "__main__":
    sys.exit(run_suite())
