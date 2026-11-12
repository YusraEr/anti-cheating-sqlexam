"""Shared student handlers, admin admission and isolation of practice records."""
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
import re
import unittest
from unittest.mock import patch
from fastapi.testclient import TestClient
import test_exam_access as access
from admin_test_support import authenticate_admin
import admin_practice
import admin_security
import database as db
import main


class AdminPracticeTests(unittest.TestCase):
    setUp=access.ExamAccessTests.setUp
    submission_count=access.ExamAccessTests.submission_count

    def open(self,client=None):
        client=client or self.client
        page=client.get('/admin/practice')
        self.assertEqual(page.status_code,200,page.text)
        nim=re.search(r'data-nim="([^"]+)"',page.text)[1]
        token=re.search(r'data-token="([^"]+)"',page.text)[1]
        return {'nim':nim,'session_token':token}

    def select(self,payload,client=None):
        return (client or self.client).post('/api/admin/practice/select-category',json={**payload,'category':'A'})

    def test_page_and_api_require_admin_not_student_cookie(self):
        self.client.cookies.delete('admin_session')
        self.assertEqual(self.client.get('/admin/practice',follow_redirects=False).status_code,302)
        self.assertEqual(self.client.get('/api/admin/practice/questions').status_code,403)

    def test_mutations_require_admin_csrf_and_same_origin(self):
        payload=self.open()
        self.assertEqual(self.select(payload).status_code,200)
        self.assertEqual(self.client.post('/api/admin/practice/run-query',json={**payload,'query':'SELECT 1'},headers={'X-CSRF-Token':'bad'}).status_code,403)
        self.assertEqual(self.client.post('/api/admin/practice/run-query',json={**payload,'query':'SELECT 1'},headers={'Origin':'https://evil.test'}).status_code,403)

    def test_same_template_controller_and_available_before_and_after_exam(self):
        for state in ('waiting','finished','active'):
            db.set_exam_state(state)
            page=self.client.get('/admin/practice')
            self.assertEqual(page.status_code,200)
            self.assertRegex(page.text, r'src="/static/js/student\.js(?:\?v=[^"]+)?"')
            self.assertIn('id="btn-enter-fullscreen"',page.text)
            self.assertEqual(db.get_exam_state(),state)

    def test_run_uses_student_executor_and_limits(self):
        payload=self.open()
        self.assertEqual(self.select(payload).status_code,200)
        with patch.object(db,'execute_student_query',return_value={'success':True}) as execute:
            r=self.client.post('/api/admin/practice/run-query',json={**payload,'query':'SELECT 1','active_database':'pagila'})
            self.assertEqual(r.status_code,200)
            execute.assert_called_once_with('SELECT 1',active_db='pagila',nim=payload['nim'])
            self.assertEqual(self.client.post('/api/admin/practice/run-query',json={**payload,'query':'x'*(db.MAX_STUDENT_QUERY_LENGTH+1)}).status_code,400)
            self.assertEqual(execute.call_count,1)

    def test_submit_uses_same_evaluator_without_student_scores_or_logs(self):
        payload=self.open()
        self.select(payload)
        with closing(db.get_sqlite_conn()) as conn:
            before=conn.execute('SELECT COUNT(*) FROM exam_logs').fetchone()[0]
        with patch.object(db,'evaluate_sql_submission',return_value=self.grade) as evaluate:
            r=self.client.post('/api/admin/practice/submit',json={**payload,'query':'SELECT 1','question_id':self.qid,'active_database':'pagila'})
            self.assertEqual(r.status_code,200,r.text)
            self.assertEqual(r.json()['latest_attempt']['score'],10)
            self.assertEqual(evaluate.call_args.kwargs['question_id'],self.qid)
            self.assertEqual(evaluate.call_args.kwargs['nim'],payload['nim'])
        self.assertEqual(self.submission_count(),0)
        with closing(db.get_sqlite_conn()) as conn:
            self.assertEqual(conn.execute('SELECT COUNT(*) FROM submission_attempts').fetchone()[0],0)
            self.assertEqual(conn.execute('SELECT COUNT(*) FROM exam_logs').fetchone()[0],before)
            self.assertEqual(conn.execute('SELECT COUNT(*) FROM users').fetchone()[0],1)
        result=self.client.get('/api/admin/practice/questions').json()
        self.assertEqual(result['submissions'][str(self.qid)]['score'],10)

    def test_two_admins_have_independent_packages_and_scores(self):
        first=self.open()
        other=TestClient(main.app)
        self.addCleanup(other.close)
        authenticate_admin(other)
        second=self.open(other)
        self.assertNotEqual(first['nim'],second['nim'])
        self.select(first)
        self.assertTrue(other.get('/api/admin/practice/questions').json()['needs_category_selection'])
        self.assertEqual(other.post('/api/admin/practice/select-category',json={**first,'category':'A'}).status_code,401)

    def test_context_does_not_leak_across_simultaneous_student_requests(self):
        db.set_exam_state('waiting')
        db.create_package('ISOLATED','Isolation fixture','Practice fixture',1)
        db.save_question(1,'Isolated question','SELECT 1',10,'SELECT 1',category='ISOLATED')
        db.set_exam_state('active')
        p=self.open()
        self.assertEqual(self.client.post('/api/admin/practice/select-category',json={**p,'category':'ISOLATED'}).status_code,200)
        def read(n):
            if n%2:
                return self.client.get('/api/admin/practice/questions').json()['category']
            return self.client.get('/api/student/questions').json()['category']
        with ThreadPoolExecutor(8) as pool:
            self.assertEqual(list(pool.map(read,range(24))),['A' if n%2==0 else 'ISOLATED' for n in range(24)])
        self.assertIsNone(db.SQLITE_REQUEST_PATH.get())
        self.assertEqual(self.submission_count(),0)

    def test_synthetic_identity_cannot_be_logged_in_as_student(self):
        payload=self.open()
        self.assertFalse(db.login_student(payload['nim'],'Intruder','testclient','intruder-token','intruder')[0])
        self.assertEqual(self.client.post('/api/student/run-query',json={**payload,'query':'SELECT 1'}).status_code,401)

    def test_reset_package_only_changes_practice(self):
        payload=self.open()
        self.select(payload)
        with patch.object(db,'evaluate_sql_submission',return_value=self.grade):
            self.assertEqual(self.client.post('/api/admin/practice/submit',json={**payload,'query':'SELECT 1','question_id':self.qid}).status_code,200)
        self.assertEqual(self.client.post('/api/admin/practice/reset-package').status_code,200)
        self.assertTrue(self.client.get('/api/admin/practice/questions').json()['needs_category_selection'])
        self.assertEqual(self.select(payload).json()['submissions'][str(self.qid)]['score'],10)
        self.assertEqual(self.client.get('/api/student/questions').json()['category'],'A')

    def test_dataset_fence_requires_reopen_and_maintenance_blocks_run(self):
        payload=self.open()
        self.select(payload)
        with closing(db.get_sqlite_conn()) as conn,conn:
            conn.execute("INSERT INTO system_settings(key,value) VALUES('dataset_revision','2') ON CONFLICT(key) DO UPDATE SET value=CAST(value AS INTEGER)+1")
        self.assertEqual(self.client.get('/api/admin/practice/questions').status_code,409)
        self.open()
        self.assertEqual(self.client.get('/api/admin/practice/questions').status_code,200)
        with closing(db.get_sqlite_conn()) as conn,conn:
            conn.execute("INSERT INTO system_settings(key,value) VALUES('grading_maintenance','test')")
        self.assertEqual(self.client.post('/api/admin/practice/run-query',json={**payload,'query':'SELECT 1'}).status_code,503)

    def test_revoked_admin_cannot_reuse_practice(self):
        self.open()
        admin_security.logout(self.client.cookies['admin_session'])
        self.assertEqual(self.client.get('/api/admin/practice/questions').status_code,403)

    def test_practice_telemetry_does_not_consume_student_log_limit(self):
        payload=self.open()
        self.select(payload)
        with patch.object(main.rate_limiter,'check',return_value=True) as limit:
            self.assertEqual(self.client.post('/api/admin/practice/log-event',json={**payload,'action_type':'blur_tab'}).status_code,200)
            practice_key=limit.call_args.args[0]
            self.assertEqual(self.client.post('/api/student/log-event',json={**self.payload,'action_type':'blur_tab'}).status_code,200)
            self.assertNotEqual(practice_key,limit.call_args.args[0])


if __name__=='__main__':
    unittest.main(verbosity=2)
