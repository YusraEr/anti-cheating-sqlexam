#!/usr/bin/env python3
"""
Test Suite for Latecomer Login, Shared NAT IP Wi-Fi, Device Fingerprinting,
and Session Lock Management.
"""

import os
import sys
import unittest
import uuid
from typing import Dict, Any

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

import database
import main
from fastapi.testclient import TestClient


class LatecomerAndSessionLockTestSuite(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        database.init_system_db()
        cls.client = TestClient(main.app)

        cls.ip_shared = "10.5.71.62"
        cls.nim1 = "TEST_H071231052"
        cls.name1 = "Siswa Pertama"
        cls.dev1 = "dev-laptop-uuid-1111"

        cls.nim2 = "TEST_H071231071"
        cls.name2 = "Siswa Terlambat"
        cls.dev2 = "dev-laptop-uuid-2222"

        cls.clean_test_users()

        conn = database.get_sqlite_conn()
        with conn:
            conn.execute("INSERT OR IGNORE INTO exam_packages (code, name, is_active) VALUES ('TEST-LOCK', 'Test Lock Package', 1);")
            conn.execute("""
                INSERT OR IGNORE INTO questions (question_number, title, description, score_weight, expected_query, category, target_database)
                VALUES (1, 'Test Lock Q1', 'Test Question', 15, 'SELECT 1;', 'TEST-LOCK', 'pagila');
            """)
        conn.close()

    @classmethod
    def tearDownClass(cls):
        cls.clean_test_users()

        conn = database.get_sqlite_conn()
        with conn:
            conn.execute("DELETE FROM questions WHERE category = 'TEST-LOCK';")
            conn.execute("DELETE FROM exam_packages WHERE code = 'TEST-LOCK';")
        conn.close()

    @classmethod
    def clean_test_users(cls):
        conn = database.get_sqlite_conn()
        with conn:
            for n in ["TEST_H071231052", "TEST_H071231071", "TEST_H071231099"]:
                user = conn.execute("SELECT id FROM users WHERE nim = ?;", (n,)).fetchone()
                if user:
                    uid = user["id"]
                    conn.execute("DELETE FROM submissions WHERE user_id = ?;", (uid,))
                    conn.execute("DELETE FROM exam_logs WHERE user_id = ?;", (uid,))
                    conn.execute("DELETE FROM users WHERE id = ?;", (uid,))
        conn.close()

    def setUp(self):
        self.clean_test_users()

    def tearDown(self):
        self.clean_test_users()

    def test_01_shared_nat_ip_two_students_both_online(self):
        """Dua mahasiswa berbeda pada IP Wi-Fi yang sama (10.5.71.62) dengan perangkat berbeda dapat login bersamaan."""
        token1 = str(uuid.uuid4())
        ok1, user1, err1 = database.login_student(self.nim1, self.name1, self.ip_shared, token1, self.dev1)
        self.assertTrue(ok1, f"Student 1 login failed: {err1}")
        self.assertEqual(user1["status"], "online")
        self.assertEqual(user1["device_id"], self.dev1)

        token2 = str(uuid.uuid4())
        ok2, user2, err2 = database.login_student(self.nim2, self.name2, self.ip_shared, token2, self.dev2)
        self.assertTrue(ok2, f"Student 2 (latecomer on same IP) should succeed: {err2}")
        self.assertEqual(user2["status"], "online")
        self.assertEqual(user2["device_id"], self.dev2)

    def test_02_same_device_multi_account_collision_blocked(self):
        """Satu perangkat/browser tidak boleh digunakan login untuk dua akun mahasiswa berbeda sekaligus."""
        token1 = str(uuid.uuid4())
        ok1, user1, _ = database.login_student(self.nim1, self.name1, self.ip_shared, token1, self.dev1)
        self.assertTrue(ok1)

        token2 = str(uuid.uuid4())
        # Student 2 tries to use Student 1's device_id
        ok2, user2, err2 = database.login_student(self.nim2, self.name2, self.ip_shared, token2, self.dev1)
        self.assertFalse(ok2, "Student 2 on same device should be blocked")
        self.assertIn("Perangkat/Browser ini masih memiliki sesi aktif", err2)

    def test_03_same_student_can_resume_session_from_same_device(self):
        """Mahasiswa yang sama dapat melanjutkan sesi dari browser/perangkat yang sama jika refresh atau reconnect."""
        token1 = str(uuid.uuid4())
        ok1, _, _ = database.login_student(self.nim1, self.name1, self.ip_shared, token1, self.dev1)
        self.assertTrue(ok1)

        # Re-login from same device with fresh token
        token1_b = str(uuid.uuid4())
        ok1_b, user1_b, err1_b = database.login_student(self.nim1, self.name1, self.ip_shared, token1_b, self.dev1)
        self.assertTrue(ok1_b, f"Resume session failed: {err1_b}")
        self.assertEqual(user1_b["session_token"], token1_b)
        self.assertEqual(user1_b["status"], "online")

    def test_04_duplicate_nim_from_other_device_blocked_while_active(self):
        """NIM yang sedang aktif tidak boleh diambil alih oleh perangkat lain tanpa izin pengawas."""
        token1 = str(uuid.uuid4())
        ok1, _, _ = database.login_student(self.nim1, self.name1, self.ip_shared, token1, self.dev1)
        self.assertTrue(ok1)

        # Other device tries to log in with same NIM
        token_imposter = str(uuid.uuid4())
        ok_imp, _, err_imp = database.login_student(self.nim1, self.name1, "10.5.71.99", token_imposter, self.dev2)
        self.assertFalse(ok_imp)
        self.assertIn("sedang aktif digunakan dalam sesi ujian pada perangkat lain", err_imp)

    def test_05_clear_session_lock_allows_new_device_and_preserves_submissions(self):
        """Admin membebaskan kunci sesi: mahasiswa dapat login dari perangkat baru dan seluruh jawaban tersimpan aman."""
        token1 = str(uuid.uuid4())
        ok1, user1, _ = database.login_student(self.nim1, self.name1, self.ip_shared, token1, self.dev1)
        self.assertTrue(ok1)

        # Simulate a mock submission
        conn = database.get_sqlite_conn()
        with conn:
            q_row = conn.execute("SELECT id FROM questions WHERE category = 'TEST-LOCK' LIMIT 1;").fetchone()
            q_id = q_row["id"]
            conn.execute(
                """
                INSERT INTO submissions (user_id, question_id, submitted_query, is_correct, score, feedback)
                VALUES (?, ?, 'SELECT 1;', 1, 15, 'Sempurna');
                """,
                (user1["id"], q_id)
            )
        conn.close()

        # Admin clears session lock
        unlocked, msg = database.clear_student_session_lock(self.nim1)
        self.assertTrue(unlocked, f"Clear session lock failed: {msg}")

        # Verify status is offline and device_id is cleared
        conn = database.get_sqlite_conn()
        user_after = dict(conn.execute("SELECT * FROM users WHERE nim = ?;", (self.nim1,)).fetchone())
        sub_count = conn.execute("SELECT COUNT(*) FROM submissions WHERE user_id = ?;", (user1["id"],)).fetchone()[0]
        conn.close()

        self.assertEqual(user_after["status"], "offline")
        self.assertIsNone(user_after["device_id"])
        self.assertEqual(sub_count, 1, "Submissions must be preserved when session lock is cleared!")

        # Student logs in from new device
        new_dev = "dev-new-borrowed-laptop-3333"
        token_new = str(uuid.uuid4())
        ok_new, user_new, err_new = database.login_student(self.nim1, self.name1, "10.5.71.88", token_new, new_dev)
        self.assertTrue(ok_new, f"Re-login after lock clear failed: {err_new}")
        self.assertEqual(user_new["status"], "online")
        self.assertEqual(user_new["device_id"], new_dev)

    def test_06_http_login_latecomer_redirects_directly_to_exam_when_exam_active(self):
        """Ketika ujian berstatus 'active', login mahasiswa langsung mengarah ke /exam, bukan /waiting."""
        # Set exam state to active
        database.set_exam_state("active")

        res = self.client.post("/login", data={
            "nim": self.nim2,
            "name": self.name2,
            "device_id": self.dev2
        }, follow_redirects=False)

        self.assertEqual(res.status_code, 302)
        self.assertEqual(res.headers.get("location"), "/exam")

        # Now test when exam is in waiting room
        database.set_exam_state("waiting")
        res_waiting = self.client.post("/login", data={
            "nim": "TEST_H071231099",
            "name": "Siswa Antrian",
            "device_id": "dev-waiting-4444"
        }, follow_redirects=False)

        self.assertEqual(res_waiting.status_code, 302)
        self.assertEqual(res_waiting.headers.get("location"), "/waiting")

    def test_07_api_admin_clear_session_lock_endpoint(self):
        """Endpoint /api/admin/clear-session-lock bekerja dengan otorisasi admin."""
        token1 = str(uuid.uuid4())
        database.login_student(self.nim1, self.name1, self.ip_shared, token1, self.dev1)

        # Set admin cookie
        self.client.cookies.set("admin_session", main.CURRENT_ADMIN_SESSION_TOKEN)

        res = self.client.post("/api/admin/clear-session-lock", json={"nim": self.nim1})
        self.assertEqual(res.status_code, 200)
        data = res.json()
        self.assertEqual(data["status"], "success")

        # Verify user is offline
        conn = database.get_sqlite_conn()
        user = dict(conn.execute("SELECT * FROM users WHERE nim = ?;", (self.nim1,)).fetchone())
        conn.close()
        self.assertEqual(user["status"], "offline")


if __name__ == "__main__":
    unittest.main()
