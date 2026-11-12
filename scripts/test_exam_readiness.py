"""Prepared exam readiness, invalidation and fast admission on disposable SQLite."""
from contextlib import closing
import unittest
from unittest.mock import patch
from fastapi.testclient import TestClient
import test_exam_access as access_tests
from admin_test_support import authenticate_admin
import database as db
import main


class ReadinessTests(unittest.TestCase):
    setUp = access_tests.ExamAccessTests.setUp

    def prepare(self):
        db.set_exam_state('waiting')
        with patch('grading_engine.validate_reference', return_value={'status': 'valid'}):
            result = self.client.post('/api/admin/exam/validate')
        self.assertEqual(result.status_code, 200, result.text)
        self.assertTrue(result.json()['readiness']['ready'])
        self.assertEqual(db.get_exam_state(), 'waiting')
        return result.json()['readiness']

    def change(self, statement, args=()):
        with closing(db.get_sqlite_conn()) as conn, conn:
            conn.execute(statement, args)

    def test_start_without_preparation_never_executes_reference(self):
        db.set_exam_state('waiting')
        with patch('grading_engine.validate_reference', side_effect=AssertionError('Unexpected evaluation')):
            result = self.client.post('/api/admin/exam/start')
        self.assertEqual(result.status_code, 400)
        self.assertIn('Validasi Kesiapan', result.json()['detail'])
        self.assertEqual(db.get_exam_state(), 'waiting')

    def test_saved_readiness_is_shared_between_admins_and_survives_new_clients(self):
        ready = self.prepare()
        second = TestClient(main.app)
        self.addCleanup(second.close)
        authenticate_admin(second)
        observed = second.get('/api/admin/waiting/participants').json()['readiness']
        self.assertEqual(observed, ready)
        with patch('grading_engine.validate_reference', side_effect=AssertionError('Start must not evaluate')):
            self.assertEqual(second.post('/api/admin/exam/start').status_code, 200)
        self.assertEqual(db.get_exam_state(), 'active')

    def test_active_exam_cannot_be_revalidated_or_disrupted(self):
        self.assertEqual(db.get_exam_state(), 'active')
        with patch('grading_engine.validate_reference') as validate:
            self.assertEqual(self.client.post('/api/admin/exam/validate').status_code, 400)
            validate.assert_not_called()
        self.assertEqual(db.get_exam_state(), 'active')

    def test_question_policy_setup_target_and_package_changes_invalidate(self):
        statements = [
            ('UPDATE questions SET question_revision=question_revision+1 WHERE id=?', (self.qid,)),
            ('UPDATE questions SET edit_revision=edit_revision+1 WHERE id=?', (self.qid,)),
            ('UPDATE questions SET expected_query=? WHERE id=?', ('SELECT 2', self.qid)),
            ('UPDATE questions SET grading_policy=? WHERE id=?', ('{"row_order":"ignore"}', self.qid)),
            ('UPDATE questions SET setup_query=? WHERE id=?', ('SELECT 1', self.qid)),
            ("""UPDATE questions SET target_database =
                CASE WHEN target_database='pagila' THEN 'classicmodels' ELSE 'pagila' END WHERE id=?""", (self.qid,)),
            ("UPDATE questions SET category='B' WHERE id=?", (self.qid,)),
            ("UPDATE exam_packages SET is_active=0 WHERE code='A'", ()),
        ]
        for statement, args in statements:
            with self.subTest(statement=statement):
                self.prepare()
                self.change(statement, args)
                self.assertFalse(db.get_exam_readiness()['ready'])
                self.assertEqual(self.client.post('/api/admin/exam/start').status_code, 400)

    def test_dataset_revision_and_maintenance_invalidate(self):
        self.prepare()
        self.change("INSERT INTO system_settings(key,value) VALUES('dataset_revision','changed') ON CONFLICT(key) DO UPDATE SET value=excluded.value")
        self.assertFalse(db.get_exam_readiness()['ready'])
        self.assertEqual(self.client.post('/api/admin/exam/start').status_code, 400)
        self.prepare()
        self.change("INSERT INTO system_settings(key,value) VALUES('grading_maintenance','busy')")
        self.assertFalse(db.get_exam_readiness()['ready'])
        self.assertEqual(self.client.post('/api/admin/exam/start').status_code, 400)

    def test_failed_revalidation_revokes_old_certificate(self):
        self.prepare()
        with patch('grading_engine.validate_reference', side_effect=ValueError('Acuan gagal')):
            self.assertEqual(self.client.post('/api/admin/exam/validate').status_code, 400)
        self.assertFalse(db.get_exam_readiness()['ready'])
        self.assertEqual(self.client.post('/api/admin/exam/start').status_code, 400)

    def test_dataset_change_during_validation_cannot_publish_readiness(self):
        db.set_exam_state('waiting')
        def validate(*args):
            self.change("INSERT INTO system_settings(key,value) VALUES('dataset_revision','new') ON CONFLICT(key) DO UPDATE SET value=excluded.value")
            return {'status': 'valid'}
        with patch('grading_engine.validate_reference', side_effect=validate):
            self.assertEqual(self.client.post('/api/admin/exam/validate').status_code, 400)
        self.assertFalse(db.get_exam_readiness()['ready'])

    def test_manual_questions_are_ready_without_automatic_grade(self):
        db.set_exam_state('waiting')
        self.change("UPDATE questions SET grading_policy=?", ('{"mode":"manual"}',))
        result = self.client.post('/api/admin/exam/validate')
        self.assertEqual(result.status_code, 200, result.text)
        self.assertTrue(result.json()['readiness']['ready'])
        self.assertEqual(self.client.post('/api/admin/exam/start').status_code, 200)

    def test_runtime_budget_change_invalidates_readiness(self):
        self.prepare()
        with patch('grading_engine.GRADE_SECONDS', 5):
            self.assertFalse(db.get_exam_readiness()['ready'])
            self.assertEqual(self.client.post('/api/admin/exam/start').status_code, 400)

    def test_empty_active_package_change_invalidates_readiness(self):
        self.prepare()
        self.change("INSERT INTO exam_packages(code,name,is_active) VALUES('EMPTY','Empty',1)")
        self.assertFalse(db.get_exam_readiness()['ready'])

    def test_database_server_change_invalidates_readiness(self):
        self.prepare()
        with patch.object(db, 'PG_PORT', 'different'):
            self.assertFalse(db.get_exam_readiness()['ready'])

    def test_no_active_questions_cannot_be_prepared_or_started(self):
        db.set_exam_state('waiting')
        self.change('UPDATE exam_packages SET is_active=0')
        self.assertEqual(self.client.post('/api/admin/exam/validate').status_code, 400)
        self.assertFalse(db.get_exam_readiness()['ready'])
        self.assertEqual(self.client.post('/api/admin/exam/start').status_code, 400)


if __name__ == '__main__':
    unittest.main(verbosity=2)
