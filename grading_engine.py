"""Grade complete results and isolated state without writing the live student workspace."""

from contextlib import ExitStack, closing
import json
import math
import os
import threading
import time

import psycopg2
from psycopg2 import sql
from pglast import parse_sql

from grading_results import policy_from, column_mapping, compare_complete, GradingLimitError
from grading_workspace import Workspace, clone_workspace, signature
from query_runner import _cell, MAX_RESPONSE_BYTES
from sql_policy import prepare_sql, SQLPolicyError, ParseError, _walk, NON_REPLAYABLE_FUNCTIONS

GRADE_SECONDS = float(os.environ.get("EXAM_GRADE_SECONDS", "60"))
if not math.isfinite(GRADE_SECONDS) or not 0 < GRADE_SECONDS <= 60:
    raise ValueError("EXAM_GRADE_SECONDS harus lebih dari 0 dan maksimal 60 detik.")
_slots = threading.BoundedSemaphore(4)


def outcome(status, correct=False, score_weight=0, graded=True, preview=None):
    messages = {
        "correct": "Hasil jawaban sesuai spesifikasi soal.",
        "empty": "Query jawaban masih kosong.",
        "wrong_database": "Koneksi database tidak sesuai dengan database soal.",
        "column_mismatch": "Nama, urutan, atau tipe kolom belum sesuai spesifikasi soal.",
        "row_count_mismatch": "Jumlah baris hasil belum sesuai spesifikasi soal.",
        "wrong_order": "Urutan baris belum sesuai instruksi ORDER BY.",
        "wrong_data": "Data hasil belum sesuai spesifikasi soal.",
        "wrong_schema": "Struktur tabel atau constraint belum sesuai spesifikasi soal.",
        "missing_table": "Perintah CREATE TABLE untuk tabel yang diminta belum lengkap.",
        "missing_dml": "Jawaban belum memuat perubahan data yang diminta.",
        "wrong_command": "Jenis atau sasaran perintah tidak sesuai instruksi soal.",
        "table_not_found": "Tabel prasyarat belum tersedia. Jalankan DDL sebelumnya atau hubungi pengawas.",
        "reference_error": "Acuan atau konfigurasi soal bermasalah. Jawaban disimpan untuk diperiksa pengawas.",
        "evaluation_error": "Evaluator mengalami kendala. Jawaban disimpan untuk diperiksa pengawas.",
        "evaluation_limit": "Batas evaluator tercapai. Jawaban memerlukan penilaian pengawas.",
        "manual_review": "Jawaban menunggu penilaian pengawas.",
        "security_violation": "Perintah SQL tidak diizinkan dalam ujian.",
    }
    return {"is_correct": correct if graded else None, "score": int(score_weight) if correct and graded else (0 if graded else None),
            "graded": graded, "eval_status": status, "feedback": messages.get(status, status), "student_result": preview}


def reference_statements(query, sandbox):
    statements = prepare_sql(query, sandbox=sandbox)
    for statement in statements:
        if statement.kind.startswith("virtual_"):
            continue
        tree = parse_sql(statement.text)[0].stmt()
        for node in _walk(tree):
            if node.get("@") == "FuncCall" and node["funcname"][-1]["sval"].lower() in NON_REPLAYABLE_FUNCTIONS:
                raise ValueError("Acuan tidak boleh bergantung pada waktu, nilai acak, atau keadaan sesi.")
            if node.get("@") == "SQLValueFunction" and node["op"]["name"] not in {"SVFOP_CURRENT_DATE"}:
                raise ValueError("Acuan tidak boleh bergantung pada identitas atau waktu sesi.")
    return statements


class ReferenceExecutionError(Exception):
    pass


def reference_rows(workspace, cursor):
    try:
        yield from workspace.rows(cursor)
    except (GradingLimitError, psycopg2.extensions.QueryCanceledError):
        raise
    except Exception as error:
        raise ReferenceExecutionError() from error


