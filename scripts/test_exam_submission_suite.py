#!/usr/bin/env python3
"""
Comprehensive Exam Submission Test Suite for Anti-Cheating SQL Exam System.
Tests all auto-grader engines: DDL, DML, DQL (SELECT), Constraints, Edge Cases,
Security, Sandbox Isolation, and E2E API Score Accumulation.
"""

import os
import sys
import time
import unittest
from typing import Dict, Any

# Ensure project root is in sys.path
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

import database
import main
from fastapi.testclient import TestClient


class ExamSubmissionTestSuite(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        database.init_system_db()
        cls.client = TestClient(main.app)

        cls.test_nim = "autotest_suite_99"
        cls.test_name = "Automated Exam Tester"
        cls.test_ip = "testclient"
        cls.session_token = "suite_token_99_secret"

        # Initialize student in SQLite
        conn = database.get_sqlite_conn()
        with conn:
            cur = conn.cursor()
            cur.execute("DELETE FROM submissions WHERE user_id IN (SELECT id FROM users WHERE nim = ?);", (cls.test_nim,))
            cur.execute("DELETE FROM users WHERE nim = ?;", (cls.test_nim,))
        conn.close()

        ok, user, reason = database.login_student(cls.test_nim, cls.test_name, cls.test_ip, cls.session_token)
        assert ok, f"login_student failed: {reason}"
        cls.client.cookies.set("student_token", cls.session_token)
        cls.client.cookies.set("student_nim", cls.test_nim)

        # Ensure exam package and exact test fixture questions for FINAL-A exist
        conn = database.get_sqlite_conn()
        cur = conn.cursor()
        cur.execute("INSERT OR IGNORE INTO exam_packages (code, name, description, is_active) VALUES ('FINAL-A', 'Paket Final A', 'Paket Final A Testing', 1);")
        test_fixture_questions = [
            (1, "Membuat Tabel Pemilik dan Hewan", "Buat tabel pemilik dan hewan...", 5,
             """CREATE TABLE pemilik (
    id_pemilik INT PRIMARY KEY,
    nama_pemilik VARCHAR(50) NOT NULL,
    no_telepon VARCHAR(15) UNIQUE NOT NULL
);
CREATE TABLE hewan (
    id_hewan INT PRIMARY KEY,
    nama_hewan VARCHAR(50) NOT NULL,
    spesies VARCHAR(30) NOT NULL,
    id_pemilik INT,
    CONSTRAINT fk_pemilik FOREIGN KEY (id_pemilik) REFERENCES pemilik(id_pemilik)
);""", "sandbox_pribadi", "Easy"),
            (2, "Memasukkan Data Pemilik dan Hewan", "Masukkan data...", 5,
             """INSERT INTO pemilik (id_pemilik, nama_pemilik, no_telepon) VALUES
(1, 'Budi Santoso', '081234567890'),
(2, 'Siti Rahma', '081298765432');
INSERT INTO hewan (id_hewan, nama_hewan, spesies, id_pemilik) VALUES
(101, 'Milo', 'Kucing', 1),
(102, 'Bleki', 'Anjing', 2);""", "sandbox_pribadi", "Easy"),
            (3, "Menampilkan Hewan dan Pemilik", "Tampilkan hewan...", 5,
             "SELECT h.id_hewan, h.nama_hewan, h.spesies, p.nama_pemilik FROM hewan h JOIN pemilik p ON h.id_pemilik = p.id_pemilik ORDER BY h.id_hewan ASC;", "sandbox_pribadi", "Easy"),
            (4, "Aktor Terproduktif", "Tampilkan aktor...", 10,
             "SELECT a.actor_id, CONCAT(a.first_name, ' ', a.last_name) AS nama_lengkap_aktor, COUNT(f.film_id) AS total_film FROM actor a JOIN film_actor fa ON a.actor_id = fa.actor_id JOIN film f ON fa.film_id = f.film_id GROUP BY a.actor_id, a.first_name, a.last_name HAVING COUNT(f.film_id) > 35 ORDER BY total_film DESC;", "pagila", "Medium"),
            (5, "Pelanggan Rental di Atas Rata-Rata", "Tampilkan pelanggan...", 20,
             "SELECT c.customer_id, c.first_name, c.last_name, COUNT(r.rental_id) AS total_rental FROM customer c JOIN rental r ON c.customer_id = r.customer_id GROUP BY c.customer_id, c.first_name, c.last_name HAVING COUNT(r.rental_id) > (SELECT AVG(rental_count) FROM (SELECT COUNT(rental_id) AS rental_count FROM rental GROUP BY customer_id) AS avg_rentals) ORDER BY total_rental DESC LIMIT 10;", "pagila", "Hard")
        ]
        for qnum, title, desc, weight, query, target_db, diff in test_fixture_questions:
            cur.execute("""
                INSERT INTO questions (question_number, title, description, score_weight, expected_query, category, target_database, difficulty_level)
                VALUES (?, ?, ?, ?, ?, 'FINAL-A', ?, ?)
                ON CONFLICT(category, question_number) DO UPDATE SET
                    expected_query = excluded.expected_query,
                    score_weight = excluded.score_weight,
                    target_database = excluded.target_database,
                    title = excluded.title;
            """, (qnum, title, desc, weight, query, target_db, diff))
        conn.commit()

        # Select category 'FINAL-A'
        res_cat = cls.client.post("/api/student/select-category", json={
            "nim": cls.test_nim,
            "session_token": cls.session_token,
            "category": "FINAL-A"
        })
        assert res_cat.status_code == 200, f"Select category failed: {res_cat.text}"

        # Fetch questions for FINAL-A
        cur.execute("SELECT * FROM questions WHERE category = 'FINAL-A' ORDER BY question_number ASC;")
        cls.questions = {r["question_number"]: dict(r) for r in cur.fetchall()}
        conn.close()
        assert len(cls.questions) >= 5, f"Expected 5 questions in FINAL-A, got {len(cls.questions)}"

        cls.schema = database.init_student_sandbox_schema(cls.test_nim)

    @classmethod
    def tearDownClass(cls):
        # Clean up student sandbox schema
        pg_conn = database.get_pg_admin_connection(dbname=database.DEFAULT_EXAM_DB)
        pg_conn.autocommit = True
        cur = pg_conn.cursor()
        cur.execute(f"DROP SCHEMA IF EXISTS {cls.schema} CASCADE;")
        cur.close()
        pg_conn.close()

        # Clean up SQLite records
        conn = database.get_sqlite_conn()
        with conn:
            cur = conn.cursor()
            cur.execute("DELETE FROM submissions WHERE user_id IN (SELECT id FROM users WHERE nim = ?);", (cls.test_nim,))
            cur.execute("DELETE FROM users WHERE nim = ?;", (cls.test_nim,))
            cur.execute("DELETE FROM questions WHERE category = 'FINAL-A';")
            cur.execute("DELETE FROM exam_packages WHERE code = 'FINAL-A';")
        conn.close()

    def clean_student_sandbox(self):
        pg_conn = database.get_pg_admin_connection(dbname=database.DEFAULT_EXAM_DB)
        pg_conn.autocommit = True
        cur = pg_conn.cursor()
        cur.execute(f"DROP TABLE IF EXISTS {self.schema}.hewan CASCADE;")
        cur.execute(f"DROP TABLE IF EXISTS {self.schema}.pemilik CASCADE;")
        cur.close()
        pg_conn.close()

    # =========================================================================
    # KATEGORI A: DDL (CREATE TABLE & Constraints) - Soal No. 1
    # =========================================================================

    def test_tc_ddl_01_perfect_answer(self):
        """TC-DDL-01: Perfect answer creating both tables with PK, FK, NOT NULL, UNIQUE."""
        self.clean_student_sandbox()
        q1 = self.questions[1]
        submitted = """
        CREATE TABLE pemilik (
            id_pemilik INT PRIMARY KEY,
            nama_pemilik VARCHAR(50) NOT NULL,
            no_telepon VARCHAR(15) UNIQUE NOT NULL
        );
        CREATE TABLE hewan (
            id_hewan INT PRIMARY KEY,
            nama_hewan VARCHAR(50) NOT NULL,
            spesies VARCHAR(30) NOT NULL,
            id_pemilik INT,
            CONSTRAINT fk_pemilik FOREIGN KEY (id_pemilik) REFERENCES pemilik(id_pemilik)
        );
        """
        res = database.evaluate_sql_submission(
            submitted_query=submitted,
            expected_query=q1["expected_query"],
            score_weight=q1["score_weight"],
            target_db=q1["target_database"],
            active_db=q1["target_database"],
            nim=self.test_nim
        )
        self.assertTrue(res["is_correct"])
        self.assertEqual(res["score"], q1["score_weight"])
        self.assertEqual(res["eval_status"], "correct")
        self.assertIn("Sempurna! Seluruh 2 tabel", res["feedback"])

    def test_tc_ddl_02_partial_table_missing_hewan(self):
        """TC-DDL-02: Partial submission (only pemilik created, hewan missing)."""
        self.clean_student_sandbox()
        q1 = self.questions[1]
        submitted = """
        CREATE TABLE pemilik (
            id_pemilik INT PRIMARY KEY,
            nama_pemilik VARCHAR(50) NOT NULL,
            no_telepon VARCHAR(15) UNIQUE NOT NULL
        );
        """
        res = database.evaluate_sql_submission(
            submitted_query=submitted,
            expected_query=q1["expected_query"],
            score_weight=q1["score_weight"],
            target_db=q1["target_database"],
            active_db=q1["target_database"],
            nim=self.test_nim
        )
        self.assertFalse(res["is_correct"])
        self.assertEqual(res["score"], 0)
        self.assertEqual(res["eval_status"], "missing_table")
        self.assertIn("hewan", res["feedback"])

    def test_tc_ddl_03_missing_unique_constraint(self):
        """TC-DDL-03: Missing UNIQUE constraint on no_telepon."""
        self.clean_student_sandbox()
        q1 = self.questions[1]
        submitted = """
        CREATE TABLE pemilik (
            id_pemilik INT PRIMARY KEY,
            nama_pemilik VARCHAR(50) NOT NULL,
            no_telepon VARCHAR(15) NOT NULL
        );
        CREATE TABLE hewan (
            id_hewan INT PRIMARY KEY,
            nama_hewan VARCHAR(50) NOT NULL,
            spesies VARCHAR(30) NOT NULL,
            id_pemilik INT REFERENCES pemilik(id_pemilik)
        );
        """
        res = database.evaluate_sql_submission(
            submitted_query=submitted,
            expected_query=q1["expected_query"],
            score_weight=q1["score_weight"],
            target_db=q1["target_database"],
            active_db=q1["target_database"],
            nim=self.test_nim
        )
        self.assertFalse(res["is_correct"])
        self.assertEqual(res["score"], 0)
        self.assertEqual(res["eval_status"], "wrong_schema")
        self.assertIn("UNIQUE", res["feedback"])

    def test_tc_ddl_04_missing_foreign_key(self):
        """TC-DDL-04: Missing FOREIGN KEY constraint on hewan(id_pemilik)."""
        self.clean_student_sandbox()
        q1 = self.questions[1]
        submitted = """
        CREATE TABLE pemilik (
            id_pemilik INT PRIMARY KEY,
            nama_pemilik VARCHAR(50) NOT NULL,
            no_telepon VARCHAR(15) UNIQUE NOT NULL
        );
        CREATE TABLE hewan (
            id_hewan INT PRIMARY KEY,
            nama_hewan VARCHAR(50) NOT NULL,
            spesies VARCHAR(30) NOT NULL,
            id_pemilik INT
        );
        """
        res = database.evaluate_sql_submission(
            submitted_query=submitted,
            expected_query=q1["expected_query"],
            score_weight=q1["score_weight"],
            target_db=q1["target_database"],
            active_db=q1["target_database"],
            nim=self.test_nim
        )
        self.assertFalse(res["is_correct"])
        self.assertEqual(res["score"], 0)
        self.assertEqual(res["eval_status"], "wrong_schema")
        self.assertIn("FOREIGN KEY", res["feedback"])

    def test_tc_ddl_05_incompatible_data_type(self):
        """TC-DDL-05: Incompatible data type (id_pemilik declared as VARCHAR instead of INT)."""
        self.clean_student_sandbox()
        q1 = self.questions[1]
        submitted = """
        CREATE TABLE pemilik (
            id_pemilik VARCHAR(20) PRIMARY KEY,
            nama_pemilik VARCHAR(50) NOT NULL,
            no_telepon VARCHAR(15) UNIQUE NOT NULL
        );
        CREATE TABLE hewan (
            id_hewan INT PRIMARY KEY,
            nama_hewan VARCHAR(50) NOT NULL,
            spesies VARCHAR(30) NOT NULL,
            id_pemilik VARCHAR(20) REFERENCES pemilik(id_pemilik)
        );
        """
        res = database.evaluate_sql_submission(
            submitted_query=submitted,
            expected_query=q1["expected_query"],
            score_weight=q1["score_weight"],
            target_db=q1["target_database"],
            active_db=q1["target_database"],
            nim=self.test_nim
        )
        self.assertFalse(res["is_correct"])
        self.assertEqual(res["score"], 0)
        self.assertEqual(res["eval_status"], "wrong_schema")
        self.assertIn("tipe data tidak sesuai", res["feedback"])

    def test_tc_ddl_06_length_mismatch(self):
        """TC-DDL-06: Length mismatch (nama_pemilik VARCHAR(20) instead of VARCHAR(50))."""
        self.clean_student_sandbox()
        q1 = self.questions[1]
        submitted = """
        CREATE TABLE pemilik (
            id_pemilik INT PRIMARY KEY,
            nama_pemilik VARCHAR(20) NOT NULL,
            no_telepon VARCHAR(15) UNIQUE NOT NULL
        );
        CREATE TABLE hewan (
            id_hewan INT PRIMARY KEY,
            nama_hewan VARCHAR(50) NOT NULL,
            spesies VARCHAR(30) NOT NULL,
            id_pemilik INT REFERENCES pemilik(id_pemilik)
        );
        """
        res = database.evaluate_sql_submission(
            submitted_query=submitted,
            expected_query=q1["expected_query"],
            score_weight=q1["score_weight"],
            target_db=q1["target_database"],
            active_db=q1["target_database"],
            nim=self.test_nim
        )
        self.assertFalse(res["is_correct"])
        self.assertEqual(res["score"], 0)
        self.assertEqual(res["eval_status"], "wrong_schema")
        self.assertIn("Ukuran panjang kolom", res["feedback"])

    def test_tc_ddl_07_missing_primary_key(self):
        """TC-DDL-07: Missing PRIMARY KEY on pemilik."""
        self.clean_student_sandbox()
        q1 = self.questions[1]
        submitted = """
        CREATE TABLE pemilik (
            id_pemilik INT,
            nama_pemilik VARCHAR(50) NOT NULL,
            no_telepon VARCHAR(15) UNIQUE NOT NULL
        );
        CREATE TABLE hewan (
            id_hewan INT PRIMARY KEY,
            nama_hewan VARCHAR(50) NOT NULL,
            spesies VARCHAR(30) NOT NULL,
            id_pemilik INT
        );
        """
        res = database.evaluate_sql_submission(
            submitted_query=submitted,
            expected_query=q1["expected_query"],
            score_weight=q1["score_weight"],
            target_db=q1["target_database"],
            active_db=q1["target_database"],
            nim=self.test_nim
        )
        self.assertFalse(res["is_correct"])
        self.assertEqual(res["score"], 0)
        self.assertEqual(res["eval_status"], "wrong_schema")
        self.assertIn("PRIMARY KEY", res["feedback"])

    def test_tc_ddl_08_missing_not_null_constraint(self):
        """TC-DDL-08: Missing NOT NULL constraint on hewan(spesies)."""
        self.clean_student_sandbox()
        q1 = self.questions[1]
        submitted = """
        CREATE TABLE pemilik (
            id_pemilik INT PRIMARY KEY,
            nama_pemilik VARCHAR(50) NOT NULL,
            no_telepon VARCHAR(15) UNIQUE NOT NULL
        );
        CREATE TABLE hewan (
            id_hewan INT PRIMARY KEY,
            nama_hewan VARCHAR(50) NOT NULL,
            spesies VARCHAR(30),
            id_pemilik INT REFERENCES pemilik(id_pemilik)
        );
        """
        res = database.evaluate_sql_submission(
            submitted_query=submitted,
            expected_query=q1["expected_query"],
            score_weight=q1["score_weight"],
            target_db=q1["target_database"],
            active_db=q1["target_database"],
            nim=self.test_nim
        )
        self.assertFalse(res["is_correct"])
        self.assertEqual(res["score"], 0)
        self.assertEqual(res["eval_status"], "wrong_schema")
        self.assertIn("NOT NULL", res["feedback"])

    def test_tc_ddl_09_repeated_execution_idempotency(self):
        """TC-DDL-09: Re-submitting valid DDL handles 'already exists' gracefully."""
        self.clean_student_sandbox()
        q1 = self.questions[1]
        submitted = """
        CREATE TABLE IF NOT EXISTS pemilik (
            id_pemilik INT PRIMARY KEY,
            nama_pemilik VARCHAR(50) NOT NULL,
            no_telepon VARCHAR(15) UNIQUE NOT NULL
        );
        CREATE TABLE IF NOT EXISTS hewan (
            id_hewan INT PRIMARY KEY,
            nama_hewan VARCHAR(50) NOT NULL,
            spesies VARCHAR(30) NOT NULL,
            id_pemilik INT REFERENCES pemilik(id_pemilik)
        );
        """
        # Run first time
        res1 = database.evaluate_sql_submission(
            submitted_query=submitted,
            expected_query=q1["expected_query"],
            score_weight=q1["score_weight"],
            target_db=q1["target_database"],
            active_db=q1["target_database"],
            nim=self.test_nim
        )
        self.assertTrue(res1["is_correct"])

        # Run second time (table already exists)
        res2 = database.evaluate_sql_submission(
            submitted_query=submitted,
            expected_query=q1["expected_query"],
            score_weight=q1["score_weight"],
            target_db=q1["target_database"],
            active_db=q1["target_database"],
            nim=self.test_nim
        )
        self.assertTrue(res2["is_correct"])

    def test_tc_ddl_10_syntax_error(self):
        """TC-DDL-10: Syntax error in DDL (misspelled keyword)."""
        self.clean_student_sandbox()
        q1 = self.questions[1]
        submitted = "CREATE TABEL pemilik (id_pemilik INT);"
        res = database.evaluate_sql_submission(
            submitted_query=submitted,
            expected_query=q1["expected_query"],
            score_weight=q1["score_weight"],
            target_db=q1["target_database"],
            active_db=q1["target_database"],
            nim=self.test_nim
        )
        self.assertFalse(res["is_correct"])
        self.assertEqual(res["score"], 0)
        self.assertIn(res["eval_status"], ["syntax_error", "missing_table"])

    def test_tc_ddl_11_select_bypass_rejected(self):
        """TC-DDL-11: Submitting pure SELECT or comment+SELECT on DDL question is rejected."""
        self.clean_student_sandbox()
        q1 = self.questions[1]
        submitted = "-- Jawaban Soal 1\nSELECT;"
        res = database.evaluate_sql_submission(
            submitted_query=submitted,
            expected_query=q1["expected_query"],
            score_weight=q1["score_weight"],
            target_db=q1["target_database"],
            active_db=q1["target_database"],
            nim=self.test_nim
        )
        self.assertFalse(res["is_correct"])
        self.assertEqual(res["score"], 0)
        self.assertEqual(res["eval_status"], "missing_table")

    # =========================================================================
    # KATEGORI B: DML (INSERT INTO & Data Consistency) - Soal No. 2
    # =========================================================================

    def test_tc_dml_01_perfect_data_insertion(self):
        """TC-DML-01: Perfect data insertion into pemilik and hewan."""
        # Ensure DDL exists first
        self.test_tc_ddl_01_perfect_answer()

        q2 = self.questions[2]
        submitted = q2["expected_query"]
        res = database.evaluate_sql_submission(
            submitted_query=submitted,
            expected_query=q2["expected_query"],
            score_weight=q2["score_weight"],
            target_db=q2["target_database"],
            active_db=q2["target_database"],
            nim=self.test_nim
        )
        self.assertTrue(res["is_correct"])
        self.assertEqual(res["score"], q2["score_weight"])
        self.assertEqual(res["eval_status"], "correct")

    def test_tc_dml_02_missing_rows(self):
        """TC-DML-02: Missing rows in DML insertion."""
        # Reset tables
        self.test_tc_ddl_01_perfect_answer()
        q2 = self.questions[2]
        # Insert only 1 pemilik and 1 hewan
        submitted = """
        INSERT INTO pemilik (id_pemilik, nama_pemilik, no_telepon) VALUES
        (1, 'Andi Pratama', '081234567890');
        INSERT INTO hewan (id_hewan, nama_hewan, spesies, id_pemilik) VALUES
        (101, 'Milo', 'Kucing', 1);
        """
        res = database.evaluate_sql_submission(
            submitted_query=submitted,
            expected_query=q2["expected_query"],
            score_weight=q2["score_weight"],
            target_db=q2["target_database"],
            active_db=q2["target_database"],
            nim=self.test_nim
        )
        self.assertFalse(res["is_correct"])
        self.assertEqual(res["score"], 0)
        self.assertEqual(res["eval_status"], "wrong_data")

    def test_tc_dml_03_foreign_key_violation(self):
        """TC-DML-03: Inserting hewan with non-existent id_pemilik triggers FK violation."""
        self.test_tc_ddl_01_perfect_answer()
        q2 = self.questions[2]
        submitted = """
        INSERT INTO hewan (id_hewan, nama_hewan, spesies, id_pemilik) VALUES
        (999, 'Ghost', 'Kucing', 9999);
        """
        res = database.evaluate_sql_submission(
            submitted_query=submitted,
            expected_query=q2["expected_query"],
            score_weight=q2["score_weight"],
            target_db=q2["target_database"],
            active_db=q2["target_database"],
            nim=self.test_nim
        )
        self.assertFalse(res["is_correct"])
        self.assertEqual(res["score"], 0)
        self.assertEqual(res["eval_status"], "syntax_error")
        self.assertIn("foreign key constraint", res["feedback"].lower())

    def test_tc_dml_04_missing_ddl_prerequisite(self):
        """TC-DML-04: Submitting DML when tables do not exist in sandbox."""
        self.clean_student_sandbox()
        q2 = self.questions[2]
        res = database.evaluate_sql_submission(
            submitted_query=q2["expected_query"],
            expected_query=q2["expected_query"],
            score_weight=q2["score_weight"],
            target_db=q2["target_database"],
            active_db=q2["target_database"],
            nim=self.test_nim
        )
        self.assertFalse(res["is_correct"])
        self.assertEqual(res["score"], 0)
        self.assertEqual(res["eval_status"], "table_not_found")

    def test_tc_dml_05_select_bypass_rejected(self):
        """TC-DML-05: Submitting pure SELECT or comment+SELECT on DML question is rejected."""
        self.test_tc_ddl_01_perfect_answer()
        q2 = self.questions[2]
        submitted = "-- Jawaban Soal 2\nSELECT;"
        res = database.evaluate_sql_submission(
            submitted_query=submitted,
            expected_query=q2["expected_query"],
            score_weight=q2["score_weight"],
            target_db=q2["target_database"],
            active_db=q2["target_database"],
            nim=self.test_nim
        )
        self.assertFalse(res["is_correct"])
        self.assertEqual(res["score"], 0)
        self.assertEqual(res["eval_status"], "missing_dml")

    # =========================================================================
    # KATEGORI C: DQL (SELECT, JOIN, Aggregations on pagila) - Soal No. 4 & No. 5
    # =========================================================================

    def test_tc_dql_01_question4_perfect_query(self):
        """TC-DQL-01: Perfect query on pagila for Question 4."""
        q4 = self.questions[4]
        res = database.evaluate_sql_submission(
            submitted_query=q4["expected_query"],
            expected_query=q4["expected_query"],
            score_weight=q4["score_weight"],
            target_db=q4["target_database"],
            active_db=q4["target_database"],
            nim=self.test_nim
        )
        self.assertTrue(res["is_correct"])
        self.assertEqual(res["score"], q4["score_weight"])
        self.assertEqual(res["eval_status"], "correct")

    def test_tc_dql_02_question4_column_alias_mismatch(self):
        """TC-DQL-02: Question 4 with wrong column alias."""
        q4 = self.questions[4]
        submitted = q4["expected_query"].replace("nama_lengkap_aktor", "nama_aktor")
        res = database.evaluate_sql_submission(
            submitted_query=submitted,
            expected_query=q4["expected_query"],
            score_weight=q4["score_weight"],
            target_db=q4["target_database"],
            active_db=q4["target_database"],
            nim=self.test_nim
        )
        self.assertFalse(res["is_correct"])
        self.assertEqual(res["score"], 0)
        self.assertIn(res["eval_status"], ["wrong_columns", "column_mismatch"])

    def test_tc_dql_03_question4_order_mismatch(self):
        """TC-DQL-03: Question 4 with wrong ordering (ASC instead of DESC)."""
        q4 = self.questions[4]
        submitted = q4["expected_query"].replace("ORDER BY total_film DESC", "ORDER BY total_film ASC")
        res = database.evaluate_sql_submission(
            submitted_query=submitted,
            expected_query=q4["expected_query"],
            score_weight=q4["score_weight"],
            target_db=q4["target_database"],
            active_db=q4["target_database"],
            nim=self.test_nim
        )
        self.assertFalse(res["is_correct"])
        self.assertEqual(res["score"], 0)
        self.assertIn(res["eval_status"], ["wrong_order", "order_mismatch"])

    def test_tc_dql_04_question4_filter_mismatch(self):
        """TC-DQL-04: Question 4 with wrong HAVING filter threshold."""
        q4 = self.questions[4]
        submitted = q4["expected_query"].replace("HAVING COUNT(f.film_id) > 35", "HAVING COUNT(f.film_id) > 30")
        res = database.evaluate_sql_submission(
            submitted_query=submitted,
            expected_query=q4["expected_query"],
            score_weight=q4["score_weight"],
            target_db=q4["target_database"],
            active_db=q4["target_database"],
            nim=self.test_nim
        )
        self.assertFalse(res["is_correct"])
        self.assertEqual(res["score"], 0)
        self.assertIn(res["eval_status"], ["row_count_mismatch", "wrong_data"])

    def test_tc_dql_05_question5_perfect_subquery(self):
        """TC-DQL-05: Perfect query on pagila for Question 5 (Multi-join & Subquery)."""
        q5 = self.questions[5]
        res = database.evaluate_sql_submission(
            submitted_query=q5["expected_query"],
            expected_query=q5["expected_query"],
            score_weight=q5["score_weight"],
            target_db=q5["target_database"],
            active_db=q5["target_database"],
            nim=self.test_nim
        )
        self.assertTrue(res["is_correct"])
        self.assertEqual(res["score"], q5["score_weight"])
        self.assertEqual(res["eval_status"], "correct")

    def test_tc_dql_06_database_routing_mismatch(self):
        """TC-DQL-06: Submitting pagila query against classicmodels database."""
        q4 = self.questions[4]
        res = database.evaluate_sql_submission(
            submitted_query=q4["expected_query"],
            expected_query=q4["expected_query"],
            score_weight=q4["score_weight"],
            target_db=q4["target_database"],  # pagila
            active_db="classicmodels",       # student selected classicmodels
            nim=self.test_nim
        )
        self.assertFalse(res["is_correct"])
        self.assertEqual(res["score"], 0)
        self.assertEqual(res["eval_status"], "wrong_database")

    def test_tc_dql_07_decimal_trailing_zeros_and_integer_types(self):
        """TC-DQL-07: Verify decimal trailing zeros (e.g. 68.30, 0.00) and integer types are preserved."""
        # Test classicmodels numeric with trailing zero
        res1 = database.execute_student_query(
            "SELECT product_line, COUNT(*) AS total_produk, ROUND(AVG(buy_price), 2) AS rata_rata_harga FROM products WHERE product_line = 'Planes' GROUP BY product_line;",
            active_db="classicmodels"
        )
        self.assertTrue(res1["success"])
        self.assertEqual(len(res1["rows"]), 1)
        row = res1["rows"][0]
        self.assertEqual(row[0], "Planes")
        self.assertEqual(row[1], 1)
        self.assertIsInstance(row[1], int)
        self.assertEqual(row[2], "68.30")

        # Test pagila numeric 0.00 and boolean activebool
        res2 = database.execute_student_query(
            "SELECT activebool, amount FROM payment JOIN customer USING (customer_id) WHERE amount = 0.00 LIMIT 1;",
            active_db="pagila"
        )
        self.assertTrue(res2["success"])
        self.assertEqual(len(res2["rows"]), 1)
        row2 = res2["rows"][0]
        self.assertIsInstance(row2[0], bool)
        self.assertEqual(row2[1], "0.00")

    # =========================================================================
    # KATEGORI D: Sandbox Lifecycle & Isolation - Soal No. 3
    # =========================================================================

    def test_tc_sbx_01_question3_select_and_cleanup(self):
        """TC-SBX-01: Question 3 SELECT data from hewan followed by DROP TABLE cleanup."""
        # Ensure DDL & DML are run first so tables and data exist
        self.test_tc_dml_01_perfect_data_insertion()

        q3 = self.questions[3]
        res = database.evaluate_sql_submission(
            submitted_query=q3["expected_query"],
            expected_query=q3["expected_query"],
            score_weight=q3["score_weight"],
            target_db=q3["target_database"],
            active_db=q3["target_database"],
            nim=self.test_nim
        )
        self.assertTrue(res["is_correct"])
        self.assertEqual(res["score"], q3["score_weight"])
        self.assertEqual(res["eval_status"], "correct")

    def test_tc_sbx_02_multi_student_sandbox_isolation(self):
        """TC-SBX-02: Cross-tenant isolation between two students."""
        nim_a = f"{self.test_nim}_a"
        nim_b = f"{self.test_nim}_b"
        schema_a = database.init_student_sandbox_schema(nim_a)
        schema_b = database.init_student_sandbox_schema(nim_b)

        # Student A creates a private secret table
        res_a = database.execute_student_query(
            "CREATE TABLE IF NOT EXISTS secret_a (val TEXT); INSERT INTO secret_a VALUES ('token_a');",
            active_db="sandbox_pribadi",
            nim=nim_a
        )
        self.assertTrue(res_a["success"])

        # Student B tries to query secret_a (must fail because it does not exist in schema_b)
        res_b = database.execute_student_query(
            "SELECT * FROM secret_a;",
            active_db="sandbox_pribadi",
            nim=nim_b
        )
        self.assertFalse(res_b["success"])
        self.assertIn("does not exist", res_b["error"].lower())

        # Cleanup isolated test schemas
        pg_conn = database.get_pg_admin_connection(dbname=database.DEFAULT_EXAM_DB)
        pg_conn.autocommit = True
        cur = pg_conn.cursor()
        cur.execute(f"DROP SCHEMA IF EXISTS {schema_a} CASCADE;")
        cur.execute(f"DROP SCHEMA IF EXISTS {schema_b} CASCADE;")
        cur.close()
        pg_conn.close()

    # =========================================================================
    # KATEGORI E: Security, Edge Cases & Performance
    # =========================================================================

    def test_tc_sec_01_forbidden_commands_blocked(self):
        """TC-SEC-01: P0 dangerous SQL commands blocked."""
        dangerous_queries = [
            "SELECT * FROM pg_shadow;",
            "ALTER USER postgres WITH PASSWORD 'hack';",
            "GRANT ALL PRIVILEGES ON ALL TABLES IN SCHEMA public TO public;",
            "SELECT pg_terminate_backend(1);",
        ]
        for q in dangerous_queries:
            res = database.execute_student_query(q, active_db="pagila", nim=self.test_nim)
            self.assertFalse(res["success"], f"Expected blocked query: {q}")
            self.assertIn("Perintah berbahaya terdeteksi", res["error"])

    def test_tc_sec_02_os_access_blocked(self):
        """TC-SEC-02: File system / OS command attempts blocked."""
        res = database.execute_student_query("COPY actor TO '/tmp/actor.csv';", active_db="pagila", nim=self.test_nim)
        self.assertFalse(res["success"])
        self.assertIn("Perintah berbahaya terdeteksi", res["error"])

    def test_tc_edge_01_empty_query(self):
        """TC-EDGE-01: Empty or comment-only query rejected."""
        q1 = self.questions[1]
        for empty_q in ["", "   ", "-- just a comment"]:
            res = database.evaluate_sql_submission(
                submitted_query=empty_q,
                expected_query=q1["expected_query"],
                score_weight=q1["score_weight"],
                target_db=q1["target_database"],
                active_db=q1["target_database"],
                nim=self.test_nim
            )
            self.assertFalse(res["is_correct"])
            self.assertEqual(res["score"], 0)

    # =========================================================================
    # KATEGORI F: End-to-End API Submission Flow
    # =========================================================================

    def test_tc_e2e_01_full_exam_submission_flow(self):
        """TC-E2E-01: Complete student submission via HTTP API (/api/student/submit)."""
        self.clean_student_sandbox()

        total_earned = 0
        total_possible = sum(self.questions[i]["score_weight"] for i in range(1, 6))

        # Submit Question 1
        res1 = self.client.post("/api/student/submit", json={
            "nim": self.test_nim,
            "session_token": self.session_token,
            "question_id": self.questions[1]["id"],
            "query": self.questions[1]["expected_query"],
            "active_database": "sandbox_pribadi"
        })
        self.assertEqual(res1.status_code, 200)
        data1 = res1.json()
        self.assertTrue(data1["is_correct"])
        total_earned += data1["score"]

        # Submit Question 2
        res2 = self.client.post("/api/student/submit", json={
            "nim": self.test_nim,
            "session_token": self.session_token,
            "question_id": self.questions[2]["id"],
            "query": self.questions[2]["expected_query"],
            "active_database": "sandbox_pribadi"
        })
        self.assertEqual(res2.status_code, 200)
        data2 = res2.json()
        self.assertTrue(data2["is_correct"])
        total_earned += data2["score"]

        # Submit Question 3
        res3 = self.client.post("/api/student/submit", json={
            "nim": self.test_nim,
            "session_token": self.session_token,
            "question_id": self.questions[3]["id"],
            "query": self.questions[3]["expected_query"],
            "active_database": "sandbox_pribadi"
        })
        self.assertEqual(res3.status_code, 200)
        data3 = res3.json()
        self.assertTrue(data3["is_correct"])
        total_earned += data3["score"]

        # Submit Question 4
        res4 = self.client.post("/api/student/submit", json={
            "nim": self.test_nim,
            "session_token": self.session_token,
            "question_id": self.questions[4]["id"],
            "query": self.questions[4]["expected_query"],
            "active_database": "pagila"
        })
        self.assertEqual(res4.status_code, 200)
        data4 = res4.json()
        self.assertTrue(data4["is_correct"])
        total_earned += data4["score"]

        # Submit Question 5
        res5 = self.client.post("/api/student/submit", json={
            "nim": self.test_nim,
            "session_token": self.session_token,
            "question_id": self.questions[5]["id"],
            "query": self.questions[5]["expected_query"],
            "active_database": "pagila"
        })
        self.assertEqual(res5.status_code, 200)
        data5 = res5.json()
        self.assertTrue(data5["is_correct"])
        total_earned += data5["score"]

        self.assertEqual(total_earned, total_possible)


def run_suite():
    print("=" * 80)
    print("RUNNING COMPREHENSIVE EXAM SUBMISSION TEST SUITE")
    print("=" * 80)
    suite = unittest.TestLoader().loadTestsFromTestCase(ExamSubmissionTestSuite)
    t0 = time.perf_counter()
    runner = unittest.TextTestRunner(verbosity=2)
    result = runner.run(suite)
    duration = time.perf_counter() - t0

    print("\n" + "=" * 80)
    if result.wasSuccessful():
        print(f"SUCCESS: All {result.testsRun} test cases passed in {duration:.2f}s!")
        print("=" * 80)
        return 0
    else:
        print(f"FAILED: {len(result.failures)} failures, {len(result.errors)} errors in {duration:.2f}s.")
        print("=" * 80)
        return 1


if __name__ == "__main__":
    sys.exit(run_suite())
