"""Regression tests for the reproduced grading fairness audit failures."""
from pathlib import Path
import sys
import unittest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from test_support import ensure_test_environment
ensure_test_environment()
import database as db


class FairnessTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.nim='FAIRNESS'
        db.login_student(cls.nim,'Fairness tests','testclient','fairness-token','fairness-device')
        db.init_student_sandbox_schema(cls.nim)

    def grade(self,answer,reference,sandbox=False,**options):
        target='sandbox_pribadi' if sandbox else 'pagila'
        return db.evaluate_sql_submission(answer,reference,10,target_db=target,active_db=target,nim=self.nim,**options)

    def pending(self,result):
        self.assertFalse(result['graded'],result)
        self.assertIsNone(result['score'],result)
        self.assertIsNone(result['is_correct'],result)
        self.assertEqual(result['eval_status'],'manual_review',result)

    def test_ordinal_and_qualified_order_ties(self):
        base="SELECT * FROM (VALUES (1,'B',2),(1,'A',2),(2,'C',1)) t(k,label,v)"
        for order in ('1,3 DESC','t.k,v DESC'):
            for direction in ('ASC','DESC'):
                result=self.grade(base+' ORDER BY k,v DESC,label '+direction,base+' ORDER BY '+order)
                self.assertTrue(result['is_correct'],result)
        self.assertFalse(self.grade(base+' ORDER BY k DESC',base+' ORDER BY 1,3 DESC')['is_correct'])

    def test_projected_expression_order(self):
        base="SELECT k+0 AS k,label,v FROM (VALUES (1,'B',2),(1,'A',2)) t(k,label,v)"
        self.assertTrue(self.grade(base+' ORDER BY k,label',base+' ORDER BY (t.k+0)')['is_correct'])

    def test_unavailable_sort_expression_requires_review(self):
        base="SELECT * FROM (VALUES (1,'B'),(1,'A')) t(k,label)"
        outcomes=[self.grade(base+' ORDER BY k,label '+d,base+' ORDER BY k+0') for d in ('ASC','DESC')]
        self.assertTrue(any(not r['graded'] for r in outcomes),outcomes)
        for result in outcomes:
            if not result['graded']:
                self.pending(result)

    def test_ties_with_numeric_tolerance(self):
        ref="SELECT * FROM (VALUES (1,'A',2.0),(1,'B',1.0)) t(k,label,v) ORDER BY k"
        answer="SELECT * FROM (VALUES (1,'B',1.001),(1,'A',2.001)) t(k,label,v) ORDER BY k,label DESC"
        self.assertTrue(self.grade(answer,ref,grading_policy={'numeric_absolute_tolerance':'0.01'})['is_correct'])
        self.assertFalse(self.grade(answer.replace('1.001','9.0'),ref,grading_policy={'numeric_absolute_tolerance':'0.01'})['is_correct'])

    def test_json_null_and_precision_preserved(self):
        for answer,reference in [("NULL::jsonb","'null'::jsonb"),
                ("'{\"n\":0.12345678901234567892}'::jsonb","'{\"n\":0.12345678901234567891}'::jsonb")]:
            result=self.grade('SELECT '+answer+' AS v','SELECT '+reference+' AS v')
            self.assertTrue(result['graded'],result)
            self.assertFalse(result['is_correct'],result)
        self.assertTrue(self.grade("SELECT '{\"b\":2,\"a\":1.00}'::jsonb AS v","SELECT '{\"a\":1,\"b\":2}'::jsonb AS v")['is_correct'])
        self.assertFalse(self.grade("SELECT ARRAY[NULL::jsonb] AS v","SELECT ARRAY['null'::jsonb] AS v")['is_correct'])

    def test_array_bounds_and_values(self):
        self.assertFalse(self.grade("SELECT '[0:1]={1,2}'::int[] AS v",'SELECT ARRAY[1,2] AS v')['is_correct'])
        self.assertTrue(self.grade("SELECT '[1:2]={1.00,2.0}'::numeric[] AS v",'SELECT ARRAY[1,2]::numeric[] AS v')['is_correct'])

    def test_unique_index_attributes(self):
        ordinary='CREATE TABLE t(id INT); CREATE UNIQUE INDEX ux ON t(id)'
        self.assertFalse(self.grade(ordinary,ordinary+' NULLS NOT DISTINCT',True)['is_correct'])
        ref='CREATE TABLE t(a INT,b INT); CREATE UNIQUE INDEX ux ON t(a) INCLUDE(b)'
        self.assertFalse(self.grade(ref.replace('t(a) INCLUDE(b)','t(a,b)'),ref,True)['is_correct'])
        self.assertTrue(self.grade(ref,ref,True)['is_correct'])

    def test_check_difference_is_review_not_zero(self):
        ref='CREATE TABLE t(id INT CHECK(id>0))'
        self.pending(self.grade('CREATE TABLE t(id INT CHECK(0<id))',ref,True))
        self.pending(self.grade('CREATE TABLE t(id INT CHECK(id<0))',ref,True))
        result=self.grade('CREATE TABLE t(id INT)',ref,True)
        self.assertTrue(result['graded'],result)
        self.assertFalse(result['is_correct'],result)

    def test_ambiguous_limit_and_constant_answer_require_review(self):
        base="SELECT label FROM (VALUES(10,'A'),(10,'B')) t(n,label)"
        self.pending(self.grade(base+' ORDER BY n DESC,label DESC LIMIT 1',base+' ORDER BY n DESC LIMIT 1'))
        self.pending(self.grade('SELECT 2::bigint AS n','SELECT count(*) AS n FROM (VALUES(1),(2)) t(n)'))

    def test_limit_grades_clear_mismatches_and_keeps_projected_sort_ties_in_review(self):
        reference = 'SELECT actor_id FROM actor ORDER BY actor_id LIMIT 2'
        for answer, status in (
            ('SELECT actor_id FROM actor WHERE FALSE', 'row_count_mismatch'),
            ('SELECT actor_id FROM actor ORDER BY actor_id LIMIT 2 OFFSET 2', 'wrong_data'),
        ):
            with self.subTest(status=status):
                result = self.grade(answer, reference)
                self.assertTrue(result['graded'], result)
                self.assertEqual(result['score'], 0)
                self.assertEqual(result['eval_status'], status)
        base = "SELECT n,label FROM (VALUES (10,'A'),(10,'B')) t(n,label)"
        outcomes = [self.grade(base + f' ORDER BY n,label {direction} LIMIT 1', base + ' ORDER BY n LIMIT 1')
                    for direction in ('ASC', 'DESC')]
        self.assertTrue(any(result['is_correct'] for result in outcomes), outcomes)
        self.pending(next(result for result in outcomes if not result['is_correct']))

    def test_float_tolerance_is_explicit_not_global(self):
        answer='SELECT (0.1::float8+0.2::float8) AS v'
        ref='SELECT 0.3::numeric AS v'
        self.assertFalse(self.grade(answer,ref)['is_correct'])
        self.assertTrue(self.grade(answer,ref,grading_policy={'numeric_absolute_tolerance':'0.000000000001'})['is_correct'])

    def test_live_snapshot_preserves_json_and_arrays(self):
        setup="CREATE TABLE preserved(j jsonb,a int[]); INSERT INTO preserved VALUES('null','[0:1]={1,2}'),('{\"n\":0.12345678901234567891}','{1,2}')"
        self.assertTrue(db.execute_student_query(setup,'sandbox_pribadi',self.nim)['success'])
        try:
            answer="SELECT j,a FROM preserved"
            self.assertTrue(self.grade(answer,answer,True)['is_correct'])
            result=self.grade("SELECT '{\"n\":0.12345678901234567891}'::jsonb AS j FROM preserved WHERE j IS NOT NULL AND j<>'null'::jsonb",
                "SELECT j FROM preserved WHERE j IS NOT NULL AND j<>'null'::jsonb",True)
            self.assertTrue(result['is_correct'],result)
            self.assertEqual(result['student_result']['row_count'],1)
            result=self.grade("SELECT '[0:1]={1,2}'::int[] AS a FROM preserved WHERE j='null'::jsonb",
                "SELECT a FROM preserved WHERE j='null'::jsonb",True)
            self.assertTrue(result['is_correct'],result)
            self.assertEqual(result['student_result']['row_count'],1)
            wrong="SELECT CASE WHEN j IS NULL THEN 'null'::jsonb ELSE NULL::jsonb END AS j,a FROM preserved"
            self.assertFalse(self.grade(wrong,answer,True)['is_correct'])
        finally:
            db.execute_student_query('DROP TABLE preserved','sandbox_pribadi',self.nim)


if __name__=='__main__':
    unittest.main(verbosity=2)