def mode(statements):
    return "select" if all(item.kind == "SelectStmt" for item in statements) else "state"


def targets(statements, kinds):
    result = set()
    for statement in statements:
        if statement.kind not in kinds:
            continue
        node = parse_sql(statement.text)[0].stmt()
        relation = node.get("relation") or (node.get("into") or {}).get("rel")
        if relation:
            result.add(relation["relname"])
        if statement.kind == "DropStmt":
            result.update(obj[-1]["sval"] for obj in node["objects"])
    return result


def preview_collector(columns):
    result = {"success": True, "columns": columns, "rows": [], "row_count": 0, "is_truncated": False, "cells_truncated": False}
    size = 0
    def append(row):
        nonlocal size
        result["row_count"] += 1
        converted = [_cell(value, 4000) for value in row]
        values = [item[0] for item in converted]
        result["cells_truncated"] |= any(item[1] for item in converted)
        length = len(json.dumps(values, ensure_ascii=True).encode())
        if len(result["rows"]) < 500 and size + length <= MAX_RESPONSE_BYTES:
            size += length
            result["rows"].append(values)
        else:
            result["is_truncated"] = True
    return result, append


def projected_order_keys(query, columns):
    tree=parse_sql(query)[0].stmt()
    sorts=tree.get('sortClause') or ()
    targets=tree.get('targetList') or ()
    relations=tree.get('fromClause') or ()
    relation=relations[0] if len(relations)==1 and relations[0].get('@') in {'RangeVar','RangeSubselect'} else {}
    qualifier=(relation.get('alias') or {}).get('aliasname') or relation.get('relname')
    def normalized(value):
        if isinstance(value,dict):
            fields=value.get('fields') or ()
            if value.get('@')=='ColumnRef' and len(fields)==2 and fields[0].get('sval')==qualifier and qualifier:
                value={**value,'fields':fields[1:]}
            return {k:normalized(v) for k,v in value.items() if k!='location'}
        if isinstance(value,(list,tuple)):
            return [normalized(v) for v in value]
        return value
    keys=[]
    for sort in sorts:
        node=sort['node']
        fields=node.get('fields') or ()
        position=(node.get('val') or {}).get('ival') if node.get('@')=='A_Const' else None
        if position is not None and 1<=position<=len(columns):
            keys.append(position-1)
            continue
        if node.get('@')=='ColumnRef' and len(fields)==1 and columns.count(fields[0].get('sval'))==1:
            keys.append(columns.index(fields[0]['sval']))
            continue
        matches=[i for i,t in enumerate(targets) if normalized(t['val'])==normalized(node)]
        if len(matches)==1 and len(targets)==len(columns):
            keys.append(matches[0])
            continue
        # SELECT * from one relation has no explicit target expressions to match.
        relations=tree.get('fromClause') or ()
        if (len(targets)==1 and targets[0]['val'].get('@')=='ColumnRef'
            and any(f.get('@')=='A_Star' for f in targets[0]['val'].get('fields') or ())
            and len(relations)==1 and relations[0].get('@') in {'RangeVar','RangeSubselect'}
            and node.get('@')=='ColumnRef' and len(fields)==2):
            relation=relations[0]
            qualifier=(relation.get('alias') or {}).get('aliasname') or relation.get('relname')
            name=fields[-1].get('sval')
            if fields[0].get('sval')==qualifier and columns.count(name)==1:
                keys.append(columns.index(name))
                continue
        return None
    return keys or None


