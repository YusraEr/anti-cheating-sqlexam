from contextlib import closing
"""Exam admission, session revocation, and submission cutoff tests without PostgreSQL."""

from concurrent.futures import ThreadPoolExecutor
import ipaddress
import os
from pathlib import Path
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from admin_test_support import authenticate_admin
import database
import main
from fastapi.testclient import TestClient
from starlette.requests import Request


class ExamAccessTests(unittest.TestCase):
    def test_reset_package_scopes_live_score_and_zip_but_preserves_archive(self):
        import csv, io, zipfile

        database.set_exam_state('finished')
        second_question = database.save_question(1, 'Package B', 'Fixture', 20, 'SELECT 2', category='B')
        database.set_exam_state('active')
        database.save_submission(self.user['id'], self.qid, 'SELECT 1', 1, 15, 'correct', 'Saved')
        self.assertEqual(self.client.post('/api/admin/student/access01/reset-category').status_code, 200)

        def export_summary():
            with zipfile.ZipFile(io.BytesIO(database.export_submissions_zip_bytes())) as archive:
                summary = next(csv.DictReader(io.StringIO(archive.read('rekap_nilai_ujian.csv').decode('utf-8-sig'))))
                attempts = list(csv.DictReader(io.StringIO(archive.read('arsip_seluruh_percobaan.csv').decode('utf-8-sig'))))
                return summary, attempts

        row = database.get_all_students_progress()[0]
        self.assertEqual((row['total_score'], row['max_score'], row['total_submitted']), (0, 0, 0))
        summary, _ = export_summary()
        self.assertEqual((summary['Paket Soal'], summary['Skor Maksimal']), ('Belum memilih paket', '0'))

        database.lock_student_category('ACCESS01', 'B')
        database.save_submission(self.user['id'], second_question, 'SELECT 2', 1, 20, 'correct', 'Saved')
        row = database.get_all_students_progress()[0]
        self.assertEqual((row['total_score'], row['max_score'], row['total_submitted']), (20, 20, 1))
        summary, attempts = export_summary()
        self.assertEqual((summary['Paket Soal'], summary['Total Skor'], summary['Skor Maksimal']), ('B', '20', '20'))
        self.assertEqual({r['Query Jawaban'] for r in attempts if r['Jenis Rekaman'] == 'PERCOBAAN'}, {'SELECT 1', 'SELECT 2'})

    def test_pending_first_answer_is_in_csv_and_zip(self):
        import csv, io, zipfile
        review={**self.grade,'graded':False,'is_correct':None,'score':None,'eval_status':'manual_review'}
        with patch.object(database,'evaluate_sql_submission',return_value=review):
            self.assertEqual(self.client.post('/api/student/submit',json=self.payload).status_code,200)
        rows=list(csv.DictReader(io.StringIO(database.export_all_submissions_csv())))
        pending=[r for r in rows if r['Jenis Rekaman']=='PERCOBAAN']
        self.assertEqual(len(pending),1)
        self.assertEqual(pending[0]['Status Evaluasi'],'BELUM DINILAI')
        self.assertEqual(pending[0]['Nilai Diperoleh'],'')
        self.assertEqual(pending[0]['Query Jawaban'],self.payload['query'])
        with zipfile.ZipFile(io.BytesIO(database.export_submissions_zip_bytes())) as archive:
            self.assertIn('arsip_seluruh_percobaan.csv',archive.namelist())
            sql='\n'.join(archive.read(name).decode() for name in archive.namelist() if name.endswith('.sql'))
            self.assertIn('BELUM DINILAI',sql)
            self.assertIn(self.payload['query'],sql)

    def test_fairness_review_is_stored_without_automatic_zero(self):
        review={**self.grade,'graded':False,'is_correct':None,'score':None,'eval_status':'manual_review'}
        with patch.object(database,'evaluate_sql_submission',return_value=review):
            r=self.client.post('/api/student/submit',json=self.payload)
        self.assertEqual(r.status_code,200,r.text)
        data=r.json()
        self.assertFalse(data['graded'])
        self.assertIsNone(data['score'])
        self.assertIsNone(data['latest_attempt']['score'])
        self.assertEqual(data['queue_status'],'review')
        self.assertIsNone(data['best_submission'])
    def test_phase4_admin_attempt_history_requires_admin(self):
        with patch.object(database, 'evaluate_sql_submission', return_value=self.grade):
            self.client.post('/api/student/submit', json=self.payload)
        result = self.client.get('/api/admin/students/ACCESS01/attempts')
        self.assertEqual(result.status_code, 200)
        self.assertEqual(len(result.json()['attempts']), 1)
        self.client.cookies.delete('admin_session')
        self.assertEqual(self.client.get('/api/admin/students/ACCESS01/attempts').status_code, 403)

    def test_phase4_question_policy_edit_preserves_history_invalidates_score(self):
        with patch.object(database, 'evaluate_sql_submission', return_value=self.grade):
            self.client.post('/api/student/submit', json=self.payload)
        q = database.get_question_by_id(self.qid)
        kwargs = dict(question_number=q['question_number'], title=q['title'], description=q['description'],
                      score_weight=q['score_weight'], expected_query=q['expected_query'], question_id=q['id'],
                      category=q['category'], target_database=q['target_database'])
        database.set_exam_state("finished")
        database.save_question(**kwargs, grading_policy={'row_order':'ignore'}, setup_query='SELECT 1')
        changed = database.get_question_by_id(self.qid)
        self.assertEqual(changed['question_revision'], q['question_revision']+1)
        best = database.get_student_submissions(self.user['id'])[self.qid]
        self.assertEqual(best['eval_status'], 'needs_regrade')
        self.assertEqual(best['score'], 0)
        self.assertEqual(best['latest_attempt']['score'], 10)
        database.save_question(**kwargs)
        unchanged = database.get_question_by_id(self.qid)
        self.assertEqual(unchanged['grading_policy'], changed['grading_policy'])
        self.assertEqual(unchanged['setup_query'], 'SELECT 1')
        self.assertEqual(unchanged['question_revision'], changed['question_revision'])

    def test_phase4_attempt_history_and_best_are_distinct(self):
        with patch.object(database, 'evaluate_sql_submission', return_value=self.grade):
            first = self.client.post('/api/student/submit', json=self.payload).json()
        failed = {**self.grade, 'is_correct': False, 'score': 0, 'eval_status': 'wrong_data'}
        with patch.object(database, 'evaluate_sql_submission', return_value=failed):
            second = self.client.post('/api/student/submit', json={**self.payload, 'query': 'SELECT 2'}).json()
        self.assertEqual(second['best_submission']['submitted_query'], 'SELECT 1')
        self.assertEqual(second['latest_attempt']['submitted_query'], 'SELECT 2')
        self.assertGreater(second['attempt_id'], first['attempt_id'])
        self.assertEqual(second['score'], 10)
        stored = database.get_student_submissions(self.user['id'])[self.qid]
        self.assertEqual(stored['submitted_query'], 'SELECT 1')
        self.assertEqual(stored['latest_attempt']['submitted_query'], 'SELECT 2')

    def test_phase4_pending_preserves_best_and_null_attempt_score(self):
        with patch.object(database, 'evaluate_sql_submission', return_value=self.grade):
            self.client.post('/api/student/submit', json=self.payload)
        pending = {**self.grade, 'graded': False, 'score': None, 'is_correct': None, 'eval_status': 'evaluation_limit'}
        with patch.object(database, 'evaluate_sql_submission', return_value=pending):
            result = self.client.post('/api/student/submit', json={**self.payload, 'query':'SELECT 3'}).json()
        self.assertFalse(result['graded'])
        self.assertEqual(result['score'], 10)
        self.assertIsNone(result['latest_attempt']['score'])
        self.assertIsNone(result['latest_attempt']['is_correct'])

    def test_phase4_pending_without_best_does_not_create_zero_grade(self):
        pending = {**self.grade, 'graded': False, 'score': None, 'is_correct': None, 'eval_status': 'reference_error'}
        with patch.object(database, 'evaluate_sql_submission', return_value=pending):
            result = self.client.post('/api/student/submit', json=self.payload).json()
        self.assertIsNone(result['score'])
        self.assertIsNone(result['best_submission'])
        self.assertEqual(self.submission_count(), 0)
        self.assertIsNone(database.get_student_submissions(self.user['id'])[self.qid]['latest_attempt']['score'])

    def test_phase4_question_revision_during_grading_blocks_save(self):
        def changed(**kwargs):
            with closing(database.get_sqlite_conn()) as conn, conn:
                conn.execute('UPDATE questions SET question_revision=question_revision+1 WHERE id=?', (self.qid,))
            return self.grade
        with patch.object(database, 'evaluate_sql_submission', side_effect=changed):
            result = self.client.post('/api/student/submit', json=self.payload)
        self.assertEqual(result.status_code, 403)
        self.assertEqual(result.json()['code'], 'question_changed')
        self.assertEqual(self.submission_count(), 0)

    def test_phase4_start_rejects_invalid_reference_and_keeps_waiting(self):
        database.set_exam_state('waiting')
        with patch('grading_engine.validate_reference', side_effect=ValueError('broken')):
            result = self.client.post('/api/admin/exam/validate')
        self.assertEqual(result.status_code, 400)
        self.assertIn('gagal divalidasi. broken', result.json()['detail'])
        self.assertIn(f'(ID {self.qid})', result.json()['detail'])
        self.assertEqual(database.get_exam_state(), 'waiting')

    def test_start_rejects_mixed_select_and_drop_with_actionable_reason(self):
        database.set_exam_state('waiting')
        with closing(database.get_sqlite_conn()) as conn, conn:
            conn.execute("UPDATE questions SET expected_query=?,target_database=? WHERE id=?",
                         ('SELECT 1; DROP TABLE contoh;', 'sandbox_pribadi', self.qid))
        result = self.client.post('/api/admin/exam/validate')
        self.assertEqual(result.status_code, 400)
        self.assertIn(f'(ID {self.qid})', result.json()['detail'])
        self.assertIn('Pisahkan acuan perubahan state dan SELECT.', result.json()['detail'])
        self.assertEqual(database.get_exam_state(), 'waiting')

    def test_phase4_start_validates_every_active_question(self):
        database.set_exam_state('waiting')
        with patch('grading_engine.validate_reference', return_value={'status':'valid'}) as validate:
            result = self.client.post('/api/admin/exam/validate')
        self.assertEqual(result.status_code, 200)
        self.assertGreater(validate.call_count, 0)
        self.assertEqual(database.get_exam_state(), 'waiting')
        with patch('grading_engine.validate_reference', side_effect=AssertionError('Start must not run SQL')):
            self.assertEqual(self.client.post('/api/admin/exam/start').status_code, 200)
        self.assertEqual(database.get_exam_state(), 'active')

    def test_phase4_start_detects_configuration_change_during_validation(self):
        database.set_exam_state('waiting')
        def validate(*args):
            with closing(database.get_sqlite_conn()) as conn, conn:
                conn.execute('UPDATE questions SET question_revision=question_revision+1 WHERE id=?', (self.qid,))
            return {'status':'valid'}
        with patch('grading_engine.validate_reference', side_effect=validate):
            result = self.client.post('/api/admin/exam/validate')
        self.assertEqual(result.status_code, 400)
        self.assertEqual(database.get_exam_state(), 'waiting')

    def setUp(self):
        directory = tempfile.TemporaryDirectory(prefix="sqlexam_access_")
        self.addCleanup(directory.cleanup)
        env = patch.dict(os.environ, {"EXAM_SQLITE_PATH": str(Path(directory.name) / "exam.db"),
                                      "ADMIN_MASTER_KEY": "test-admin-only"})
        env.start()
        self.addCleanup(env.stop)
        shared = patch.object(database, "ALLOW_SHARED_IP", True)
        shared.start()
        self.addCleanup(shared.stop)
        limiter = patch.object(main, "rate_limiter", main.InMemoryRateLimiter())
        limiter.start()
        self.addCleanup(limiter.stop)
        postgres = patch.object(database.psycopg2, "connect", side_effect=AssertionError("Unit tests must not connect to PostgreSQL"))
        postgres.start()
        self.addCleanup(postgres.stop)
        database.init_system_db()
        database.set_exam_state("active")
        self.client = TestClient(main.app)
        self.addCleanup(self.client.close)
        authenticate_admin(self.client)
        ok, self.user, _ = database.login_student("ACCESS01", "Access Student", "testclient", "token-a", "device-a")
        self.assertTrue(ok)
        database.lock_student_category("ACCESS01", "A")
        self.client.cookies.set("student_nim", "ACCESS01")
        self.client.cookies.set("student_token", "token-a")
        self.qid = database.get_questions_by_category("A")[0]["id"]
        self.payload = {"nim": "ACCESS01", "session_token": "token-a", "query": "SELECT 1",
                        "question_id": self.qid, "active_database": "pagila"}
        self.grade = {"is_correct": True, "score": 10, "eval_status": "correct", "feedback": "Saved", "student_result": {}}

    def call(self, method, url, payload=None):
        return self.client.request(method, url, json=payload) if payload is not None else self.client.request(method, url)

    def exam_endpoints(self):
        return [
            ("GET", "/api/student/questions", None),
            ("POST", "/api/student/select-category", {**self.payload, "category": "A"}),
            ("POST", "/api/student/run-query", self.payload),
            ("POST", "/api/student/submit", self.payload),
            ("GET", "/api/student/databases", None),
            ("GET", "/api/student/schema", None),
            ("GET", "/api/student/schema-full", None),
        ]

    def submission_count(self):
        conn = database.get_sqlite_conn()
        try:
            return conn.execute("SELECT COUNT(*) FROM submissions").fetchone()[0]
        finally:
            conn.close()

    def test_waiting_and_finished_block_all_exam_apis(self):
        with patch.object(database, "execute_student_query") as execute, patch.object(database, "evaluate_sql_submission") as grade:
            for state in ("waiting", "finished"):
                database.set_exam_state(state)
                for method, url, payload in self.exam_endpoints():
                    with self.subTest(state=state, url=url):
                        response = self.call(method, url, payload)
                        self.assertEqual(response.status_code, 403, response.text)
                        self.assertEqual(response.json()["exam_state"], state)
                self.assertEqual(self.client.get("/exam", follow_redirects=False).headers["location"], "/waiting")
            execute.assert_not_called()
            grade.assert_not_called()
        self.assertEqual(self.submission_count(), 0)

    def test_active_exam_can_read_execute_and_submit(self):
        self.assertEqual(self.client.get("/api/student/questions").status_code, 200)
        self.assertEqual(self.client.get("/api/student/databases").status_code, 200)
        with patch.object(database, "execute_student_query", return_value={"success": True}):
            self.assertEqual(self.client.post("/api/student/run-query", json=self.payload).status_code, 200)
        with patch.object(database, "evaluate_sql_submission", return_value=self.grade):
            self.assertEqual(self.client.post("/api/student/submit", json=self.payload).status_code, 200)
        self.assertEqual(self.submission_count(), 1)

    def test_missing_auth_is_rejected_on_exam_metadata(self):
        self.client.cookies.delete("student_token")
        for url in ("/api/student/questions", "/api/student/databases", "/api/student/schema", "/api/student/schema-full", "/api/exam/state", "/api/waiting/participants"):
            self.assertEqual(self.client.get(url).status_code, 401, url)

    def test_exam_responses_are_not_cached(self):
        for state in ("active", "finished"):
            database.set_exam_state(state)
            response = self.client.get("/api/student/questions")
            self.assertIn("no-store", response.headers["cache-control"])

    def test_package_menu_excludes_inactive_and_empty_packages(self):
        database.reset_student_category("ACCESS01")
        database.create_package("EMPTY", "No questions")
        response = self.client.get("/api/student/questions")
        self.assertEqual(response.status_code, 200)
        self.assertEqual([p["code"] for p in response.json()["categories"]], ["A"])
        database.set_exam_state("waiting")
        database.update_package("A", is_active=0)
        database.set_exam_state("active")
        self.assertEqual(self.client.get("/api/student/questions").json()["categories"], [])

    def test_unselected_or_inactive_package_cannot_execute(self):
        database.reset_student_category("ACCESS01")
        self.assertEqual(self.client.post("/api/student/run-query", json=self.payload).status_code, 403)
        database.lock_student_category("ACCESS01", "A")
        database.set_exam_state("waiting")
        database.update_package("A", is_active=0)
        database.set_exam_state("active")
        self.assertEqual(self.client.get("/api/student/questions").status_code, 403)

    def test_package_selection_rejects_missing_inactive_and_empty_packages(self):
        database.reset_student_category("ACCESS01")
        database.create_package("EMPTY", "No questions")
        database.set_exam_state("waiting")
        database.update_package("B", is_active=0)
        database.set_exam_state("active")
        for code in ("MISSING", "EMPTY", "B"):
            response = self.client.post("/api/student/select-category", json={**self.payload, "category": code})
            self.assertEqual(response.status_code, 400)

    def test_question_from_other_package_is_rejected(self):
        conn = database.get_sqlite_conn()
        with conn:
            other = conn.execute("INSERT INTO questions (category,question_number,title,description) VALUES ('B',99,'Other package','Other package')").lastrowid
        conn.close()
        with patch.object(database, "evaluate_sql_submission") as grade:
            self.assertEqual(self.client.post("/api/student/submit", json={**self.payload, "question_id": other}).status_code, 400)
            grade.assert_not_called()

    def test_metadata_cannot_select_another_students_nim(self):
        self.assertEqual(self.client.get("/api/student/databases?nim=OTHER").status_code, 403)

    def test_stop_while_grading_blocks_final_save(self):
        entered, release = threading.Event(), threading.Event()
        def delayed_grade(**kwargs):
            entered.set()
            if not release.wait(10):
                raise RuntimeError("Test did not release grader")
            return self.grade
        with patch.object(database, "evaluate_sql_submission", side_effect=delayed_grade):
            with ThreadPoolExecutor(max_workers=1) as pool:
                pending = pool.submit(self.client.post, "/api/student/submit", json=self.payload)
                try:
                    self.assertTrue(entered.wait(5))
                    stopped = self.client.post("/api/admin/exam/stop")
                    self.assertEqual(stopped.status_code, 200)
                    self.assertEqual(database.get_exam_state(), "finished")
                finally:
                    release.set()
                self.assertEqual(pending.result(timeout=10).status_code, 403)
        self.assertEqual(self.submission_count(), 0)

    def test_stop_then_start_during_grading_invalidates_old_request(self):
        def restarted_grade(**kwargs):
            database.set_exam_state("finished")
            database.set_exam_state("active")
            return self.grade
        with patch.object(database, "evaluate_sql_submission", side_effect=restarted_grade):
            response = self.client.post("/api/student/submit", json=self.payload)
        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.json()["code"], "exam_changed")
        self.assertEqual(self.submission_count(), 0)

    def test_revoked_session_during_grading_cannot_save(self):
        def revoked_grade(**kwargs):
            database.kick_student("ACCESS01")
            return self.grade
        with patch.object(database, "evaluate_sql_submission", side_effect=revoked_grade):
            self.assertEqual(self.client.post("/api/student/submit", json=self.payload).status_code, 401)
        self.assertEqual(self.submission_count(), 0)

    def test_package_reset_during_grading_cannot_save(self):
        def reset_grade(**kwargs):
            database.reset_student_category("ACCESS01")
            return self.grade
        with patch.object(database, "evaluate_sql_submission", side_effect=reset_grade):
            self.assertEqual(self.client.post("/api/student/submit", json=self.payload).status_code, 403)
        self.assertEqual(self.submission_count(), 0)

    def test_duplicate_start_preserves_revision(self):
        before = self.client.get("/api/exam/state").json()
        self.assertEqual(self.client.post("/api/admin/exam/start").status_code, 200)
        self.assertEqual(self.client.get("/api/exam/state").json(), before)

    def test_admin_controls_reject_student_only_session(self):
        self.client.cookies.delete("admin_session")
        for url in ("/api/admin/exam/validate", "/api/admin/exam/start", "/api/admin/exam/stop", "/api/admin/stop-all-sessions"):
            self.assertEqual(self.client.post(url).status_code, 403)
        self.assertEqual(database.get_exam_state(), "active")

    def test_invalid_state_is_rejected(self):
        with self.assertRaises(ValueError):
            database.set_exam_state("unknown")
        self.assertEqual(database.get_exam_state(), "active")

    def test_heartbeat_reports_finished_without_reopening_exam(self):
        database.set_exam_state("finished")
        result = self.client.post("/api/student/heartbeat", json=self.payload).json()
        self.assertTrue(result["valid"])
        self.assertEqual(result["exam_state"], "finished")
        self.assertEqual(self.client.get("/waiting").status_code, 200)
        self.assertIn("Ujian Selesai", self.client.get("/waiting").text)

    def test_stale_heartbeat_cannot_resurrect_revoked_session(self):
        original = main.require_student
        def revoke_after_read(*args, **kwargs):
            user = original(*args, **kwargs)
            database.kick_student(user["nim"])
            return user
        with patch.object(main, "require_student", side_effect=revoke_after_read):
            response = self.client.post("/api/student/heartbeat", json=self.payload)
        self.assertFalse(response.json()["valid"])
        self.assertFalse(database.validate_student_session("ACCESS01", "token-a", "testclient")[0])

    def test_stale_waiting_heartbeat_cannot_resurrect_cleared_session(self):
        database.set_exam_state("waiting")
        original = main.require_student
        def clear_after_read(*args, **kwargs):
            user = original(*args, **kwargs)
            database.clear_student_session_lock(user["nim"])
            return user
        with patch.object(main, "require_student", side_effect=clear_after_read):
            self.assertEqual(self.client.post("/api/waiting/heartbeat").status_code, 401)

    def test_waiting_device_cannot_login_another_account(self):
        database.set_exam_state("waiting")
        self.assertEqual(self.client.get("/waiting").status_code, 200)
        self.assertFalse(database.login_student("ACCESS02", "Other Student", "testclient", "other-token", "device-a")[0])

    def test_same_ip_or_copied_device_id_without_token_cannot_take_over(self):
        for device in ("device-b", "device-a", None):
            self.assertFalse(database.login_student("ACCESS01", "Access Student", "testclient", "stolen", device)[0])
        self.assertTrue(database.validate_student_session("ACCESS01", "token-a", "testclient")[0])

    def test_session_lock_survives_heartbeat_timeout(self):
        conn = database.get_sqlite_conn()
        with conn:
            conn.execute("UPDATE users SET last_active=datetime('now','-120 seconds')")
        conn.close()
        self.assertFalse(database.login_student("ACCESS01", "Access Student", "testclient", "stolen", "device-b")[0])

    def test_owner_can_resume_after_ip_change_and_old_token_is_revoked(self):
        self.assertFalse(database.validate_student_session("ACCESS01", "token-a", "10.0.0.2")[0])
        ok, _, _ = database.login_student("ACCESS01", "Access Student", "10.0.0.2", "token-b", "device-a", resume_token="token-a")
        self.assertTrue(ok)
        self.assertFalse(database.validate_student_session("ACCESS01", "token-a", "testclient")[0])
        self.assertTrue(database.validate_student_session("ACCESS01", "token-b", "10.0.0.2")[0])

    def test_different_students_can_share_ip(self):
        self.assertTrue(database.login_student("ACCESS02", "Other Student", "testclient", "token-b", "device-b")[0])

    def test_kick_requires_unlock_and_preserves_answers(self):
        database.save_submission(self.user["id"], self.qid, "SELECT 1", 1, 10)
        self.assertEqual(self.client.post("/api/admin/kick-student", json={"nim": "ACCESS01"}).status_code, 200)
        self.assertFalse(database.login_student("ACCESS01", "Access Student", "testclient", "token-b", "device-a", resume_token="token-a")[0])
        self.assertEqual(self.client.post("/api/admin/clear-session-lock", json={"nim": "ACCESS01"}).status_code, 200)
        self.assertTrue(database.login_student("ACCESS01", "Access Student", "testclient", "token-b", "device-b")[0])
        self.assertEqual(self.submission_count(), 1)

    def test_stop_all_revokes_waiting_and_online_sessions(self):
        database.login_student("ACCESS02", "Other Student", "testclient", "token-b", "device-b")
        database.set_exam_state("waiting")
        database.mark_student_waiting("ACCESS02", "token-b", "testclient")
        self.assertEqual(database.stop_all_active_sessions(), 2)
        self.assertFalse(self.client.post("/api/student/heartbeat", json=self.payload).json()["valid"])
        self.assertFalse(database.login_student("ACCESS02", "Other Student", "testclient", "new", "device-b", resume_token="token-b")[0])

    def test_old_logout_does_not_revoke_new_session(self):
        database.login_student("ACCESS01", "Access Student", "testclient", "token-b", "device-a", resume_token="token-a")
        database.logout_student("ACCESS01", "token-a")
        self.assertTrue(database.validate_student_session("ACCESS01", "token-b", "testclient")[0])

    def test_late_submit_preserves_previously_saved_score(self):
        database.save_submission(self.user["id"], self.qid, "SELECT 5", 1, 5)
        def close_exam(**kwargs):
            database.set_exam_state("finished")
            return self.grade
        with patch.object(database, "evaluate_sql_submission", side_effect=close_exam):
            self.assertEqual(self.client.post("/api/student/submit", json=self.payload).status_code, 403)
        answer = database.get_student_submissions(self.user["id"])[self.qid]
        self.assertEqual((answer["score"], answer["submitted_query"]), (5, "SELECT 5"))

    def test_http_resume_requires_existing_cookie_and_rotates_token(self):
        form = {"nim": "ACCESS01", "name": "Access Student", "device_id": "device-a"}
        other = TestClient(main.app)
        self.addCleanup(other.close)
        refused = other.post("/login", data=form, follow_redirects=False)
        self.assertEqual(refused.status_code, 200)
        self.assertNotIn("set-cookie", refused.headers)
        resumed = self.client.post("/login", data=form, follow_redirects=False)
        self.assertEqual(resumed.status_code, 302)
        self.assertFalse(database.validate_student_session("ACCESS01", "token-a", "testclient")[0])
        self.assertTrue(database.validate_student_session("ACCESS01", resumed.cookies.get("student_token"), "testclient")[0])

    def test_concurrent_first_login_to_same_nim_has_one_owner(self):
        def login(index):
            return database.login_student("RACE01", "Race Student", "testclient", f"token-{index}", f"device-{index}")[0]
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(login, range(2)))
        self.assertEqual(sum(results), 1)

    def test_clear_class_returns_control_to_preparation_and_revokes_tokens(self):
        self.assertEqual(self.client.post("/api/admin/clear-sessions", json={"clear_logs": False}).status_code, 200)
        self.assertEqual(database.get_exam_state(), "waiting")
        self.assertFalse(database.validate_student_session("ACCESS01", "token-a", "testclient")[0])

    def test_forwarded_header_is_ignored_without_trusted_proxy(self):
        request = Request({"type": "http", "headers": [(b"x-forwarded-for", b"1.2.3.4")], "client": ("10.0.0.2", 42)})
        with patch.object(main, "TRUSTED_PROXY_NETWORKS", ()):
            self.assertEqual(main.get_client_ip(request), "10.0.0.2")

    def test_trusted_proxy_selects_nearest_untrusted_address(self):
        request = Request({"type": "http", "headers": [(b"x-forwarded-for", b"1.2.3.4, 192.168.1.9, 10.0.0.3")], "client": ("10.0.0.2", 42)})
        with patch.object(main, "TRUSTED_PROXY_NETWORKS", (ipaddress.ip_network("10.0.0.0/24"),)):
            self.assertEqual(main.get_client_ip(request), "192.168.1.9")

    def test_malformed_forwarded_header_falls_back_to_peer(self):
        request = Request({"type": "http", "headers": [(b"x-forwarded-for", b"not-an-ip")], "client": ("10.0.0.2", 42)})
        with patch.object(main, "TRUSTED_PROXY_NETWORKS", (ipaddress.ip_network("10.0.0.0/24"),)):
            self.assertEqual(main.get_client_ip(request), "10.0.0.2")


if __name__ == "__main__":
    unittest.main(verbosity=2)
