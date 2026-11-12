"""SQLite setup is idempotent and backs up an older store without touching PostgreSQL."""
from contextlib import closing
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import database as db
from setup_sqlite import setup


class SetupTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory(prefix='sqlexam_setup_')
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        self.path = self.root/'exam.db'
        env = patch.dict(os.environ,{'EXAM_SQLITE_PATH':str(self.path),'ADMIN_MASTER_KEY':'setup-fixture-key'})
        env.start()
        self.addCleanup(env.stop)
        postgres = patch.object(db.psycopg2,'connect',side_effect=AssertionError('Setup must not connect to PostgreSQL'))
        postgres.start()
        self.addCleanup(postgres.stop)

    def test_fresh_setup_and_repeated_setup_preserve_scores(self):
        self.assertEqual(setup(self.path)['schema_version'],db.SQLITE_SCHEMA_VERSION)
        _,user,_ = db.login_student('SETUP01','Setup Student','testclient','token')
        q = db.get_all_questions()[0]
        self.assertEqual({item['target_database'] for item in db.get_all_questions()}, {'classicmodels'})
        db.save_submission(user['id'],q['id'],'SELECT 1',1,10,'correct','Saved')
        result = setup(self.path)
        self.assertIsNone(result['backup'])
        self.assertEqual(result['attempts'],1)
        self.assertEqual(db.get_student_submissions(user['id'])[q['id']]['score'],10)

    def test_legacy_seed_target_repair_preserves_edited_questions_and_history(self):
        setup(self.path)
        with closing(db.get_sqlite_conn()) as conn, conn:
            conn.execute("UPDATE questions SET target_database='pagila'")
            conn.execute("UPDATE questions SET edit_revision=2 WHERE question_number=2")
        _, user, _ = db.login_student('SETUP02', 'Setup Student', 'testclient', 'token')
        question = db.get_all_questions()[2]
        db.save_submission(user['id'], question['id'], 'SELECT 1', 1, 10, 'correct', 'Saved')
        db.set_exam_state('active')
        db.init_system_db()
        self.assertEqual(db.get_all_questions()[0]['target_database'], 'pagila')
        db.set_exam_state('finished')
        db.init_system_db()
        db.init_system_db()
        questions = db.get_all_questions()
        self.assertEqual([q['target_database'] for q in questions[:3]], ['classicmodels', 'pagila', 'pagila'])
        self.assertEqual(questions[0]['question_revision'], 2)
        self.assertEqual(db.get_student_submissions(user['id'])[question['id']]['score'], 10)

    def test_old_version_is_backed_up_before_upgrade(self):
        setup(self.path)
        with closing(db.get_sqlite_conn()) as conn,conn:
            conn.execute('DROP TABLE grading_decisions')
            conn.execute('DROP TABLE grading_jobs')
            conn.execute('PRAGMA user_version=4')
        result = setup(self.path,self.root/'backups')
        self.assertEqual(result['schema_version'],5)
        import sqlite3
        with closing(sqlite3.connect(result['backup'])) as copy:
            self.assertEqual(copy.execute('PRAGMA user_version').fetchone()[0],4)

    def test_future_schema_is_rejected_unchanged(self):
        setup(self.path)
        with closing(db.get_sqlite_conn()) as conn,conn:
            conn.execute('PRAGMA user_version=999')
        with self.assertRaises(RuntimeError):
            setup(self.path)
        with closing(db.get_sqlite_conn()) as conn:
            self.assertEqual(conn.execute('PRAGMA user_version').fetchone()[0],999)


if __name__=='__main__':
    unittest.main(verbosity=2)