def compare_query(reference, student, ref_query, stud_query, policy, source=None):
    with reference.cursor(ref_query) as ref, student.cursor(stud_query, source) as actual:
        ref_columns = [item[0] for item in ref.description]
        stud_columns = [item[0] for item in actual.description]
        mapping = column_mapping(ref_columns, stud_columns, [item[1] for item in ref.description],
                                 [item[1] for item in actual.description], policy)
        if max(len(ref_columns), len(stud_columns)) > 64:
            raise GradingLimitError("Hasil melebihi 64 kolom evaluator.")
        if mapping is None:
            return False, "column_mismatch", None
        tree = parse_sql(ref_query)[0].stmt()
        ordered = policy.row_order == "required" or (policy.row_order == "auto" and bool(tree.get("sortClause")))
        order_keys = projected_order_keys(ref_query, ref_columns) if ordered else None
        limited = bool(tree.get("limitCount") or tree.get("limitOffset"))
        preview, collect = preview_collector([stud_columns[i] for i in mapping])
        student_rows = ((tuple(row[index] for index in mapping)) for row in student.rows(actual))
        correct, status, count = compare_complete(reference_rows(reference, ref), student_rows, policy, ordered,
                                                 reference.check, collect, order_keys=order_keys, limited=limited)
        preview["row_count"] = count
        if status == "wrong_order" and order_keys is None:
            status = "manual_review"
        return correct, status, preview


def prerequisite_queries(question_id):
    import database as db
    if question_id is None:
        return []
    question = db.get_question_by_id(question_id)
    with closing(db.get_sqlite_conn()) as conn:
        rows = conn.execute("""SELECT q.expected_query FROM questions q
            WHERE q.category=? AND q.question_number<? AND q.target_database='sandbox_pribadi'
            ORDER BY q.question_number,q.id""", (question["category"], question["question_number"])).fetchall()
    return [row[0] for row in rows if row[0] and mode(reference_statements(row[0], True)) == "state"]


def accepted_prerequisites(nim, question_id):
    import database as db
    if question_id is None:
        return []
    question = db.get_question_by_id(question_id)
    with closing(db.get_sqlite_conn()) as conn:
        rows = conn.execute("""SELECT s.submitted_query FROM submissions s JOIN users u ON u.id=s.user_id
            JOIN questions q ON q.id=s.question_id WHERE LOWER(u.nim)=LOWER(?) AND s.is_correct=1
            AND q.category=? AND q.question_number<? AND q.target_database='sandbox_pribadi'
            AND s.question_revision=q.question_revision ORDER BY q.question_number,q.id""",
            (nim, question["category"], question["question_number"])).fetchall()
    return [row[0] for row in rows if mode(prepare_sql(row[0], sandbox=True)) == "state"]


def isolated_pair(stack, deadline, nim, setup_query, question_id, validation=False):
    import database as db
    reference = Workspace(deadline)
    stack.callback(reference.close)
    student = Workspace(deadline)
    stack.callback(student.close)
    source = db.get_clean_student_schema_name(nim) if nim else None
    setup = [setup_query] if setup_query else (prerequisite_queries(question_id) if validation else accepted_prerequisites(nim, question_id))
    if setup:
        for query in setup:
            statements = reference_statements(query, True)
            reference.run(statements, source)
            student.run(statements, source)
    elif nim:
        clone_workspace(nim, (reference, student))
        reference.virtual = student.virtual = db.get_student_virtual_database(nim)
    return reference, student, source


def compare_state(reference, student, policy):
    ref = signature(reference.conn, reference.schema)
    actual = signature(student.conn, student.schema)
    if ref != actual or reference.virtual != student.virtual:
        if reference.virtual==student.virtual and set(ref)==set(actual):
            def without_checks(value):
                return {name:{**attrs,'constraints':[c for c in attrs['constraints'] if ' ADD CHECK ' not in c[0]]}
                    for name,attrs in value.items()}
            def check_counts(value):
                return {name:sum(' ADD CHECK ' in c[0] for c in attrs['constraints']) for name,attrs in value.items()}
            if without_checks(ref)==without_checks(actual) and check_counts(ref)==check_counts(actual):
                return False,'manual_review',None
        return False, "wrong_schema", None
    for table in ref:
        query = sql.SQL("SELECT * FROM {}").format(sql.Identifier(table)).as_string(reference.conn)
        equal, status, _ = compare_query(reference, student, query, query,
                                        policy.model_copy(update={"row_order": "ignore"}))
        if not equal:
            return equal, status, None
    return True, "correct", None


