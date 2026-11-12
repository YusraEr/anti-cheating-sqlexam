"""Regression checks for admin state, concurrency, practice fences and uploads."""
from contextlib import closing
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from concurrent.futures import ThreadPoolExecutor
import threading
import re
from io import BytesIO
from PIL import Image
import test_exam_access as access
from fastapi.testclient import TestClient
from admin_test_support import authenticate_admin
import admin_practice
import admin_security
import database as db
import main
import question_uploads
from types import SimpleNamespace


class AdminFixTests(unittest.TestCase):
    setUp=access.ExamAccessTests.setUp

    def test_sql_upload_rejected_before_parser_and_runs_off_event_loop(self):
        with patch.object(db,'reset_target_database',return_value=(True,'ok')) as reset:
            response=self.client.post('/api/admin/upload-sql',content=b'bad multipart',
                headers={'content-type':'multipart/form-data; boundary=x','content-length':str(26*1024*1024)})
            self.assertEqual(response.status_code,413)
            reset.assert_not_called()
            def chunks():
                yield b'--x\r\nContent-Disposition: form-data; name="file"; filename="big.sql"\r\n\r\n'
                for _ in range(26):
                    yield b'x' * (1024*1024)
                yield b'\r\n--x--\r\n'
            response=self.client.post('/api/admin/upload-sql',content=chunks(),
                headers={'content-type':'multipart/form-data; boundary=x'})
            self.assertEqual(response.status_code,413,response.text)
            reset.assert_not_called()
        import asyncio
        def reset_in_worker(**kwargs):
            with self.assertRaises(RuntimeError):
                asyncio.get_running_loop()
            return True,'ok'
        with patch.object(db,'reset_target_database',side_effect=reset_in_worker):
            response=self.client.post('/api/admin/upload-sql',files={'file':('fixture.sql',b'SELECT 1;')})
            self.assertEqual(response.status_code,200,response.text)
        self.client.cookies.delete('admin_session')
        response=self.client.post('/api/admin/upload-sql',content=b'bad multipart',
            headers={'content-type':'multipart/form-data; boundary=x'})
        self.assertEqual(response.status_code,403)

    def test_question_rejects_invalid_weights_and_accepts_manual_without_key(self):
        db.set_exam_state('waiting')
        payload = {'question_number': 999, 'category': 'A', 'title': 'Audit', 'description': 'Fixture',
                   'score_weight': 10, 'expected_query': 'SELECT 1'}
        for weight in (0, -5, 1.5, True):
            with self.subTest(weight=weight):
                response = self.client.post('/api/admin/questions', json={**payload, 'score_weight': weight})
                self.assertEqual(response.status_code, 422, response.text)
                self.assertEqual(response.json()['detail'][0]['loc'], ['body', 'score_weight'])
        empty = self.client.post('/api/admin/questions', json={**payload, 'expected_query': ''})
        self.assertEqual(empty.status_code, 400)
        manual = self.client.post('/api/admin/questions', json={**payload, 'expected_query': '',
                                                              'grading_policy': {'mode': 'manual'}})
        self.assertEqual(manual.status_code, 200, manual.text)

    def test_question_validation_reports_sql_field_and_preserves_data(self):
        db.set_exam_state('waiting')
        payload={'question_number':999,'category':'A','title':'Validation test','description':'Validation fixture',
            'score_weight':10,'expected_query':'ini hanya kalimat acak','target_database':'pagila'}
        with closing(db.get_sqlite_conn()) as conn:
            before=conn.execute('SELECT COUNT(*) FROM questions').fetchone()[0]
        r=self.client.post('/api/admin/questions',json=payload)
        self.assertEqual(r.status_code,400,r.text)
        self.assertIn('Kunci jawaban',r.json()['detail'])
        with closing(db.get_sqlite_conn()) as conn:
            self.assertEqual(conn.execute('SELECT COUNT(*) FROM questions').fetchone()[0],before)
        r=self.client.post('/api/admin/questions',json={**payload,'expected_query':'SELECT 1','setup_query':'ini bukan SQL'})
        self.assertEqual(r.status_code,400,r.text)
        self.assertIn('setup',r.json()['detail'])
        r=self.client.post('/api/admin/questions',json={**payload,'expected_query':'SELECT 1'})
        self.assertEqual(r.status_code,200,r.text)

    def png(self):
        output=BytesIO()
        Image.new('RGB',(2,2)).save(output,format='PNG')
        return output.getvalue()

    def test_fresh_installation_without_state_can_manage_packages(self):
        with closing(db.get_sqlite_conn()) as conn,conn:
            conn.execute("DELETE FROM system_settings WHERE key='exam_state'")
        r=self.client.post('/api/admin/packages',json={'code':'FRESH','name':'Fresh package'})
        self.assertEqual(r.status_code,200,r.text)
        package=next(p for p in self.client.get('/api/admin/questions').json()['packages'] if p['code']=='FRESH')
        self.assertEqual(self.client.put('/api/admin/packages/FRESH',json={'expected_edit_revision':package['edit_revision'],'name':'Fresh edit'}).status_code,200)

    def test_valid_image_and_failed_batch_are_atomic(self):
        with tempfile.TemporaryDirectory(prefix='upload_fix_') as directory,patch.object(main,'STATIC_DIR',directory):
            payload=self.png()
            r=self.client.post('/api/admin/questions/upload-image',files={'image':('test.png',payload,'image/png')})
            self.assertEqual(r.status_code,200,r.text)
            root=Path(directory)/'uploads/questions'
            self.assertEqual(len(list(root.iterdir())),1)
            r=self.client.post('/api/admin/questions/upload-images',files=[('images',('good.png',payload,'image/png')),('images',('bad.png',b'bad','image/png'))])
            self.assertEqual(r.status_code,422)
            self.assertEqual(len(list(root.iterdir())),1)

    def test_upload_collision_does_not_delete_existing_file(self):
        with tempfile.TemporaryDirectory(prefix='upload_collision_') as directory,patch.object(main,'STATIC_DIR',directory):
            root=Path(directory)/'uploads/questions'
            root.mkdir(parents=True)
            existing=root/('output_'+'a'*32+'.png')
            existing.write_bytes(b'existing')
            with patch.object(question_uploads.uuid,'uuid4',return_value=SimpleNamespace(hex='a'*32)):
                r=self.client.post('/api/admin/questions/upload-image',files={'image':('new.png',self.png(),'image/png')})
            self.assertEqual(r.status_code,500)
            self.assertEqual(existing.read_bytes(),b'existing')

    def test_upload_limits_and_legacy_svg_headers(self):
        with patch.object(question_uploads,'MAX_FILE_BYTES',16):
            r=self.client.post('/api/admin/questions/upload-image',files={'image':('test.png',self.png(),'image/png')})
            self.assertEqual(r.status_code,413,r.text)
        with patch.object(question_uploads,'MAX_BATCH_BYTES',16):
            self.assertEqual(self.client.post('/api/admin/questions/upload-image',content=b'x'*300000).status_code,413)
            chunks=iter([b'--abc\r\nContent-Disposition: form-data; name="image"; filename="image.png"\r\nContent-Type: image/png\r\n\r\n'+b'x'*150000,b'x'*150000+b'\r\n--abc--\r\n'])
            self.assertEqual(self.client.post('/api/admin/questions/upload-image',content=chunks,headers={'Content-Type':'multipart/form-data; boundary=abc'}).status_code,413)
        r=self.client.get('/static/uploads/questions/nonexistent-audit.svg')
        self.assertIn('sandbox',r.headers['content-security-policy'])

    def test_two_concurrent_edits_have_one_winner(self):
        self.make_package()
        revision=self.revision()
        other=TestClient(main.app);self.addCleanup(other.close);authenticate_admin(other)
        barrier=threading.Barrier(2)
        def edit(client,name):
            barrier.wait(10)
            return client.put('/api/admin/packages/ADMIN-AUDIT',json={'expected_edit_revision':revision,'name':name}).status_code
        with ThreadPoolExecutor(2) as pool:
            a=pool.submit(edit,self.client,'First');b=pool.submit(edit,other,'Second')
            self.assertEqual(sorted([a.result(10),b.result(10)]),[200,409])

    def test_delete_and_lock_cannot_both_succeed(self):
        db.set_exam_state('waiting')
        for n in range(4):
            code=f'RACE-PACKAGE-{n}'
            nim=f'RACE-USER-{n}'
            db.create_package(code,code)
            db.save_question(1,'Race question','Race',10,'SELECT 1',category=code)
            db.login_student(nim,nim,'testclient',f'token-{n}',f'device-{n}')
            barrier=threading.Barrier(2)
            def delete():
                barrier.wait(10)
                return db.delete_package(code,delete_questions=True)[0]
            def lock():
                barrier.wait(10)
                return db.lock_student_category(nim,code)[0]
            with ThreadPoolExecutor(2) as pool:
                deleted=pool.submit(delete);locked=pool.submit(lock)
                self.assertNotEqual(deleted.result(10),locked.result(10))
            with closing(db.get_sqlite_conn()) as conn:
                selected=conn.execute('SELECT selected_category FROM users WHERE nim=?',(nim,)).fetchone()[0]
                if selected:
                    self.assertIsNotNone(db.get_package_by_code(selected))

    def make_package(self):
        db.set_exam_state('waiting')
        db.create_package('ADMIN-AUDIT','Original','Original description',0)
        return db.save_question(1,'Audit question','Audit',10,'SELECT missing_column FROM missing_table',category='ADMIN-AUDIT')

    def test_active_exam_rejects_package_activation(self):
        self.make_package()
        db.set_exam_state('active')
        r=self.update(is_active=True)
        self.assertEqual(r.status_code,409)
        self.assertEqual(db.get_package_by_code('ADMIN-AUDIT')['is_active'],0)

    def test_delete_after_student_lock_is_rejected(self):
        self.make_package()
        db.update_package('ADMIN-AUDIT',is_active=1)
        db.login_student('RACE-AUDIT','Race audit','testclient','race-audit-token','race-audit-device')
        self.assertTrue(db.lock_student_category('RACE-AUDIT','ADMIN-AUDIT')[0])
        r=self.client.delete('/api/admin/packages/ADMIN-AUDIT',params={'expected_edit_revision':self.revision()})
        self.assertEqual(r.status_code,400,r.text)
        self.assertIsNotNone(db.get_package_by_code('ADMIN-AUDIT'))

    def revision(self):
        return next(p['edit_revision'] for p in self.client.get('/api/admin/packages').json()['packages'] if p['code']=='ADMIN-AUDIT')

    def update(self,client=None,**fields):
        return (client or self.client).put('/api/admin/packages/ADMIN-AUDIT',json={'expected_edit_revision':self.revision(),**fields})

    def test_active_exam_rejects_package_cascade_delete(self):
        qid=self.make_package()
        db.set_exam_state('active')
        r=self.client.delete('/api/admin/packages/ADMIN-AUDIT',params={'delete_questions':'true','expected_edit_revision':self.revision()})
        self.assertEqual(r.status_code,409)
        self.assertIsNotNone(db.get_question_by_id(qid))

    def test_stale_admin_edit_cannot_overwrite(self):
        self.make_package()
        other=TestClient(main.app);self.addCleanup(other.close);authenticate_admin(other)
        revision=self.revision()
        self.assertEqual(self.update(name='Admin one edit',description='Original description').status_code,200)
        self.assertEqual(other.put('/api/admin/packages/ADMIN-AUDIT',json={'expected_edit_revision':revision,'name':'Original','description':'Admin two edit'}).status_code,409)
        self.assertEqual(db.get_package_by_code('ADMIN-AUDIT')['name'],'Admin one edit')

    def test_missing_entities_return_not_found(self):
        self.assertEqual(self.client.put('/api/admin/packages/DOES-NOT-EXIST',json={'name':'Unknown'}).status_code,404)
        self.assertEqual(self.client.post('/api/admin/student/DOES-NOT-EXIST/reset-category').status_code,404)

    def test_empty_package_name_is_rejected(self):
        self.make_package()
        self.assertEqual(self.update(name='   ').status_code,422)
        self.assertEqual(db.get_package_by_code('ADMIN-AUDIT')['name'],'Original')

    def test_svg_and_invalid_image_bytes_are_rejected(self):
        with tempfile.TemporaryDirectory(prefix='admin_upload_audit_') as folder,patch.object(main,'STATIC_DIR',folder):
            for name,content in [('active.svg',b'<svg xmlns="http://www.w3.org/2000/svg" onload="window.auditOnly=true"/>'),('not-image.png',b'not an image')]:
                r=self.client.post('/api/admin/questions/upload-image',files={'image':(name,content,'application/octet-stream')})
                self.assertEqual(r.status_code,422,r.text)
                self.assertEqual(len(list((Path(folder)/'uploads/questions').glob('*'))),0)

    def test_invalid_batch_leaves_no_files(self):
        with tempfile.TemporaryDirectory(prefix='admin_upload_audit_') as folder,patch.object(main,'STATIC_DIR',folder):
            r=self.client.post('/api/admin/questions/upload-images',files=[('images',('one.png',b'bytes','image/png')),('images',('two.exe',b'bytes','application/octet-stream'))])
            self.assertEqual(r.status_code,422)
            self.assertEqual(len(list((Path(folder)/'uploads/questions').glob('*'))),0)

    def test_start_validation_preserves_practice_history(self):
        db.set_exam_state('waiting')
        self.assertEqual(self.client.get('/admin/practice').status_code,200)
        actor=admin_security.get_session(self.client.cookies['admin_session'])
        with admin_practice.isolated_store(admin_practice.location(actor)),closing(db.get_sqlite_conn()) as conn:
            before=conn.execute('SELECT id FROM users').fetchone()[0]
        with patch('grading_engine.validate_reference',return_value={'status':'valid'}):
            self.assertEqual(self.client.post('/api/admin/exam/validate').status_code,200)
            self.assertEqual(self.client.post('/api/admin/exam/start').status_code,200)
        self.assertEqual(self.client.get('/admin/practice').status_code,200)
        with admin_practice.isolated_store(admin_practice.location(actor)),closing(db.get_sqlite_conn()) as conn:
            after=conn.execute('SELECT id FROM users').fetchone()[0]
        self.assertEqual(before,after)

    def test_stale_practice_question_snapshot_is_rejected(self):
        db.set_exam_state('waiting')
        self.client.get('/admin/practice')
        q=db.get_question_by_id(self.qid)
        db.save_question(q['question_number'],q['title'],q['description'],q['score_weight'],'SELECT 999 AS v',question_id=self.qid,category='A')
        actor=admin_security.get_session(self.client.cookies['admin_session'])
        self.assertEqual(self.client.get('/api/admin/practice/questions').status_code,409)
        with admin_practice.isolated_store(admin_practice.location(actor)):
            self.assertNotEqual(db.get_question_by_id(self.qid)['expected_query'],'SELECT 999 AS v')

    def test_dataset_change_during_practice_grading_blocks_save(self):
        db.set_exam_state('waiting')
        html=self.client.get('/admin/practice').text
        payload={'nim':re.search(r'data-nim="([^"]+)"',html)[1],'session_token':re.search(r'data-token="([^"]+)"',html)[1]}
        self.client.post('/api/admin/practice/select-category',json={**payload,'category':'A'})
        def grade(**kwargs):
            token=db.SQLITE_REQUEST_PATH.set(None)
            try:
                with closing(db.get_sqlite_conn()) as conn,conn:
                    conn.execute("INSERT INTO system_settings(key,value) VALUES('dataset_revision','2') ON CONFLICT(key) DO UPDATE SET value=CAST(value AS INTEGER)+1")
            finally:
                db.SQLITE_REQUEST_PATH.reset(token)
            return self.grade
        with patch.object(db,'evaluate_sql_submission',side_effect=grade):
            r=self.client.post('/api/admin/practice/submit',json={**payload,'query':'SELECT 1','question_id':self.qid})
        self.assertEqual(r.status_code,409,r.text)
        actor=admin_security.get_session(self.client.cookies['admin_session'])
        with admin_practice.isolated_store(admin_practice.location(actor)),closing(db.get_sqlite_conn()) as conn:
            self.assertEqual(conn.execute('SELECT COUNT(*) FROM submission_attempts').fetchone()[0],0)
        self.assertEqual(self.client.get('/api/admin/practice/questions').status_code,409)


if __name__=='__main__':
    unittest.main(verbosity=2)