def evaluate(submitted_query, expected_query, score_weight=10, target_db="pagila", active_db="pagila", nim=None,
             grading_policy=None, setup_query="", question_id=None):
    import database as db
    clean = lambda value: (value or db.DEFAULT_EXAM_DB).strip().lower().split("(")[0].strip()
    sandbox = clean(target_db) == "sandbox_pribadi"
    if clean(target_db) != clean(active_db):
        return outcome("wrong_database")
    if not (submitted_query or "").strip():
        return outcome("empty")
    try:
        policy = policy_from(grading_policy)
    except Exception:
        return outcome("reference_error", graded=False)
    if policy.mode == "manual" or not (expected_query or "").strip():
        return outcome("manual_review", graded=False)
    try:
        expected = reference_statements(expected_query, sandbox)
        if setup_query:
            reference_statements(setup_query, True)
        expected_mode = mode(expected)
        if expected_mode == "state" and any(x.kind == "SelectStmt" for x in expected):
            raise ValueError("Pisahkan soal perubahan state dan SELECT.")
    except Exception:
        return outcome("reference_error", graded=False)
    try:
        if len(submitted_query) > db.MAX_STUDENT_QUERY_LENGTH:
            raise SQLPolicyError("Query terlalu panjang.")
        submitted = prepare_sql(submitted_query, sandbox=sandbox,
                                 schema=db.get_clean_student_schema_name(nim) if sandbox and nim else None)
    except SQLPolicyError:
        return outcome("security_violation")
    except (ParseError, ValueError) as error:
        result = outcome("syntax_error")
        result["feedback"] = f"Sintaks SQL tidak valid: {error}"
        return result
    if expected_mode == "select" and mode(submitted) != "select":
        return outcome("wrong_command")
    creates = targets(expected, {"CreateStmt", "CreateTableAsStmt"})
    inserts = targets(expected, {"InsertStmt"})
    if creates and not creates.issubset(targets(submitted, {"CreateStmt", "CreateTableAsStmt"})):
        return outcome("missing_table")
    if inserts and not targets(submitted, {"InsertStmt"}):
        return outcome("missing_dml")
    for kind in ("UpdateStmt", "DeleteStmt"):
        required = targets(expected, {kind})
        if required and not required.issubset(targets(submitted, {kind})):
            return outcome("missing_dml")
    if expected_mode == "state" and not sandbox:
        return outcome("reference_error", graded=False)
    if not _slots.acquire(blocking=False):
        return outcome("evaluation_limit", graded=False)
    deadline = time.monotonic() + GRADE_SECONDS
    try:
        with ExitStack() as stack:
            if sandbox:
                reference, student, source = isolated_pair(stack, deadline, nim, setup_query, question_id)
            else:
                pool = db.get_pg_student_pool_for_db(clean(target_db))
                conn = db.acquire_pooled_connection(pool, clean(target_db))
                def release():
                    reusable = True
                    try:
                        conn.rollback()
                        conn.autocommit = True
                        with conn.cursor() as cursor:
                            cursor.execute("DISCARD ALL")
                        conn.set_session(isolation_level="READ COMMITTED", readonly=None, autocommit=False)
                    except Exception:
                        reusable = False
                    pool.putconn(conn, close=not reusable)
                stack.callback(release)
                conn.rollback()
                conn.autocommit = True
                with conn.cursor() as cursor:
                    cursor.execute("DISCARD ALL")
                conn.set_session(isolation_level="REPEATABLE READ", readonly=True, autocommit=False)
                reference = Workspace.__new__(Workspace)
                reference.conn, reference.schema, reference.deadline = conn, "public", deadline
                student, source = reference, None
            if expected_mode == "select":
                if len(expected) != 1 or len(submitted) != 1:
                    return outcome("wrong_command")
                ref_tree=parse_sql(expected[0].text)[0].stmt()
                answer_tree=parse_sql(submitted[0].text)[0].stmt()
                constant_candidate=bool(ref_tree.get('fromClause')) and not any(n.get('@')=='SelectStmt' and n.get('fromClause') for n in _walk(answer_tree))
                try:
                    # Prepare reference first so semantic errors cannot be blamed on the answer.
                    with reference.cursor(expected[0].text) as checked:
                        checked.fetchmany(0)
                except (GradingLimitError, psycopg2.extensions.QueryCanceledError):
                    raise
                except Exception:
                    return outcome("reference_error", graded=False)
                try:
                    correct, status, preview = compare_query(reference, student, expected[0].text, submitted[0].text, policy, source)
                    if correct and constant_candidate:
                        correct,status=False,'manual_review'
                except psycopg2.extensions.QueryCanceledError:
                    raise
                except psycopg2.Error as error:
                    result = outcome("syntax_error")
                    result["feedback"] = db.sanitize_db_error_message(error)
                    return result
            else:
                for target in (reference, student):
                    for table in creates:
                        target.execute(sql.SQL("DROP TABLE IF EXISTS {} CASCADE").format(sql.Identifier(table)))
                    if inserts - creates:
                        available = signature(target.conn, target.schema)
                        if not (inserts - creates).issubset(available):
                            return outcome("table_not_found")
                        target.execute(sql.SQL("TRUNCATE {} RESTART IDENTITY CASCADE").format(sql.SQL(",").join(map(sql.Identifier, sorted(inserts - creates)))))
                try:
                    reference.run(expected)
                except (GradingLimitError, psycopg2.extensions.QueryCanceledError):
                    raise
                except Exception:
                    return outcome("reference_error", graded=False)
                try:
                    student.run(submitted, source)
                except psycopg2.extensions.QueryCanceledError:
                    raise
                except psycopg2.Error as error:
                    result = outcome("syntax_error")
                    result["feedback"] = db.sanitize_db_error_message(error)
                    return result
                except (SQLPolicyError, ValueError) as error:
                    result = outcome("syntax_error")
                    result["feedback"] = str(error)
                    return result
                correct, status, preview = compare_state(reference, student, policy)
            return outcome(status, correct, score_weight, graded=status!='manual_review',preview=preview)
    except ReferenceExecutionError:
        return outcome("reference_error", graded=False)
    except (GradingLimitError, psycopg2.extensions.QueryCanceledError):
        return outcome("evaluation_limit", graded=False)
    except Exception:
        return outcome("evaluation_error", graded=False)
    finally:
        _slots.release()


def validate_reference(query, target_db, grading_policy=None, setup_query="", question_id=None):
    policy = policy_from(grading_policy)
    if policy.mode == "manual":
        return {"status": "manual", "message": "Penilaian manual."}
    if not query or not query.strip():
        raise ValueError("Soal otomatis memerlukan query acuan atau mode manual eksplisit.")
    sandbox = target_db == "sandbox_pribadi"
    statements = reference_statements(query, sandbox)
    if mode(statements) == "state" and any(x.kind == "SelectStmt" for x in statements):
        raise ValueError("Pisahkan acuan perubahan state dan SELECT.")
    if sandbox:
        with ExitStack() as stack:
            reference, _, _ = isolated_pair(stack, time.monotonic() + GRADE_SECONDS, None, setup_query, question_id, validation=True)
            reference.run(statements)
    else:
        result = evaluate(query, query, target_db=target_db, active_db=target_db, grading_policy=policy)
        if not result["graded"] or not result["is_correct"]:
            raise ValueError("Query acuan tidak dapat divalidasi pada dataset saat ini.")
    return {"status": "valid", "message": "Acuan berhasil divalidasi."}
