import os
from contextvars import ContextVar
import uuid
import sqlite3
import datetime
from datetime import date, time as dt_time
import decimal
import json
import re
import time
import zipfile
import io
import csv
import threading
from contextlib import closing, nullcontext
import hashlib
from typing import Optional, Dict, Any, List, Tuple, Set

import psycopg2

from psycopg2 import sql, extras, pool
import sandbox_roles
from sql_policy import prepare_sql, split_statements, SQLPolicyError, ParseError



# Paths

BASE_DIR = os.path.dirname(os.path.abspath(__file__))

SQLITE_DB_PATH = os.path.join(BASE_DIR, "exam_system.db")

CLASSICMODELS_SQL_PATH = os.path.join(BASE_DIR, "data", "classicmodels.sql")



# Load .env file automatically if present

env_file = os.path.join(BASE_DIR, ".env")

if os.path.exists(env_file):

    try:

        with open(env_file, "r", encoding="utf-8") as f:

            for line in f:

                line = line.strip()

                if line and not line.startswith("#") and "=" in line:

                    k, v = line.split("=", 1)

                    k = k.strip()

                    v = v.strip().strip("'\"")

                    if k and k not in os.environ:

                        os.environ[k] = v

    except Exception:

        pass



# PostgreSQL Configuration

PG_HOST = os.getenv("PG_HOST", "localhost")

PG_PORT = int(os.getenv("PG_PORT", 5432))

DEFAULT_EXAM_DB = os.getenv("DEFAULT_EXAM_DB", "pagila")

PG_DB = os.getenv("PG_DB", DEFAULT_EXAM_DB)

PG_ADMIN_USER = os.getenv("PG_ADMIN_USER", "postgres")

PG_ADMIN_PASS = os.getenv("PG_ADMIN_PASS", "")

PG_STUDENT_USER = os.getenv("PG_STUDENT_USER", "student_role")

PG_STUDENT_PASS = os.getenv("PG_STUDENT_PASS", "student123")

# Exam Hint Mode: 'strict' (default for formal exams - pedagogical guidance without spilling answers)
# or 'guided' (for informal practice/tutorials - detailed technical diffs)
EXAM_HINT_MODE = os.getenv("EXAM_HINT_MODE", "strict").lower().strip()

# Allow multiple students on shared Wi-Fi / NAT router IP address
ALLOW_SHARED_IP = os.getenv("ALLOW_SHARED_IP", "true").lower().strip() in ("true", "1", "yes")



# Multi-Database Whitelist & Metadata (pgAdmin-style selector)

ALLOWED_DATABASES = [

    {

        "name": "pagila",

        "label": "pagila (Rental Film)",

        "description": "Database resmi praktikum rental film & DVD (15 tabel master)",

        "type": "master_readonly"

    },

    {

        "name": "sandbox_pribadi",

        "label": "sandbox_pribadi (DDL / DML)",

        "description": "Kamar database pribadi Anda untuk latihan CREATE, INSERT, & DROP tabel",

        "type": "sandbox_student"

    },

    {

        "name": "classicmodels",

        "label": "classicmodels (Retail & Sales)",

        "description": "Database praktikum penjualan model skala & pelanggan (8 tabel)",

        "type": "master_readonly"

    }

]



def get_allowed_databases() -> List[Dict[str, Any]]:

    """Returns list of allowed databases for students and proctors."""

    return ALLOWED_DATABASES



# ==============================================================================

# SQLite System Database Management

# ==============================================================================



SQLITE_REQUEST_PATH = ContextVar("sqlite_request_path", default=None)
SQLITE_COMMIT_GUARD = ContextVar("sqlite_commit_guard", default=nullcontext)


def get_sqlite_conn() -> sqlite3.Connection:
    db_path = SQLITE_REQUEST_PATH.get() or os.environ.get("EXAM_SQLITE_PATH", SQLITE_DB_PATH)
    conn = sqlite3.connect(db_path, timeout=30.0)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON;")
    conn.execute("PRAGMA journal_mode = WAL;")
    conn.execute("PRAGMA synchronous = NORMAL;")
    conn.execute("PRAGMA busy_timeout = 5000;")
    return conn



SQLITE_SCHEMA_VERSION = 5


def _add_missing_columns(conn: sqlite3.Connection, columns) -> None:
    for table, definition in columns:
        existing = {row["name"] for row in conn.execute(f"PRAGMA table_info({table});")}
        if definition.split()[0] not in existing:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {definition};")


def migrate_system_db(conn: sqlite3.Connection) -> None:
    """Upgrade legacy databases atomically; never infer correctness from ALTER errors."""
    conn.execute("BEGIN IMMEDIATE;")
    try:
        version = conn.execute("PRAGMA user_version;").fetchone()[0]
        if version > SQLITE_SCHEMA_VERSION:
            raise RuntimeError(
                f"SQLite schema version {version} is newer than supported version "
                f"{SQLITE_SCHEMA_VERSION}. Use a compatible application version."
            )
        migrations = (
            (1, (
                ("users", "virtual_db TEXT DEFAULT NULL"),
                ("users", "selected_category TEXT DEFAULT NULL"),
                ("users", "device_id TEXT DEFAULT NULL"),
                ("questions", "target_database TEXT DEFAULT 'pagila'"),
                ("questions", "difficulty_level TEXT DEFAULT 'Medium'"),
                ("questions", "expected_output_image TEXT DEFAULT ''"),
                ("questions", "question_images TEXT DEFAULT ''"),
                ("exam_packages", "description TEXT DEFAULT ''"),
                ("exam_packages", "is_active INTEGER DEFAULT 1"),
            )),
            (2, (
                ("submissions", "is_correct INTEGER DEFAULT 0"),
                ("submissions", "score INTEGER DEFAULT 0"),
                ("submissions", "eval_status TEXT DEFAULT 'pending'"),
                ("submissions", "feedback TEXT DEFAULT ''"),
            )),
            (3, (
                ("questions", "grading_policy TEXT NOT NULL DEFAULT '{}'"),
                ("questions", "setup_query TEXT NOT NULL DEFAULT ''"),
                ("questions", "question_revision INTEGER NOT NULL DEFAULT 1"),
                ("questions", "reference_validation TEXT NOT NULL DEFAULT 'unvalidated'"),
                ("submissions", "question_revision INTEGER NOT NULL DEFAULT 1"),
            )),
        )
        migrations += ((4, (("questions", "edit_revision INTEGER NOT NULL DEFAULT 1"),)),)
        migrations += ((5, (("users", "grading_revision INTEGER NOT NULL DEFAULT 0"),)),)
        for target_version, columns in migrations:
            if version < target_version:
                _add_missing_columns(conn, columns)
                if target_version == 3:
                    conn.execute("""CREATE TABLE submission_attempts (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                        question_id INTEGER NOT NULL REFERENCES questions(id) ON DELETE CASCADE,
                        submitted_query TEXT NOT NULL, submitted_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                        graded INTEGER NOT NULL, is_correct INTEGER, score INTEGER,
                        eval_status TEXT NOT NULL, feedback TEXT NOT NULL DEFAULT '',
                        question_revision INTEGER NOT NULL, source TEXT NOT NULL DEFAULT 'submission')""")
                    conn.execute("CREATE INDEX idx_attempts_student_question ON submission_attempts(user_id,question_id,id)")
                    conn.execute("""INSERT INTO submission_attempts
                        (user_id,question_id,submitted_query,submitted_at,graded,is_correct,score,eval_status,feedback,question_revision,source)
                        SELECT user_id,question_id,submitted_query,submitted_at,
                            CASE WHEN eval_status IN ('pending','manual_review','reference_error','evaluation_error','evaluation_limit') THEN 0 ELSE 1 END,
                            is_correct,score,COALESCE(eval_status,'pending'),COALESCE(feedback,''),1,'legacy_best'
                        FROM submissions""")
                if target_version == 4:
                    conn.execute("""CREATE TABLE admin_sessions (
                        id TEXT PRIMARY KEY, token_hash TEXT UNIQUE NOT NULL,
                        created_at INTEGER NOT NULL, expires_at INTEGER NOT NULL,
                        last_seen INTEGER NOT NULL, login_ip TEXT NOT NULL)""")
                    conn.execute("""CREATE TABLE admin_audit (
                        id INTEGER PRIMARY KEY AUTOINCREMENT, session_id TEXT NOT NULL,
                        method TEXT NOT NULL, path TEXT NOT NULL, status_code INTEGER NOT NULL,
                        ip_address TEXT NOT NULL, timestamp TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP)""")
                    conn.execute("CREATE INDEX idx_admin_audit_session ON admin_audit(session_id,id)")
                if target_version == 5:
                    import grading_queue
                    grading_queue.migrate(conn)
                conn.execute(f"PRAGMA user_version = {target_version};")
        for _, columns in migrations:
            for table, definition in columns:
                names = {row["name"] for row in conn.execute(f"PRAGMA table_info({table});")}
                if definition.split()[0] not in names:
                    raise RuntimeError(f"SQLite schema is inconsistent: {table}.{definition.split()[0]} is missing")
        for table, required in (("admin_sessions", {"id","token_hash","created_at","expires_at","last_seen","login_ip"}), ("admin_audit", {"id","session_id","method","path","status_code","ip_address","timestamp"})):
            if not required.issubset({r["name"] for r in conn.execute(f"PRAGMA table_info({table})")}):
                raise RuntimeError(f"SQLite schema is inconsistent: {table}")
        for table,required in (("grading_jobs", {"id","attempt_id","question_revision","exam_revision","dataset_revision","active_database","status","tries","lease_token","lease_until","decision_base","version"}),
                               ("grading_decisions", {"id","attempt_id","question_revision","graded","score","is_correct","eval_status","feedback","source","run_token"})):
            if not required.issubset({r["name"] for r in conn.execute(f"PRAGMA table_info({table})")}):
                raise RuntimeError(f"SQLite schema is inconsistent: {table}")
        attempts_columns = {row["name"] for row in conn.execute("PRAGMA table_info(submission_attempts)")}
        if not {"id", "user_id", "question_id", "submitted_query", "submitted_at", "graded", "is_correct", "score", "eval_status", "feedback", "question_revision", "source"}.issubset(attempts_columns):
            raise RuntimeError("SQLite schema is inconsistent: submission_attempts is missing or incomplete")
        conn.commit()
    except Exception:
        conn.rollback()
        raise


def init_system_db():
    conn = get_sqlite_conn()
    try:
        version = conn.execute("PRAGMA user_version;").fetchone()[0]
        if version > SQLITE_SCHEMA_VERSION:
            raise RuntimeError(f"Unsupported SQLite schema version: {version}")
        _initialize_system_db(conn)
    finally:
        conn.close()


def _initialize_system_db(conn: sqlite3.Connection):

    with conn:

        conn.executescript("""

        CREATE TABLE IF NOT EXISTS users (

            id INTEGER PRIMARY KEY AUTOINCREMENT,

            nim TEXT UNIQUE NOT NULL,

            name TEXT NOT NULL,

            ip_address TEXT,

            device_id TEXT,

            session_token TEXT,

            status TEXT DEFAULT 'offline',

            last_active TIMESTAMP DEFAULT CURRENT_TIMESTAMP,

            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP

        );



        CREATE TABLE IF NOT EXISTS questions (

            id INTEGER PRIMARY KEY AUTOINCREMENT,

            category TEXT NOT NULL DEFAULT 'A',

            question_number INTEGER NOT NULL,

            title TEXT NOT NULL,

            description TEXT NOT NULL,

            score_weight INTEGER DEFAULT 10,

            expected_query TEXT,

            target_database TEXT DEFAULT 'pagila',

            difficulty_level TEXT DEFAULT 'Medium',

            expected_output_image TEXT DEFAULT '',

            question_images TEXT DEFAULT '',

            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,

            UNIQUE(category, question_number)

        );



        CREATE TABLE IF NOT EXISTS submissions (

            id INTEGER PRIMARY KEY AUTOINCREMENT,

            user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,

            question_id INTEGER NOT NULL REFERENCES questions(id) ON DELETE CASCADE,

            submitted_query TEXT NOT NULL,

            submitted_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,

            status TEXT DEFAULT 'submitted',

            is_correct INTEGER DEFAULT 0,

            score INTEGER DEFAULT 0,

            eval_status TEXT DEFAULT 'pending',

            feedback TEXT DEFAULT '',

            UNIQUE(user_id, question_id)

        );



        CREATE TABLE IF NOT EXISTS exam_logs (

            id INTEGER PRIMARY KEY AUTOINCREMENT,

            user_id INTEGER REFERENCES users(id) ON DELETE SET NULL,

            action_type TEXT NOT NULL,

            details TEXT,

            timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP

        );



        CREATE TABLE IF NOT EXISTS system_settings (

            key TEXT PRIMARY KEY,

            value TEXT NOT NULL

        );



        CREATE TABLE IF NOT EXISTS waiting_room_messages (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            nim TEXT NOT NULL,
            name TEXT NOT NULL,
            message TEXT NOT NULL,
            sent_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS exam_packages (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            code TEXT UNIQUE NOT NULL,
            name TEXT NOT NULL,
            description TEXT DEFAULT '',
            is_active INTEGER DEFAULT 1,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );

        CREATE INDEX IF NOT EXISTS idx_users_nim ON users(nim);
        CREATE INDEX IF NOT EXISTS idx_users_session_token ON users(session_token);
        CREATE INDEX IF NOT EXISTS idx_users_status ON users(status);
        CREATE INDEX IF NOT EXISTS idx_questions_category ON questions(category);
        CREATE INDEX IF NOT EXISTS idx_questions_num ON questions(question_number);
        CREATE INDEX IF NOT EXISTS idx_questions_cat_num ON questions(category, question_number);
        CREATE INDEX IF NOT EXISTS idx_submissions_user_id ON submissions(user_id);
        CREATE INDEX IF NOT EXISTS idx_submissions_question_id ON submissions(question_id);
        CREATE INDEX IF NOT EXISTS idx_submissions_user_question ON submissions(user_id, question_id);
        CREATE INDEX IF NOT EXISTS idx_exam_logs_user_id ON exam_logs(user_id);
        CREATE INDEX IF NOT EXISTS idx_exam_logs_action ON exam_logs(action_type);
        CREATE INDEX IF NOT EXISTS idx_exam_logs_timestamp ON exam_logs(timestamp);
        CREATE INDEX IF NOT EXISTS idx_exam_packages_code ON exam_packages(code);

        """)



        migrate_system_db(conn)

        import admin_security
        admin_security.initialize_secret(conn, os.getenv("ADMIN_MASTER_KEY"))
        cur = conn.cursor()

        # Check if initial system seeding has been completed or marked
        cur.execute("SELECT value FROM system_settings WHERE key = 'initial_seed_completed';")
        seed_row = cur.fetchone()

        if seed_row is None:
            # Check if this database has already been configured or used by an admin
            cur.execute("SELECT COUNT(*) FROM exam_packages;")
            pkg_count = cur.fetchone()[0]
            cur.execute("SELECT COUNT(*) FROM questions;")
            q_count = cur.fetchone()[0]
            cur.execute("SELECT COUNT(*) FROM exam_logs WHERE action_type IN ('create_package', 'delete_package', 'reset_all', 'delete_question', 'save_question');")
            admin_activity_count = cur.fetchone()[0]

            if pkg_count > 0 or q_count > 0 or admin_activity_count > 0:
                # Existing database already touched by admin -> mark as completed so we NEVER seed dummy questions
                cur.execute(
                    """
                    INSERT INTO system_settings (key, value) VALUES ('initial_seed_completed', '1')
                    ON CONFLICT(key) DO UPDATE SET value = '1';
                    """
                )
                conn.commit()
                is_seed_completed = True
            else:
                is_seed_completed = False
        else:
            is_seed_completed = (str(seed_row[0]).strip() == "1")

        if not is_seed_completed:
            # Seed default exam packages only if truly a fresh install and not yet configured
            cur.execute("SELECT COUNT(*) FROM exam_packages;")
            if cur.fetchone()[0] == 0:
                cur.execute("SELECT DISTINCT category FROM questions WHERE category IS NOT NULL AND category != '';")
                cats = [r[0].strip().upper() for r in cur.fetchall() if r[0]]
                if not cats:
                    cats = ["A", "B"]
                for cat in sorted(set(cats)):
                    cur.execute(
                        "INSERT OR IGNORE INTO exam_packages (code, name, description, is_active) VALUES (?, ?, ?, 1);",
                        (cat, f"Paket {cat}", f"Paket Soal Ujian Praktikum Basis Data {cat}")
                    )
                conn.commit()

            # Seed initial exam questions only if truly a fresh install
            cur.execute("SELECT COUNT(*) FROM questions;")
            if cur.fetchone()[0] == 0:
                seed_default_questions(conn)

            # Mark initial seeding as permanently completed
            cur.execute(
                """
                INSERT INTO system_settings (key, value) VALUES ('initial_seed_completed', '1')
                ON CONFLICT(key) DO UPDATE SET value = '1';
                """
            )
            conn.commit()

        repair_default_question_targets(conn)


def get_admin_master_key() -> str:

    """Return the encoded master credential; never a recoverable password."""

    conn = get_sqlite_conn()

    cur = conn.cursor()

    cur.execute("SELECT value FROM system_settings WHERE key = 'admin_master_key';")

    row = cur.fetchone()

    conn.close()

    return row["value"] if row else ""



def update_admin_master_key(new_key: str) -> bool:
    import admin_security
    with closing(get_sqlite_conn()) as conn, conn:
        conn.execute("UPDATE system_settings SET value=? WHERE key='admin_master_key'", (admin_security.hash_key(new_key.strip()),))
        conn.execute("DELETE FROM admin_sessions")
    return True


def _default_questions():
    return [

        (

            1,

            "Daftar Pelanggan San Francisco",

            "Tampilkan kolom **customer_name**, **contact_first_name**, **phone**, dan **credit_limit** dari tabel `customers` untuk semua pelanggan yang berada di kota ('city') **'San Francisco'**. Urutkan hasilnya berdasarkan **customer_name** secara ascending (A-Z).",

            15,

            "SELECT customer_name, contact_first_name, phone, credit_limit FROM customers WHERE city = 'San Francisco' ORDER BY customer_name ASC;"

        ),

        (

            2,

            "Produk dengan Stok Terbatas",

            "Manajemen membutuhkan informasi stok produk yang menipis. Tampilkan **product_code**, **product_name**, **product_line**, **quantity_in_stock**, dan **buy_price** dari tabel `products` yang memiliki **quantity_in_stock** kurang dari 3000 unit. Urutkan dari stok yang paling sedikit.",

            15,

            "SELECT product_code, product_name, product_line, quantity_in_stock, buy_price FROM products WHERE quantity_in_stock < 3000 ORDER BY quantity_in_stock ASC;"

        ),

        (

            3,

            "Rata-rata Harga Beli per Lini Produk",

            "Hitung rata-rata harga beli (**buy_price**) dan jumlah varian produk untuk setiap lini produk (**product_line**). Tampilkan kolom **product_line**, **total_produk** (hitung jumlah produk), dan **rata_rata_harga** (dibulatkan 2 desimal menggunakan `ROUND(AVG(buy_price), 2)`). Urutkan dari rata-rata harga tertinggi ke terendah.",

            20,

            "SELECT product_line, COUNT(*) AS total_produk, ROUND(AVG(buy_price), 2) AS rata_rata_harga FROM products GROUP BY product_line ORDER BY rata_rata_harga DESC;"

        ),

        (

            4,

            "Pelanggan dan Sales Representative",

            "Tampilkan data relasi antara pelanggan dan karyawan sales representative mereka. Kolom yang harus ditampilkan: **customer_name**, **city**, nama lengkap sales rep (**first_name** digabung dengan **last_name** sebagai **sales_rep_name**), serta email sales rep. Gunakan `INNER JOIN` antara tabel `customers` dan `employees` berdasarkan relasi `sales_rep_employee_number` = `employee_number`.",

            25,

            "SELECT c.customer_name, c.city, CONCAT(e.first_name, ' ', e.last_name) AS sales_rep_name, e.email FROM customers c INNER JOIN employees e ON c.sales_rep_employee_number = e.employee_number ORDER BY c.customer_name ASC;"

        ),

        (

            5,

            "Total Pendapatan Pesanan per Status",

            "Hitung total nilai penjualan pesanan untuk setiap **status** pesanan. Tampilkan kolom **status** dari tabel `orders` dan kolom kalkulasi **total_nilai** (`SUM(quantity_ordered * price_each)`). Gunakan relasi antara tabel `orders` dan `orderdetails`. Urutkan dari total nilai terbesar ke terkecil.",

            25,

            "SELECT o.status, SUM(od.quantity_ordered * od.price_each) AS total_nilai FROM orders o INNER JOIN orderdetails od ON o.order_number = od.order_number GROUP BY o.status ORDER BY total_nilai DESC;"

        )

    ]

def seed_default_questions(conn: sqlite3.Connection):
    conn.executemany(
        """INSERT INTO questions
           (question_number, title, description, score_weight, expected_query, target_database)
           VALUES (?, ?, ?, ?, ?, 'classicmodels')""",
        _default_questions(),
    )


def repair_default_question_targets(conn: sqlite3.Connection):
    # Only repair untouched seeds without submission history, outside an active exam.
    conn.executemany(
        """UPDATE questions
           SET target_database = 'classicmodels', reference_validation = 'unvalidated',
               question_revision = question_revision + 1, edit_revision = edit_revision + 1
           WHERE question_number = ? AND title = ? AND description = ? AND score_weight = ?
             AND expected_query = ? AND category = 'A' AND target_database = 'pagila'
             AND question_revision = 1 AND edit_revision = 1
             AND NOT EXISTS (SELECT 1 FROM system_settings WHERE key = 'exam_state' AND value = 'active')
             AND NOT EXISTS (SELECT 1 FROM submissions WHERE question_id = questions.id)
             AND NOT EXISTS (SELECT 1 FROM submission_attempts WHERE question_id = questions.id)""",
        _default_questions(),
    )


# ------------------------------------------------------------------------------

# Student User & Session Management (IP Binding, Anti-Joki & Unique NIM)

# ------------------------------------------------------------------------------



def is_user_active(last_active_str: Optional[str], status: str, timeout_seconds: int = 60) -> bool:
    if status not in ("online", "waiting") or not last_active_str:
        return False
    try:
        now = datetime.datetime.now(datetime.timezone.utc).replace(tzinfo=None)
        clean_str = last_active_str.replace("Z", "").split("+")[0].strip()
        dt = datetime.datetime.fromisoformat(clean_str)
        diff = (now - dt).total_seconds()
        # Normal active window: last active between 0 and timeout_seconds ago
        if 0 <= diff < timeout_seconds:
            return True
        # Minor clock skew / jitter tolerance (up to 5 seconds ahead)
        elif diff < 0 and abs(diff) < 5:
            return True
        return False
    except Exception:
        return status == "online"



def login_student(nim: str, name: str, ip_address: str, session_token: str, device_id: Optional[str] = None, resume_token: Optional[str] = None) -> Tuple[bool, Optional[Dict[str, Any]], Optional[str]]:
    nim = nim.strip()
    name = name.strip()
    if nim.upper().startswith('ADMIN-PRACTICE-') and SQLITE_REQUEST_PATH.get() is None:
        return False, None, "Identitas ini khusus simulasi admin."
    clean_dev = (device_id or "").strip()[:64]

    if not nim or not name:
        return False, None, "NIM dan Nama Lengkap wajib diisi!"

    if not re.match(r'^[A-Za-z0-9_\-\.]+$', nim):
        return False, None, "Format NIM tidak valid. Gunakan huruf dan angka tanpa spasi."

    conn = get_sqlite_conn()
    try:
        conn.execute("BEGIN IMMEDIATE;")
        cur = conn.cursor()

        # 1. Anti-Joki Perangkat:
        # Jika client menyertakan device_id unik browser/perangkat,
        # pastikan tidak ada mahasiswa lain yang sedang aktif di browser/perangkat yang sama.
        if clean_dev:
            cur.execute(
                "SELECT * FROM users WHERE device_id = ? AND LOWER(nim) != LOWER(?) AND status IN ('online', 'waiting');",
                (clean_dev, nim)
            )
            other_dev_users = cur.fetchall()
            for other_user in other_dev_users:
                if other_user["session_token"] not in (None, "KICKED_BY_ADMIN"):
                    conn.execute(
                        """
                        INSERT INTO exam_logs (user_id, action_type, details)
                        VALUES (?, 'multi_account_blocked', ?);
                        """,
                        (
                            other_user["id"],
                            f"Percobaan login dengan NIM {nim} ditolak: Perangkat ({clean_dev[:8]}...) sedang aktif untuk NIM {other_user['nim']}"
                        )
                    )
                    conn.commit()
                    return False, None, f"Perangkat/Browser ini masih memiliki sesi aktif untuk NIM {other_user['nim']}. Satu laptop/browser hanya boleh digunakan oleh satu mahasiswa!"

        # Jika ALLOW_SHARED_IP dinonaktifkan (mode strict IP):
        # Pastikan satu IP tidak menjalankan lebih dari 1 mahasiswa aktif sekaligus (kecuali localhost)
        if not ALLOW_SHARED_IP and ip_address not in ("127.0.0.1", "localhost", "::1", "testclient"):
            cur.execute(
                "SELECT * FROM users WHERE ip_address = ? AND LOWER(nim) != LOWER(?) AND status IN ('online', 'waiting');",
                (ip_address, nim)
            )
            other_active_on_ip = cur.fetchall()
            for other_user in other_active_on_ip:
                if other_user["session_token"] not in (None, "KICKED_BY_ADMIN"):
                    conn.execute(
                        """
                        INSERT INTO exam_logs (user_id, action_type, details)
                        VALUES (?, 'multi_account_blocked', ?);
                        """,
                        (
                            other_user["id"],
                            f"Percobaan login dengan NIM {nim} ditolak: Perangkat (IP: {ip_address}) sedang aktif untuk NIM {other_user['nim']}"
                        )
                    )
                    conn.commit()
                    return False, None, f"Perangkat ini (IP: {ip_address}) masih memiliki sesi aktif untuk NIM {other_user['nim']}. Satu perangkat hanya boleh digunakan oleh satu mahasiswa!"

        # 2. Cek apakah NIM ini sudah terdaftar sebelumnya
        cur.execute("SELECT * FROM users WHERE LOWER(nim) = LOWER(?);", (nim,))
        existing_user = cur.fetchone()

        if existing_user:
            user_id = existing_user["id"]
            reg_nim = existing_user["nim"]
            reg_name = existing_user["name"]
            old_ip = existing_user["ip_address"]
            old_dev = existing_user["device_id"] if "device_id" in existing_user.keys() else None
            user_status = existing_user["status"]

            if existing_user["session_token"] == "KICKED_BY_ADMIN":
                return False, None, "Sesi Anda dihentikan pengawas. Minta pengawas membebaskan sesi sebelum login kembali."

            # 2a. Verifikasi Nama (Mencegah mahasiswa login dengan NIM orang lain)
            if reg_name.strip().lower() != name.lower():
                conn.execute(
                    """
                    INSERT INTO exam_logs (user_id, action_type, details)
                    VALUES (?, 'nim_hijack_attempt', ?);
                    """,
                    (
                        user_id,
                        f"Percobaan login NIM {reg_nim} ditolak: Nama '{name}' tidak cocok dengan nama terdaftar '{reg_name}' (IP: {ip_address})"
                    )
                )
                conn.commit()
                return False, None, f"NIM {reg_nim} sudah terdaftar atas nama '{reg_name}'. Nama yang Anda masukkan tidak cocok. Pastikan Anda tidak menggunakan NIM mahasiswa lain!"

            # 2b. Enforce Unique Active NIM: Cek apakah NIM sedang aktif di sesi ujian
            active = user_status in ("online", "waiting") and bool(existing_user["session_token"])
            is_same_device = bool(
                resume_token and resume_token == existing_user["session_token"]
                and (not old_dev or (clean_dev and clean_dev == old_dev))
            )

            if active and not is_same_device:
                # Perangkat/IP LAIN mencoba login dengan NIM yang SEDANG AKTIF!
                conn.execute(
                    """
                    INSERT INTO exam_logs (user_id, action_type, details)
                    VALUES (?, 'duplicate_nim_blocked', ?);
                    """,
                    (
                        user_id,
                        f"Percobaan login ganda NIM {reg_nim} dari IP {ip_address} (dev: {clean_dev[:8] if clean_dev else 'none'}) DITOLAK karena sedang aktif di IP {old_ip}"
                    )
                )
                conn.commit()
                return False, None, f"NIM {reg_nim} sedang aktif digunakan dalam sesi ujian pada perangkat lain (IP: {old_ip}). Setiap mahasiswa hanya dapat aktif pada 1 sesi ujian dengan NIM unik. Minta pengawas untuk 'Bebaskan Sesi' jika perangkat Anda bermasalah."

            # Kunci perangkat bertahan sampai logout atau dibebaskan pengawas.
            action_type = 'resume_session' if active else 'login'
            action_desc = f"Mahasiswa {reg_nim} melanjutkan sesi pada IP {ip_address}" if active else f"Mahasiswa {reg_nim} login kembali dari IP {ip_address}"

            conn.execute(
                """
                UPDATE users
                SET ip_address = ?,
                    device_id = COALESCE(?, device_id),
                    session_token = ?,
                    status = 'online',
                    last_active = CURRENT_TIMESTAMP
                WHERE id = ?;
                """,
                (ip_address, clean_dev or None, session_token, user_id)
            )
            conn.execute(
                """
                INSERT INTO exam_logs (user_id, action_type, details)
                VALUES (?, ?, ?);
                """,
                (user_id, action_type, action_desc)
            )

        else:
            # 3. Registrasi Mahasiswa Baru dengan NIM unik
            cur.execute(
                """
                INSERT INTO users (nim, name, ip_address, device_id, session_token, status, last_active)
                VALUES (?, ?, ?, ?, ?, 'online', CURRENT_TIMESTAMP);
                """,
                (nim, name, ip_address, clean_dev or None, session_token)
            )
            user_id = cur.lastrowid
            cur.execute(
                """
                INSERT INTO exam_logs (user_id, action_type, details)
                VALUES (?, 'login', ?);
                """,
                (user_id, f"Registrasi awal dan login NIM {nim} ({name}) dari IP {ip_address}")
            )

        conn.commit()
        cur.execute("SELECT * FROM users WHERE id = ?;", (user_id,))
        user_row = dict(cur.fetchone())
        return True, user_row, None



    except Exception as e:

        conn.rollback()

        raise e

    finally:

        conn.close()



class StudentAccessError(Exception):
    def __init__(self, message: str, status_code: int = 401, code: str = "invalid_session", exam_state=None):
        super().__init__(message)
        self.status_code = status_code
        self.code = code
        self.exam_state = exam_state


def require_student_access(nim, session_token, request_ip, *, active=False, category=False,
                           revision=None, conn=None):
    if not nim or not session_token:
        raise StudentAccessError("Sesi tidak ditemukan. Silakan login kembali.")
    if conn is None:
        with closing(get_sqlite_conn()) as connection:
            return require_student_access(nim, session_token, request_ip, active=active,
                                          category=category, revision=revision, conn=connection)
    row = conn.execute("""
        SELECT u.*,
            COALESCE((SELECT value FROM system_settings WHERE key='exam_state'), 'waiting') AS exam_state,
            COALESCE((SELECT value FROM system_settings WHERE key='exam_revision'), '0') AS exam_revision
        FROM users u WHERE LOWER(u.nim)=LOWER(?);
    """, (nim,)).fetchone()
    if not row:
        raise StudentAccessError("Pengguna tidak ditemukan.")
    user = dict(row)
    if user["session_token"] == "KICKED_BY_ADMIN":
        raise StudentAccessError("Sesi ujian Anda telah dihentikan oleh pengawas.")
    if user["status"] not in ("online", "waiting") or user["session_token"] != session_token:
        raise StudentAccessError("Sesi Anda telah berakhir atau telah diganti. Silakan login kembali.")
    if user["ip_address"] != request_ip:
        raise StudentAccessError("Alamat IP berubah. Silakan login ulang dari perangkat yang sama.")
    if active and user["exam_state"] != "active":
        message = "Ujian telah selesai." if user["exam_state"] == "finished" else "Ujian belum dimulai."
        raise StudentAccessError(message, 403, "exam_not_active", user["exam_state"])
    if revision is not None and str(revision) != user["exam_revision"]:
        raise StudentAccessError("Status ujian berubah saat permintaan diproses. Jawaban belum disimpan.",
                                 403, "exam_changed", user["exam_state"])
    if category:
        package = conn.execute("SELECT is_active FROM exam_packages WHERE code=?;",
                               (user["selected_category"],)).fetchone()
        if not package or not package["is_active"]:
            raise StudentAccessError("Pilih paket soal aktif sebelum melanjutkan.", 403, "package_required")
    return user


def validate_student_session(nim: str, session_token: str, request_ip: str) -> Tuple[bool, Optional[Dict[str, Any]], str]:
    try:
        return True, require_student_access(nim, session_token, request_ip), "Valid"
    except StudentAccessError as error:
        return False, None, str(error)


def update_student_heartbeat(user_id: int, session_token: str, request_ip: str):
    with closing(get_sqlite_conn()) as conn, conn:
        conn.execute("BEGIN IMMEDIATE;")
        row = conn.execute("SELECT nim FROM users WHERE id=?", (user_id,)).fetchone()
        user = require_student_access(row["nim"] if row else None, session_token, request_ip, conn=conn)
        target_status = "online" if user["exam_state"] == "active" else "waiting"
        conn.execute("UPDATE users SET last_active=CURRENT_TIMESTAMP, status=? WHERE id=?;",
                     (target_status, user_id))
        return user


def logout_student(nim: str, session_token: str):
    with closing(get_sqlite_conn()) as conn, conn:
        conn.execute("BEGIN IMMEDIATE;")
        user = conn.execute("""SELECT id FROM users WHERE LOWER(nim)=LOWER(?)
            AND session_token=? AND status IN ('online','waiting')""", (nim, session_token)).fetchone()
        if user:
            conn.execute("UPDATE users SET status='offline',session_token=NULL,device_id=NULL WHERE id=?", (user["id"],))
            conn.execute("INSERT INTO exam_logs (user_id, action_type, details) VALUES (?, 'logout', 'Mahasiswa logout manual')",
                         (user["id"],))


def clear_student_session_lock(nim: str) -> Tuple[bool, str]:
    """Membebaskan kunci sesi perangkat & status online untuk NIM tertentu tanpa menghapus submisi."""
    clean_nim = (nim or "").strip()
    if not clean_nim:
        return False, "NIM tidak boleh kosong."

    conn = get_sqlite_conn()
    try:
        cur = conn.cursor()
        cur.execute("SELECT id, name FROM users WHERE LOWER(nim) = LOWER(?);", (clean_nim,))
        user = cur.fetchone()
        if not user:
            return False, f"Mahasiswa dengan NIM '{clean_nim}' tidak ditemukan."

        user_id = user["id"]
        user_name = user["name"]

        cur.execute(
            """
            UPDATE users
            SET status = 'offline',
                session_token = NULL,
                device_id = NULL,
                last_active = datetime('now', '-120 seconds')
            WHERE id = ?;
            """,
            (user_id,)
        )
        cur.execute(
            """
            INSERT INTO exam_logs (user_id, action_type, details)
            VALUES (?, 'clear_session_lock', ?);
            """,
            (user_id, f"Pengawas membebaskan kunci sesi NIM {clean_nim} ({user_name}). Jawaban mahasiswa tetap tersimpan.")
        )
        conn.commit()
        return True, f"Kunci sesi untuk NIM {clean_nim} ({user_name}) berhasil dibebaskan. Mahasiswa dapat langsung login kembali tanpa kehilangan data jawaban."
    except Exception as e:
        conn.rollback()
        return False, f"Gagal membebaskan kunci sesi: {str(e)}"
    finally:
        conn.close()


def kick_student(nim: str) -> bool:
    with closing(get_sqlite_conn()) as conn, conn:
        conn.execute("BEGIN IMMEDIATE;")
        user = conn.execute("SELECT id FROM users WHERE LOWER(nim)=LOWER(?)", (nim,)).fetchone()
        if user:
            conn.execute("UPDATE users SET status='offline',session_token='KICKED_BY_ADMIN' WHERE id=?", (user["id"],))
            conn.execute("INSERT INTO exam_logs (user_id, action_type, details) VALUES (?, 'kick_student', 'Sesi dihentikan pengawas; login diblokir sampai sesi dibebaskan.')",
                         (user["id"],))
        return bool(user)


def stop_all_active_sessions() -> int:
    """Hentikan semua sesi mahasiswa yang sedang berlangsung sekaligus dan bebaskan semua kunci sesi."""
    conn = get_sqlite_conn()
    with conn:
        cur = conn.cursor()
        cur.execute(
            """
            UPDATE users
            SET session_token = 'KICKED_BY_ADMIN',
                device_id = NULL,
                status = 'offline',
                last_active = datetime('now', '-120 seconds')
            WHERE status = 'online' OR (session_token IS NOT NULL AND session_token != 'KICKED_BY_ADMIN');
            """
        )
        count = cur.rowcount
        cur.execute(
            """
            INSERT INTO exam_logs (action_type, details)
            VALUES ('stop_all_sessions', ?);
            """,
            (f"Pengawas menghentikan dan membebaskan semua sesi ({count} mahasiswa di-logout)",)
        )
    conn.close()
    return count


def clear_exam_logs() -> int:

    """Bersihkan seluruh riwayat log aktivitas sistem."""

    conn = get_sqlite_conn()

    with conn:

        cur = conn.cursor()

        cur.execute("SELECT COUNT(*) FROM exam_logs;")

        count = cur.fetchone()[0]

        cur.execute("DELETE FROM exam_logs;")

        cur.execute(

            """

            INSERT INTO exam_logs (action_type, details)

            VALUES ('clear_logs', 'Pengawas membersihkan seluruh riwayat log aktivitas ujian.');

            """

        )

    conn.close()

    return count



def clear_previous_sessions(clear_logs: bool = False) -> Dict[str, int]:

    """Bersihkan seluruh sesi mahasiswa dan jawaban/submisi sebelumnya."""

    conn = get_sqlite_conn()

    with conn:

        conn.execute("BEGIN IMMEDIATE;")
        _set_exam_state(conn, "waiting")
        cur = conn.cursor()

        cur.execute("SELECT COUNT(*) FROM users;")

        user_count = cur.fetchone()[0]

        cur.execute("SELECT COUNT(*) FROM submissions;")

        sub_count = cur.fetchone()[0]



        cur.execute("DELETE FROM submissions;")

        cur.execute("DELETE FROM users;")



        if clear_logs:

            cur.execute("DELETE FROM exam_logs;")

            cur.execute(

                """

                INSERT INTO exam_logs (action_type, details)

                VALUES ('reset_all', 'Pengawas membersihkan seluruh sesi mahasiswa, submisi, dan log aktivitas.');

                """

            )

        else:

            cur.execute(

                """

                INSERT INTO exam_logs (action_type, details)

                VALUES ('clear_sessions', ?);

                """,

                (f"Pengawas membersihkan sesi mahasiswa sebelumnya ({user_count} mahasiswa, {sub_count} submisi dihapus).",)

            )

    conn.close()

    return {"users_cleared": user_count, "submissions_cleared": sub_count}



# ------------------------------------------------------------------------------

# Questions CRUD

# ------------------------------------------------------------------------------

def parse_image_urls(val: Any) -> List[str]:
    """Parse string, JSON list, or list into a clean list of image URL strings."""
    if not val:
        return []
    if isinstance(val, list):
        return [str(x).strip() for x in val if str(x).strip()]
    if isinstance(val, str):
        val_str = val.strip()
        if not val_str:
            return []
        if val_str.startswith("[") and val_str.endswith("]"):
            try:
                parsed = json.loads(val_str)
                if isinstance(parsed, list):
                    return [str(x).strip() for x in parsed if str(x).strip()]
            except Exception:
                pass
        return [p.strip() for p in re.split(r'[\r\n,]+', val_str) if p.strip()]
    return []

def _enrich_question_dict(item: Dict[str, Any]) -> Dict[str, Any]:
    """Normalize and enrich question dictionary with multi-image lists."""
    # Question diagrams / images
    q_imgs_raw = item.get("question_images", "")
    q_imgs_list = parse_image_urls(q_imgs_raw)
    item["question_images_list"] = q_imgs_list
    item["question_images"] = json.dumps(q_imgs_list) if q_imgs_list else ""

    # Expected output table screenshots
    exp_raw = item.get("expected_output_image", "")
    exp_list = parse_image_urls(exp_raw)
    item["expected_output_images"] = exp_list
    item["expected_output_image"] = exp_list[0] if exp_list else (exp_raw if isinstance(exp_raw, str) else "")
    return item

def get_all_questions(include_expected_query: bool = False, category: Optional[str] = None) -> List[Dict[str, Any]]:

    conn = get_sqlite_conn()

    cur = conn.cursor()

    if category and category.strip():

        cur.execute("SELECT * FROM questions WHERE UPPER(category) = ? ORDER BY question_number ASC;", (category.strip().upper(),))

    else:

        cur.execute("SELECT * FROM questions ORDER BY category ASC, question_number ASC;")

    rows = cur.fetchall()

    conn.close()



    result = []

    for r in rows:

        item = dict(r)

        if not include_expected_query:

            item.pop("expected_query", None)
            item.pop("setup_query", None)
            item.pop("reference_validation", None)

        result.append(_enrich_question_dict(item))

    return result





def get_questions_by_category(category: str, include_expected_query: bool = False) -> List[Dict[str, Any]]:

    """Returns questions filtered by category for student or exam usage."""

    conn = get_sqlite_conn()

    cur = conn.cursor()

    cur.execute("SELECT * FROM questions WHERE UPPER(category) = ? ORDER BY question_number ASC;", (category.strip().upper(),))

    rows = cur.fetchall()

    conn.close()



    result = []

    for r in rows:

        item = dict(r)

        if not include_expected_query:

            item.pop("expected_query", None)
            item.pop("setup_query", None)
            item.pop("reference_validation", None)

        result.append(_enrich_question_dict(item))

    return result





def get_available_categories() -> List[Dict[str, Any]]:
    result = []
    for package in get_all_packages(include_inactive=False):
        if not package.get("total_questions"):
            continue
        item = dict(package)
        item.update(category=package["code"], count=package["total_questions"],
                    question_count=package["total_questions"], total_weight=package.get("total_score", 0))
        result.append(item)
    return result


def get_all_packages(include_inactive: bool = True) -> List[Dict[str, Any]]:

    """Returns all configured exam packages with real question stats."""

    conn = get_sqlite_conn()

    cur = conn.cursor()

    query = "SELECT id, code, name, description, is_active, created_at FROM exam_packages"

    if not include_inactive:

        query += " WHERE is_active = 1"

    query += " ORDER BY code ASC;"

    cur.execute(query)

    rows = cur.fetchall()



    cur.execute("SELECT category, COUNT(*), SUM(score_weight) FROM questions GROUP BY category;")

    q_stats = {r[0]: {"count": r[1], "total_score": r[2] or 0} for r in cur.fetchall()}



    cur.execute("SELECT selected_category, COUNT(*) FROM users WHERE selected_category IS NOT NULL AND selected_category != '' GROUP BY selected_category;")

    u_stats = {r[0]: r[1] for r in cur.fetchall()}

    conn.close()



    pkgs = []

    for r in rows:

        code = r[1]

        qs = q_stats.get(code, {"count": 0, "total_score": 0})

        pkgs.append({

            "id": r[0],

            "code": code,

            "name": r[2],

            "description": r[3] or "",

            "is_active": bool(r[4]),

            "created_at": r[5],

            "question_count": qs["count"],

            "total_questions": qs["count"],

            "total_score": qs["total_score"],

            "total_weight": qs["total_score"],

            "student_count": u_stats.get(code, 0),

            "total_students": u_stats.get(code, 0)

        })

    return pkgs



def get_package_by_code(code: str) -> Optional[Dict[str, Any]]:

    conn = get_sqlite_conn()

    cur = conn.cursor()

    cur.execute("SELECT id, code, name, description, is_active, created_at FROM exam_packages WHERE code = ?;", (code,))

    r = cur.fetchone()

    conn.close()

    if not r:

        return None

    return {

        "id": r[0],

        "code": r[1],

        "name": r[2],

        "description": r[3] or "",

        "is_active": bool(r[4]),

        "created_at": r[5]

    }



def create_package(code: str, name: str, description: str = "", is_active: int = 1, guard_exam=False) -> Tuple[bool, str, Optional[int]]:

    code = (code or "").strip().upper()

    name = (name or "").strip()

    if not code or not name:

        return False, "Kode dan Nama Paket wajib diisi.", None

    conn = get_sqlite_conn()

    cur = conn.cursor()

    try:

        conn.execute('BEGIN IMMEDIATE')
        state=conn.execute("SELECT value FROM system_settings WHERE key='exam_state'").fetchone()
        if guard_exam and state and state[0]=='active':
            raise StudentAccessError('Akhiri ujian sebelum membuat paket.',409,'exam_active')
        cur.execute("INSERT INTO exam_packages (code, name, description, is_active) VALUES (?, ?, ?, ?);", (code, name, description, is_active))

        pkg_id = cur.lastrowid

        conn.commit()

        return True, f"Paket {code} berhasil ditambahkan.", pkg_id

    except sqlite3.IntegrityError:

        return False, f"Kode paket '{code}' sudah digunakan.", None

    finally:

        conn.close()



def package_revision(package):
    fields=[package['id'],package['code'],package['name'],package['description'] or '',bool(package['is_active'])]
    return hashlib.sha256(json.dumps(fields,ensure_ascii=True).encode()).hexdigest()


def _package_write_guard(conn,code,expected_revision=None):
    state=conn.execute("SELECT value FROM system_settings WHERE key='exam_state'").fetchone()
    if state and state[0]=='active':
        raise StudentAccessError('Akhiri ujian sebelum mengubah paket.',409,'exam_active')
    row=conn.execute('SELECT * FROM exam_packages WHERE code=?',(code,)).fetchone()
    if not row:
        raise StudentAccessError('Paket tidak ditemukan.',404,'package_missing')
    if expected_revision is not None and package_revision(row)!=expected_revision:
        raise StudentAccessError('Paket berubah. Muat ulang sebelum menyimpan atau menghapus.',409,'package_conflict')


def update_package(code, name=None, description=None, is_active=None, expected_revision=None):
    code=(code or '').strip().upper()
    if name is not None and not name.strip():
        raise StudentAccessError('Nama paket wajib diisi.',422,'package_name')
    fields=[];values=[]
    for column,value in (('name',name),('description',description),('is_active',is_active)):
        if value is not None:
            fields.append(column+'=?')
            values.append(int(bool(value)) if column=='is_active' else value.strip())
    with closing(get_sqlite_conn()) as conn,conn:
        conn.execute('BEGIN IMMEDIATE')
        _package_write_guard(conn,code,expected_revision)
        if not fields:
            return False,'Tidak ada data yang diperbarui.'
        conn.execute("UPDATE exam_packages SET "+','.join(fields)+' WHERE code=?',values+[code])
    return True,f'Paket {code} berhasil diperbarui.'


def delete_package(code, delete_questions=False, expected_revision=None):
    code=(code or '').strip().upper()
    with closing(get_sqlite_conn()) as conn,conn:
        conn.execute('BEGIN IMMEDIATE')
        _package_write_guard(conn,code,expected_revision)
        count=conn.execute('SELECT COUNT(*) FROM users WHERE UPPER(selected_category)=?',(code,)).fetchone()[0]
        if count:
            return False,f'Tidak dapat menghapus Paket {code}: Masih ada {count} mahasiswa terkunci pada paket ini.'
        conn.execute('DELETE FROM exam_packages WHERE code=?',(code,))
        if delete_questions:
            conn.execute('DELETE FROM questions WHERE UPPER(category)=?',(code,))
    return True,f'Paket {code} berhasil dihapus.'


def lock_student_category(nim: str, category: str, session_context=None) -> Tuple[bool, str]:
    clean_cat = (category or "").strip().upper()
    with closing(get_sqlite_conn()) as conn, conn:
        conn.execute("BEGIN IMMEDIATE;")
        if session_context:
            user = require_student_access(nim, session_context["session_token"], session_context["ip_address"],
                                          active=True, revision=session_context["exam_revision"], conn=conn)
        else:
            row = conn.execute("SELECT * FROM users WHERE LOWER(nim)=LOWER(?)", (nim,)).fetchone()
            user = dict(row) if row else None
        if not user:
            return False, "Mahasiswa tidak ditemukan."
        package = conn.execute("SELECT code FROM exam_packages WHERE code=? AND is_active=1", (clean_cat,)).fetchone()
        has_questions = conn.execute("SELECT 1 FROM questions WHERE category=? LIMIT 1", (clean_cat,)).fetchone()
        if not package or not has_questions:
            return False, "Paket soal tidak tersedia atau belum memiliki soal."
        if user["selected_category"] and user["selected_category"] != clean_cat:
            return False, "Paket soal Anda sudah terkunci. Hubungi pengawas untuk mereset."
        conn.execute("UPDATE users SET selected_category=? WHERE id=?", (clean_cat, user["id"]))
        return True, f"Berhasil mengunci Paket {clean_cat}."


def reset_student_category(nim: str) -> Tuple[bool, str]:

    clean_nim = (nim or "").strip()

    conn = get_sqlite_conn()

    cur = conn.cursor()

    cur.execute("UPDATE users SET selected_category = '',grading_revision=grading_revision+1 WHERE LOWER(nim) = LOWER(?);", (clean_nim,))
    if cur.rowcount == 0:
        conn.close()
        raise StudentAccessError("Mahasiswa tidak ditemukan.",404,"student_missing")

    conn.commit()

    conn.close()

    return True, f"Pilihan paket untuk NIM {clean_nim} berhasil di-reset."


def reset_student_by_nim(nim: str) -> Dict[str, Any]:
    clean_nim = (nim or "").strip()
    if not clean_nim:
        return {"success": False, "message": "NIM tidak boleh kosong."}
    with closing(get_sqlite_conn()) as conn, conn:
        conn.execute("BEGIN IMMEDIATE;")
        user = conn.execute("SELECT id,name,nim FROM users WHERE LOWER(nim)=LOWER(?)", (clean_nim,)).fetchone()
        if not user:
            return {"success": False, "message": "Mahasiswa tidak ditemukan."}
        if not cleanup_student_sandbox_schema(user["nim"]):
            return {"success": False, "message": "Cleanup sandbox gagal; data mahasiswa dan jawaban tetap disimpan."}
        count = conn.execute("SELECT count(*) FROM submissions WHERE user_id=?", (user["id"],)).fetchone()[0]
        conn.execute("DELETE FROM submissions WHERE user_id=?", (user["id"],))
        conn.execute("DELETE FROM users WHERE id=?", (user["id"],))
        conn.execute("INSERT INTO exam_logs (action_type,details) VALUES ('reset_student',?)", (f"Pengawas mereset sesi dan workspace NIM {user['nim']}",))
        return {"success": True, "nim": user["nim"], "name": user["name"], "submissions_cleared": count, "schema_dropped": True}


def get_student_virtual_database(nim: Optional[str]) -> Optional[str]:
    if not nim:
        return None
    with closing(get_sqlite_conn()) as conn:
        row = conn.execute("SELECT virtual_db FROM users WHERE LOWER(nim)=LOWER(?)", (str(nim).strip(),)).fetchone()
        return row[0] if row else None


def set_student_virtual_database(nim: Optional[str], db_name: str) -> None:
    """Updates the student's virtual database name."""
    if not nim:
        return
    clean_nim = str(nim).strip()
    clean_vdb = db_name.strip().lower()
    conn = get_sqlite_conn()
    try:
        cur = conn.cursor()
        cur.execute("""
            INSERT INTO users (nim, name, ip_address, status, virtual_db)
            VALUES (?, ?, '127.0.0.1', 'offline', ?)
            ON CONFLICT(nim) DO UPDATE SET virtual_db = excluded.virtual_db;
        """, (clean_nim, f"Mahasiswa {clean_nim}", clean_vdb))
        conn.commit()
    except Exception as e:
        print(f"Error setting virtual db: {e}")
    finally:
        conn.close()

def clear_student_virtual_database(nim: Optional[str]) -> None:
    """Clears the student's virtual database name upon DROP DATABASE."""
    if not nim:
        return
    clean_nim = str(nim).strip()
    conn = get_sqlite_conn()
    try:
        cur = conn.cursor()
        cur.execute("UPDATE users SET virtual_db = NULL WHERE LOWER(nim) = LOWER(?);", (clean_nim,))
        conn.commit()
    except Exception:
        pass
    finally:
        conn.close()



def get_question_by_id(question_id: int) -> Optional[Dict[str, Any]]:

    conn = get_sqlite_conn()

    cur = conn.cursor()

    cur.execute("SELECT * FROM questions WHERE id = ?;", (question_id,))

    row = cur.fetchone()

    conn.close()

    return _enrich_question_dict(dict(row)) if row else None



def save_question(question_number, title, description, score_weight, expected_query=None, question_id=None,
                  category="A", target_database=DEFAULT_EXAM_DB, difficulty_level="Medium",
                  expected_output_image="", question_images="", grading_policy=None, setup_query=None, expected_edit_revision=None):
    from grading_results import policy_from
    category = (category or "A").strip().upper()
    if isinstance(question_number, bool) or not isinstance(question_number, int) or question_number < 1:
        raise StudentAccessError("Nomor soal harus bilangan bulat positif.", 422, "invalid_question_number")
    if isinstance(score_weight, bool) or not isinstance(score_weight, int) or score_weight < 1:
        raise StudentAccessError("Bobot soal harus bilangan bulat positif.", 422, "invalid_score_weight")
    with closing(get_sqlite_conn()) as conn, conn:
        conn.execute("BEGIN IMMEDIATE;")
        state = conn.execute("SELECT value FROM system_settings WHERE key='exam_state'").fetchone()
        if state and state[0] == "active":
            raise StudentAccessError("Akhiri ujian sebelum mengubah soal.", 409, "exam_active")
        old_row = conn.execute("SELECT * FROM questions WHERE id=?", (question_id,)).fetchone() if question_id else None
        if question_id and not old_row:
            raise StudentAccessError("Soal tidak ditemukan atau sudah dihapus admin lain.", 404, "question_missing")
        old = dict(old_row) if old_row else {}
        if old and expected_edit_revision is not None and old["edit_revision"] != expected_edit_revision:
            raise StudentAccessError("Soal telah diubah admin lain. Muat ulang sebelum menyimpan.", 409, "question_conflict")
        collision = conn.execute("SELECT id FROM questions WHERE UPPER(category)=? AND question_number=? AND id<>?",
                                 (category, question_number, question_id or -1)).fetchone()
        if collision:
            raise StudentAccessError(f"Paket {category} sudah memiliki soal nomor {question_number}. Pilih nomor lain.",
                                     409, "question_number_conflict")
        policy = policy_from(grading_policy if grading_policy is not None else old.get("grading_policy"))
        policy_json = json.dumps(policy.model_dump(mode="json"), sort_keys=True)
        setup = setup_query if setup_query is not None else old.get("setup_query", "")
        exp_list = parse_image_urls(expected_output_image)
        image = json.dumps(exp_list) if len(exp_list)>1 else (exp_list[0] if exp_list else "")
        images = parse_image_urls(question_images)
        question_image = json.dumps(images) if len(images)>1 else (images[0] if images else "")
        values = dict(question_number=question_number,title=title,description=description,score_weight=score_weight,
                      expected_query=expected_query,category=category,target_database=target_database,
                      difficulty_level=difficulty_level,expected_output_image=image,question_images=question_image,
                      grading_policy=policy_json,setup_query=setup)
        grading_fields = ("question_number","category","expected_query","score_weight","target_database","setup_query")
        changed = bool(old and (any(old.get(key) != values[key] for key in grading_fields)
                               or policy_from(old.get("grading_policy")) != policy))
        values["edit_revision"] = old.get("edit_revision", 0) + 1
        values["question_revision"] = old.get("question_revision",1) + int(changed)
        values["reference_validation"] = "unvalidated" if changed or not old else old["reference_validation"]
        if question_id:
            conn.execute("UPDATE questions SET " + ",".join(key+"=?" for key in values) + " WHERE id=?", (*values.values(),question_id))
            if changed:
                conn.execute("UPDATE submissions SET score=0,is_correct=0,eval_status='needs_regrade',feedback='Acuan soal berubah; nilai perlu dievaluasi ulang.' WHERE question_id=?", (question_id,))
        else:
            question_id = conn.execute("INSERT INTO questions ("+",".join(values)+") VALUES ("+",".join("?" for _ in values)+")", tuple(values.values())).lastrowid
        return question_id


def delete_question(question_id: int, expected_edit_revision=None):
    with closing(get_sqlite_conn()) as conn, conn:
        conn.execute("BEGIN IMMEDIATE")
        state = conn.execute("SELECT value FROM system_settings WHERE key='exam_state'").fetchone()
        if state and state[0] == "active":
            raise StudentAccessError("Akhiri ujian sebelum menghapus soal.", 409, "exam_active")
        row = conn.execute("SELECT edit_revision FROM questions WHERE id=?", (question_id,)).fetchone()
        if row and expected_edit_revision is not None and row[0] != expected_edit_revision:
            raise StudentAccessError("Soal telah diubah admin lain. Muat ulang sebelum menghapus.", 409, "question_conflict")
        conn.execute("DELETE FROM questions WHERE id=?", (question_id,))


def delete_all_questions():
    with closing(get_sqlite_conn()) as conn, conn:
        conn.execute("BEGIN IMMEDIATE")
        state = conn.execute("SELECT value FROM system_settings WHERE key='exam_state'").fetchone()
        if state and state[0] == "active":
            raise StudentAccessError("Akhiri ujian sebelum menghapus soal.", 409, "exam_active")
        conn.execute("DELETE FROM questions")


# ------------------------------------------------------------------------------

# Submissions Management

# ------------------------------------------------------------------------------





def extract_table_name_from_dml(dml_sql: str) -> Optional[str]:

    """

    Extracts the targeted table name from an INSERT INTO query.

    Handles optional schema qualifiers, quotes, and backticks.

    """

    if not dml_sql:

        return None

    match = re.search(

        r'INSERT\s+INTO\s+(?:[`"]?[a-zA-Z0-9_]+[`"]?\.)?[`"]?([a-zA-Z0-9_]+)[`"]?',

        dml_sql,

        re.IGNORECASE

    )

    return match.group(1).lower() if match else None


def extract_all_table_names_from_dml(dml_sql: str) -> List[str]:
    """
    Extracts all table names from one or multiple INSERT INTO statements.
    Preserves declaration order and filters duplicates.
    """
    if not dml_sql:
        return []
    matches = re.findall(
        r'INSERT\s+INTO\s+(?:[`"]?[a-zA-Z0-9_]+[`"]?\.)?[`"]?([a-zA-Z0-9_]+)[`"]?',
        dml_sql,
        re.IGNORECASE
    )
    seen = set()
    result = []
    for m in matches:
        m_clean = m.strip().lower()
        if m_clean not in seen:
            seen.add(m_clean)
            result.append(m.strip())
    return result


def compile_reference_dml(
    expected_dml_sql: str,
    table_name: Any,
    student_schema: str,
    dbname: str = DEFAULT_EXAM_DB
) -> Dict[str, Any]:
    """
    Compiles the lecturer's expected DML (INSERT INTO) query inside an isolated temporary schema.
    Clones all specified table structures from the student's sandbox schema, executes expected DML,
    extracts the reference rows and columns for each table, and cleanly drops the temporary schema.
    Guarantees ZERO schema leaks.
    """
    if isinstance(table_name, str):
        table_names = [table_name]
    elif isinstance(table_name, (list, tuple)):
        table_names = list(table_name)
    else:
        table_names = extract_all_table_names_from_dml(expected_dml_sql)

    temp_schema = f"_ref_dml_{uuid.uuid4().hex[:12]}"
    conn = get_pg_admin_connection(dbname=dbname)
    conn.autocommit = True
    cur = conn.cursor()

    try:
        cur.execute(f"CREATE SCHEMA {temp_schema};")
        for t in table_names:
            cur.execute(sql.SQL("CREATE TABLE {}.{} (LIKE {}.{} INCLUDING DEFAULTS INCLUDING CONSTRAINTS INCLUDING INDEXES);").format(
                sql.Identifier(temp_schema),
                sql.Identifier(t),
                sql.Identifier(student_schema),
                sql.Identifier(t)
            ))
        cur.execute(f"SET search_path TO {temp_schema}, public;")
        cur.execute(expected_dml_sql)

        tables_data = {}
        for t in table_names:
            cur.execute(sql.SQL("SELECT * FROM {}.{};").format(
                sql.Identifier(temp_schema),
                sql.Identifier(t)
            ))
            cols = [desc[0] for desc in cur.description] if cur.description else []
            rows = cur.fetchall()
            tables_data[t.lower()] = {
                "columns": cols,
                "rows": rows,
                "row_count": len(rows)
            }

        first_tbl = table_names[0].lower() if table_names else ""
        first_cols = tables_data[first_tbl]["columns"] if first_tbl in tables_data else []
        first_rows = tables_data[first_tbl]["rows"] if first_tbl in tables_data else []

        return {
            "success": True,
            "tables": tables_data,
            "columns": first_cols,
            "rows": first_rows,
            "row_count": len(first_rows)
        }
    except Exception as e:
        return {
            "success": False,
            "error": f"Gagal mengeksekusi kunci acuan DML dosen: {str(e)}",
            "tables": {},
            "columns": [],
            "rows": [],
            "row_count": 0
        }
    finally:
        try:
            cur.execute(f"DROP SCHEMA IF EXISTS {temp_schema} CASCADE;")
        except Exception:
            pass
        cur.close()
        conn.close()



def compare_table_records(

    ref_columns: List[str],

    ref_rows: List[Tuple],

    stud_columns: List[str],

    stud_rows: List[Tuple],

    table_name: str

) -> Tuple[bool, str, Dict[str, Any]]:

    """

    Compares the reference records with the student's records in a table.

    Validates:

    1. Non-empty table

    2. Row count matching

    3. Record values matching (order-insensitive, normalized)

    Returns (is_correct, feedback_message, diagnostic_details).

    """

    details = {

        "table_name": table_name,

        "expected_rows": len(ref_rows),

        "student_rows": len(stud_rows),

        "status": "ok"

    }



    if not stud_rows:

        details["status"] = "empty"

        return False, f"Tabel '{table_name}' masih kosong. Tidak ada baris data yang tersimpan.", details



    if len(stud_rows) != len(ref_rows):
        details["status"] = "row_count_mismatch"
        if EXAM_HINT_MODE == "guided":
            msg = f"Jumlah baris data pada tabel '{table_name}' ({len(stud_rows)} baris) belum sesuai. Periksa kembali jumlah data yang dimasukkan (diharapkan {len(ref_rows)} baris)."
        else:
            msg = f"Kuantitas baris data pada tabel '{table_name}' belum sesuai. Periksa kembali seluruh data registrasi mentah yang diminta untuk dimasukkan."
        return False, msg, details



    ref_cols_lower = [c.lower() for c in ref_columns]

    stud_cols_lower = [c.lower() for c in stud_columns]



    if ref_cols_lower == stud_cols_lower:

        col_mapping = list(range(len(ref_columns)))

    else:

        col_mapping = []

        for rc in ref_cols_lower:

            if rc in stud_cols_lower:

                col_mapping.append(stud_cols_lower.index(rc))

            else:

                col_mapping.append(None)



    norm_stud_rows = []

    for r in stud_rows:

        row_items = []

        for idx in col_mapping:

            val = r[idx] if idx is not None and idx < len(r) else None

            row_items.append(_normalize_cell_value(val))

        norm_stud_rows.append(row_items)



    norm_ref_rows = []

    for r in ref_rows:

        norm_ref_rows.append([_normalize_cell_value(val) for val in r])



    def row_sort_key(row):

        return tuple(str(x) for x in row)



    sorted_stud = sorted(norm_stud_rows, key=row_sort_key)

    sorted_ref = sorted(norm_ref_rows, key=row_sort_key)



    if sorted_stud != sorted_ref:

        details["status"] = "value_mismatch"

        unmatched_stud = [r for r in sorted_stud if r not in sorted_ref]

        unmatched_ref = [r for r in sorted_ref if r not in sorted_stud]

        details["unmatched_student"] = unmatched_stud

        details["unmatched_reference"] = unmatched_ref



        msg = f"Data yang tersimpan di tabel '{table_name}' belum sesuai. Periksa kembali keakuratan nilai setiap kolom (angka, teks, atau format tanggal) yang Anda masukkan."

        return False, msg, details



    msg = f"Sempurna! Seluruh {len(stud_rows)} baris data pada tabel '{table_name}' berhasil dimasukkan dan nilainya valid."

    return True, msg, details





def _normalize_cell_value(val: Any) -> Any:
    from grading_results import canonical
    return canonical(val)


def evaluate_sql_submission(submitted_query: str, expected_query: Optional[str], score_weight: int = 10,
                            target_db: str = DEFAULT_EXAM_DB, active_db: str = DEFAULT_EXAM_DB,
                            nim: Optional[str] = None, grading_policy=None, setup_query="", question_id=None):
    from grading_engine import evaluate
    return evaluate(submitted_query, expected_query, score_weight, target_db, active_db, nim,
                    grading_policy, setup_query, question_id)


def save_submission(user_id: int, question_id: int, submitted_query: str, is_correct=0,
                    score=0, eval_status="pending", feedback="", session_context=None, graded=True, active_database=None):
    with SQLITE_COMMIT_GUARD.get()(), closing(get_sqlite_conn()) as conn, conn:
        conn.execute("BEGIN IMMEDIATE;")
        question = conn.execute("SELECT * FROM questions WHERE id=?", (question_id,)).fetchone()
        if question is None:
            raise StudentAccessError("Soal tidak ditemukan.", 404, "question_changed")
        if session_context:
            user = require_student_access(session_context["nim"], session_context["session_token"],
                session_context["ip_address"], active=True, category=True,
                revision=session_context["exam_revision"], conn=conn)
            if user["id"] != user_id or question["category"] != user["selected_category"]:
                raise StudentAccessError("Soal tidak sesuai paket sesi Anda.", 403, "package_changed")
            if session_context.get("question_revision", question["question_revision"]) != question["question_revision"]:
                raise StudentAccessError("Soal berubah selama penilaian. Kirim ulang setelah memuat soal terbaru.", 403, "question_changed")
        existing = conn.execute("SELECT * FROM submissions WHERE user_id=? AND question_id=?", (user_id, question_id)).fetchone()
        current = dict(existing) if existing else None
        previous_score = int(current["score"] or 0) if current else 0
        attempt_score = int(score or 0) if graded else None
        attempt_correct = int(bool(is_correct)) if graded else None
        attempt = conn.execute("""INSERT INTO submission_attempts
            (user_id,question_id,submitted_query,graded,is_correct,score,eval_status,feedback,question_revision)
            VALUES (?,?,?,?,?,?,?,?,?)""", (user_id, question_id, submitted_query, int(graded), attempt_correct,
            attempt_score, eval_status, feedback, question["question_revision"]))
        retained = bool(current and (not graded or (previous_score > attempt_score or (previous_score == attempt_score and bool(current["is_correct"]) and not attempt_correct))))
        if graded and not retained:
            conn.execute("""INSERT INTO submissions
                (user_id,question_id,submitted_query,is_correct,score,eval_status,feedback,question_revision)
                VALUES (?,?,?,?,?,?,?,?) ON CONFLICT(user_id,question_id) DO UPDATE SET
                submitted_query=excluded.submitted_query,submitted_at=CURRENT_TIMESTAMP,
                is_correct=excluded.is_correct,score=excluded.score,eval_status=excluded.eval_status,
                feedback=excluded.feedback,question_revision=excluded.question_revision""",
                (user_id, question_id, submitted_query, attempt_correct, attempt_score, eval_status, feedback, question["question_revision"]))
            current = dict(conn.execute("SELECT * FROM submissions WHERE user_id=? AND question_id=?", (user_id, question_id)).fetchone())
        latest = dict(conn.execute("SELECT * FROM submission_attempts WHERE id=?", (attempt.lastrowid,)).fetchone())
        import grading_queue
        queue_status = grading_queue.enqueue_pending(conn,latest,dict(question),active_database)
        if queue_status:
            latest["grading_job"] = {"status":queue_status}

        conn.execute("INSERT INTO exam_logs (user_id,action_type,details) VALUES (?,'submit',?)",
                     (user_id, f"Percobaan {attempt.lastrowid}, soal {question_id}, status {eval_status}; dinilai={bool(graded)}" + (f". Skor tertinggi {previous_score} Poin tetap dipertahankan" if retained else "")))
        return {"status": "success", "message": "Percobaan jawaban disimpan", "graded": graded,
                "score": current["score"] if current else None, "is_correct": bool(current["is_correct"]) if current else None,
                "score_retained": retained, "previous_score": previous_score, "new_score": attempt_score,
                "retained_query": current["submitted_query"] if current else "", "queue_status":queue_status,
                "best_submission": current, "latest_attempt": latest, "attempt_id": latest["id"]}


def get_student_submissions(user_id: int) -> Dict[int, Dict[str, Any]]:
    import grading_queue
    with closing(get_sqlite_conn()) as conn:
        result = {row["question_id"]: dict(row) for row in conn.execute("SELECT * FROM submissions WHERE user_id=?", (user_id,))}
        latest = conn.execute("""SELECT a.* FROM submission_attempts a WHERE a.user_id=? AND a.id IN
            (SELECT MAX(id) FROM submission_attempts WHERE user_id=? GROUP BY question_id)""", (user_id, user_id)).fetchall()
        for row in latest:
            result.setdefault(row["question_id"], {"submitted_query": "", "score": None, "is_correct": None})["latest_attempt"] = grading_queue.effective_attempt(conn,row)
        return result


def get_all_students_progress() -> List[Dict[str, Any]]:

    conn = get_sqlite_conn()

    cur = conn.cursor()



    # Calculate online status based on 60-second inactivity window
    query = """
    SELECT
        u.id,
        u.nim,
        u.name,
        u.ip_address,
        u.status AS raw_status,
        u.last_active,
        u.selected_category,
        COUNT(s.id) AS total_submitted,
        COALESCE(SUM(s.score), 0) AS total_score,
        COUNT(CASE WHEN s.is_correct = 1 THEN 1 END) AS correct_count,
        (
            SELECT COALESCE(SUM(score_weight), 0)
            FROM questions q
            WHERE q.category = NULLIF(u.selected_category, '') COLLATE NOCASE
        ) AS max_score,
        (
            SELECT COUNT(*)
            FROM questions q
            WHERE q.category = NULLIF(u.selected_category, '') COLLATE NOCASE
        ) AS total_questions,
        (
            SELECT COUNT(*)
            FROM exam_logs l
            WHERE l.user_id = u.id AND l.action_type = 'blur_tab'
        ) AS tab_switches
    FROM users u
    LEFT JOIN submissions s ON u.id = s.user_id AND s.question_id IN (
        SELECT q.id FROM questions q
        WHERE q.category = NULLIF(u.selected_category, '') COLLATE NOCASE
    )
    GROUP BY u.id
    ORDER BY total_score DESC, u.nim ASC;
    """

    cur.execute(query)

    rows = cur.fetchall()

    conn.close()



    result = []

    now = datetime.datetime.now(datetime.timezone.utc).replace(tzinfo=None)

    for r in rows:

        row_dict = dict(r)

        last_active = r["last_active"]

        is_live = False

        if last_active:

            try:

                # Handle sqlite timestamp

                dt = datetime.datetime.fromisoformat(last_active.replace("Z", ""))

                if (now - dt).total_seconds() < 90 and row_dict["raw_status"] == "online":

                    is_live = True

            except Exception:

                is_live = (row_dict["raw_status"] == "online")



        row_dict["is_active"] = is_live

        result.append(row_dict)

    return result



def log_student_event(context, action_type, details):
    allowed = {"paste_attempt","devtools_attempt","blur_tab","focus_tab","exit_fullscreen"}
    if action_type not in allowed or len(details) > 1000:
        raise ValueError("Peristiwa mahasiswa tidak valid.")
    clean_details = "".join(char for char in details if ord(char)>=32 or char in "\n\t")
    with closing(get_sqlite_conn()) as conn, conn:
        conn.execute("BEGIN IMMEDIATE")
        user = require_student_access(context["nim"], context["session_token"], context["ip_address"], active=True,
                                      category=True, revision=context["exam_revision"], conn=conn)
        conn.execute("INSERT INTO exam_logs(user_id,action_type,details) VALUES(?,?,?)",
                     (user["id"],action_type,"Laporan klien: " + clean_details))


def log_exam_event(user_id: Optional[int], action_type: str, details: str):

    conn = get_sqlite_conn()

    with conn:

        conn.execute(

            """

            INSERT INTO exam_logs (user_id, action_type, details)

            VALUES (?, ?, ?);

            """,

            (user_id, action_type, details)

        )

    conn.close()



def get_recent_exam_logs(limit: int = 50, offset: int = 0) -> Tuple[List[Dict[str, Any]], int]:
    """Returns paginated exam logs along with total count."""
    limit = max(1, min(int(limit), 200))
    offset = max(0, int(offset))
    conn = get_sqlite_conn()
    cur = conn.cursor()
    cur.execute("SELECT COUNT(*) FROM exam_logs;")
    total = cur.fetchone()[0]

    cur.execute(
        """
        SELECT l.id, l.user_id, u.nim, u.name, l.action_type, l.details, l.timestamp
        FROM exam_logs l
        LEFT JOIN users u ON l.user_id = u.id
        ORDER BY l.id DESC
        LIMIT ? OFFSET ?;
        """,
        (limit, offset)
    )
    rows = cur.fetchall()
    conn.close()
    return [dict(r) for r in rows], total



# ------------------------------------------------------------------------------

# Result Export: CSV and ZIP (.sql files per student)

# ------------------------------------------------------------------------------



def safe_csv_writer(output):
    writer = csv.writer(output)
    class Writer:
        def writerow(self, values):
            writer.writerow([("'" + value if isinstance(value, str) and value.lstrip(" \t\r\n").startswith(("=", "+", "-", "@")) else value) for value in values])
    return Writer()


def export_all_submissions_csv() -> str:
    conn = get_sqlite_conn()
    cur = conn.cursor()
    cur.execute(
        """
        SELECT
            u.nim,
            u.name,
            u.ip_address,
            q.question_number,
            q.title AS question_title,
            q.score_weight,
            s.score,
            s.is_correct,
            s.submitted_query,
            s.submitted_at
        FROM submissions s
        JOIN users u ON s.user_id = u.id
        JOIN questions q ON s.question_id = q.id
        ORDER BY u.nim ASC, q.question_number ASC;
        """
    )
    rows = [dict(row) for row in cur.fetchall()]
    import grading_queue
    attempts = conn.execute('''SELECT a.*, u.nim, u.name, u.ip_address,
        q.question_number, q.title AS question_title, q.score_weight
        FROM submission_attempts a JOIN users u ON u.id=a.user_id
        JOIN questions q ON q.id=a.question_id ORDER BY u.nim,q.question_number,a.id''').fetchall()
    for attempt in attempts:
        effective = grading_queue.effective_attempt(conn, attempt)
        rows.append({**dict(attempt), **effective, 'archive_kind': 'PERCOBAAN'})
    conn.close()

    output = io.StringIO()
    writer = safe_csv_writer(output)
    writer.writerow([
        "NIM", "Nama Mahasiswa", "IP Address",
        "Nomor Soal", "Judul Soal", "Bobot Nilai",
        "Nilai Diperoleh", "Status Evaluasi",
        "Query Jawaban", "Waktu Submit", "Jenis Rekaman", "ID Percobaan"
    ])
    for r in rows:
        pending = not r.get('graded', r['score'] is not None)
        status_text = "BELUM DINILAI" if pending else ("BENAR" if r["is_correct"] else "BELUM TEPAT")
        awarded_score = "" if pending else r["score"]
        writer.writerow([
            r["nim"], r["name"], r["ip_address"],
            r["question_number"], r["question_title"], r["score_weight"],
            awarded_score, status_text,
            r["submitted_query"], r["submitted_at"], r.get('archive_kind', 'NILAI TERBAIK'),
            r.get('id', '')
        ])
    return output.getvalue()



def export_submissions_zip_bytes() -> bytes:
    conn = get_sqlite_conn()
    cur = conn.cursor()
    cur.execute("SELECT id, nim, name, ip_address, selected_category FROM users ORDER BY nim ASC;")
    users = cur.fetchall()

    zip_buffer = io.BytesIO()
    summary_rows = []

    with zipfile.ZipFile(zip_buffer, "w", zipfile.ZIP_DEFLATED) as zip_file:
        zip_file.writestr('arsip_seluruh_percobaan.csv', export_all_submissions_csv().encode('utf-8-sig'))
        for u in users:
            user_id = u["id"]
            nim = u["nim"]
            name = re.sub(r'[^a-zA-Z0-9_-]', '_', u["name"])
            category = u["selected_category"] or ""
            package_label = category or "Belum memilih paket"

            cur.execute(
                """
                SELECT q.id, q.question_number, q.title, q.score_weight, s.submitted_query, s.submitted_at, s.score, s.is_correct
                FROM questions q
                LEFT JOIN submissions s ON q.id = s.question_id AND s.user_id = ?
                WHERE q.category = NULLIF(?, '') COLLATE NOCASE
                ORDER BY q.question_number ASC;
                """,
                (user_id, category)
            )
            q_rows = cur.fetchall()

            q_rows = [dict(row) for row in q_rows]
            for qr in q_rows:
                if qr['submitted_query'] is None:
                    latest = conn.execute('''SELECT * FROM submission_attempts
                        WHERE user_id=? AND question_id=? ORDER BY id DESC LIMIT 1''',
                        (user_id, qr['id'])).fetchone()
                    if latest:
                        qr.update(submitted_query=latest['submitted_query'], submitted_at=latest['submitted_at'], pending=True)
            user_total_score = sum((qr["score"] or 0) for qr in q_rows)
            user_max_score = sum((qr["score_weight"] or 0) for qr in q_rows)
            user_correct_count = sum(1 for qr in q_rows if qr["is_correct"])
            total_q = len(q_rows)
            pct = round((user_total_score / user_max_score * 100), 1) if user_max_score > 0 else 0.0

            summary_rows.append({
                "nim": nim,
                "name": u["name"],
                "ip_address": u["ip_address"] or "-",
                "category": package_label,
                "total_score": user_total_score,
                "max_score": user_max_score,
                "correct_count": user_correct_count,
                "total_q": total_q,
                "pct": pct
            })

            file_content = [
                f"-- ============================================================",
                f"-- LEMBAR JAWABAN UJIAN PRAKTIKUM BASIS DATA",
                f"-- NIM           : {nim}",
                f"-- Nama          : {u['name']}",
                f"-- Paket Soal    : {package_label}",
                f"-- Total Nilai   : {user_total_score} / {user_max_score} Poin ({pct}%)",
                f"-- Jawaban Benar : {user_correct_count} dari {total_q} Soal",
                f"-- Diekspor pada : {datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
                f"-- ============================================================\n"
            ]

            for qr in q_rows:
                q_num = qr["question_number"]
                q_title = qr["title"]
                q_weight = qr["score_weight"]
                ans = qr["submitted_query"] or "-- BELUM DIJAWAB"
                submit_time = qr["submitted_at"] or "-"
                score = 'Belum dinilai' if qr.get('pending') else (qr["score"] if qr["score"] is not None else 0)
                status_label = 'BELUM DINILAI' if qr.get('pending') else ("BENAR" if qr["is_correct"] else ("BELUM TEPAT" if qr["submitted_query"] else "BELUM DIJAWAB"))

                file_content.append(f"-- ------------------------------------------------------------")
                file_content.append(f"-- SOAL NO. {q_num}: {q_title} (Bobot: {q_weight} | Nilai Diperoleh: {score} | Status: {status_label})")
                file_content.append(f"-- Waktu Submit: {submit_time}")
                file_content.append(f"-- ------------------------------------------------------------")

                clean_ans = ans.strip()
                if not clean_ans.startswith("--") and not clean_ans.endswith(";"):
                    clean_ans += ";"
                file_content.append(f"{clean_ans}\n")

            import grading_queue
            for attempt in conn.execute('SELECT * FROM submission_attempts WHERE user_id=? ORDER BY id', (user_id,)):
                effective = grading_queue.effective_attempt(conn, attempt)
                if not effective['graded']:
                    file_content.extend([
                        f"-- PERCOBAAN {attempt['id']} | ID SOAL {attempt['question_id']} | BELUM DINILAI | Nilai: belum tersedia",
                        f"-- Waktu Submit: {attempt['submitted_at']}",
                        attempt['submitted_query'],
                        "\n",
                    ])
            sql_data = "\n".join(file_content)
            safe_nim = re.sub(r'[^a-zA-Z0-9_-]', '_', nim)
            if safe_nim != nim:
                safe_nim += f"_{user_id}"
            zip_file.writestr(f"{safe_nim}_{name}.sql", sql_data)

        # Generate rekap_nilai_ujian.csv inside the ZIP file
        summary_output = io.StringIO()
        csv_writer = safe_csv_writer(summary_output)
        csv_writer.writerow([
            "NIM", "Nama Mahasiswa", "IP Address", "Paket Soal",
            "Total Skor", "Skor Maksimal", "Soal Benar", "Total Soal", "Persentase Nilai (%)"
        ])
        for s in summary_rows:
            csv_writer.writerow([
                s["nim"], s["name"], s["ip_address"], s["category"],
                s["total_score"], s["max_score"], s["correct_count"], s["total_q"], f"{s['pct']}%"
            ])
        zip_file.writestr("rekap_nilai_ujian.csv", summary_output.getvalue().encode("utf-8-sig"))

    conn.close()
    zip_buffer.seek(0)
    return zip_buffer.getvalue()



def export_all_logs_csv() -> str:

    """Mengekspor seluruh riwayat log aktivitas ujian sistem ke format CSV."""

    conn = get_sqlite_conn()

    cur = conn.cursor()

    cur.execute(

        """

        SELECT

            l.id,

            l.timestamp,

            COALESCE(u.nim, '-') AS nim,

            COALESCE(u.name, 'Sistem / Pengawas') AS name,

            COALESCE(u.ip_address, '-') AS ip_address,

            l.action_type,

            COALESCE(l.details, '-') AS details

        FROM exam_logs l

        LEFT JOIN users u ON l.user_id = u.id

        ORDER BY l.id ASC;

        """

    )

    rows = cur.fetchall()

    conn.close()



    output = io.StringIO()

    writer = safe_csv_writer(output)

    writer.writerow([

        "ID Log",

        "Waktu",

        "NIM",

        "Nama Mahasiswa",

        "IP Address",

        "Tipe Aksi",

        "Detail Aktivitas"

    ])

    for r in rows:

        writer.writerow([

            r["id"],

            r["timestamp"],

            r["nim"],

            r["name"],

            r["ip_address"],

            r["action_type"],

            r["details"]

        ])

    return output.getvalue()



def export_tab_switches_csv() -> str:

    """

    Mengekspor rekapitulasi data perpindahan tab & fokus jendela mahasiswa ke format CSV.

    Menampilkan seluruh mahasiswa beserta jumlah peringatan dan riwayat waktu kejadian kronologis.

    """

    conn = get_sqlite_conn()

    cur = conn.cursor()



    cur.execute(

        """

        SELECT

            u.id,

            u.nim,

            u.name,

            u.ip_address,

            u.status,

            u.last_active,

            (

                SELECT COUNT(*)

                FROM exam_logs l

                WHERE l.user_id = u.id AND l.action_type = 'blur_tab'

            ) AS tab_switches

        FROM users u

        ORDER BY tab_switches DESC, u.nim ASC;

        """

    )

    users = cur.fetchall()



    output = io.StringIO()

    writer = safe_csv_writer(output)

    writer.writerow([

        "NIM",

        "Nama Mahasiswa",

        "IP Address",

        "Total Peringatan Tab",

        "Status Sesi",

        "Waktu Kejadian",

        "Aksi",

        "Detail Aktivitas"

    ])



    for u in users:

        user_id = u["id"]

        nim = u["nim"]

        name = u["name"]

        ip = u["ip_address"] or "-"

        switches_count = u["tab_switches"]

        status = u["status"] or "offline"



        cur.execute(

            """

            SELECT action_type, details, timestamp

            FROM exam_logs

            WHERE user_id = ? AND action_type IN ('blur_tab', 'focus_tab')

            ORDER BY id ASC;

            """,

            (user_id,)

        )

        logs = cur.fetchall()



        if logs:

            for l in logs:

                writer.writerow([

                    nim,

                    name,

                    ip,

                    switches_count,

                    status,

                    l["timestamp"],

                    l["action_type"],

                    l["details"]

                ])

        else:

            writer.writerow([

                nim,

                name,

                ip,

                0,

                status,

                "-",

                "NIHIL",

                "Tidak ada catatan perpindahan tab (tertib)"

            ])



    # Cek apakah ada log perpindahan tab tanpa user_id

    cur.execute(

        """

        SELECT action_type, details, timestamp

        FROM exam_logs

        WHERE user_id IS NULL AND action_type IN ('blur_tab', 'focus_tab')

        ORDER BY id ASC;

        """

    )

    anon_logs = cur.fetchall()

    for l in anon_logs:

        writer.writerow([

            "-",

            "Anonim / Sesi Tidak Valid",

            "-",

            "-",

            "-",

            l["timestamp"],

            l["action_type"],

            l["details"]

        ])



    conn.close()

    return output.getvalue()





# ==============================================================================

# PostgreSQL Exam Database (Sandbox & Execution Engine)

# ==============================================================================



_HARDENED_DATABASES = set()


def init_postgres_db() -> Tuple[bool, str]:
    if PG_ADMIN_USER == PG_STUDENT_USER or DEFAULT_EXAM_DB not in ALLOWED_MASTER_DBS:
        return False, "Konfigurasi role/database ujian tidak aman atau tidak didukung."
    try:
        close_pg_pools()
        with closing(get_pg_admin_connection("postgres")) as admin:
            admin.autocommit = True
            with admin.cursor() as cursor:
                sandbox_roles.harden_role(cursor, PG_STUDENT_USER, PG_STUDENT_PASS)
                for dbname in sorted(ALLOWED_MASTER_DBS):
                    cursor.execute("SELECT 1 FROM pg_database WHERE datname=%s", (dbname,))
                    if not cursor.fetchone():
                        cursor.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(dbname)))
        for dbname in sorted(ALLOWED_MASTER_DBS):
            with closing(get_pg_admin_connection(dbname)) as conn, conn:
                with conn.cursor() as cursor:
                    grant_student_permissions(cursor)
                    cursor.execute("SELECT count(*) FROM information_schema.tables WHERE table_schema='public' AND table_type='BASE TABLE'")
                    empty = cursor.fetchone()[0] == 0
            if empty:
                # Rebuilding missing master data is a new dataset, not a transparent restart.
                if get_exam_state() == "active":
                    set_exam_state("waiting")
                ok, message = reset_target_database(dbname)
                if not ok:
                    raise RuntimeError(message)
            _HARDENED_DATABASES.add(dbname)
        return True, "Database master read-only dan role sandbox terisolasi siap digunakan."
    except Exception as error:
        _HARDENED_DATABASES.clear()
        return False, f"Gagal menyiapkan PostgreSQL dengan aman: {sanitize_db_error_message(error)}"


def execute_pg_dump(cursor, sql_text: str):
    r"""
    Executes PostgreSQL SQL dump text supporting:
    - Standard SQL statements (DDL/DML)
    - Dollar-quoted blocks ($$ ... $$ or $tag$ ... $tag$)
    - COPY <table> [(cols)] FROM stdin; followed by tab-delimited data and \.
    - Gracefully skips unsupported extensions (e.g. pgvector) and benign permission issues
    """
    def strip_leading_comments(stmt: str) -> str:
        lines = stmt.splitlines()
        i = 0
        while i < len(lines):
            line = lines[i].strip()
            if not line or line.startswith('--'):
                i += 1
                continue
            break
        return '\n'.join(lines[i:]).strip()

    lines = sql_text.splitlines(keepends=True)
    i = 0
    n = len(lines)

    current_stmt_lines = []
    in_dollar_tag = None

    dollar_regex = re.compile(r'(\$[a-zA-Z0-9_]*\$)')
    copy_regex = re.compile(r'^\s*COPY\s+(.+?)\s+FROM\s+stdin\s*;?', re.IGNORECASE)

    def run_stmt(s: str):
        if not s:
            return
        try:
            cursor.execute(s)
        except Exception as e:
            err_msg = str(e).lower()
            if "extension" in err_msg or "vector" in err_msg or "film_embedding" in err_msg or "transaction_timeout" in err_msg or "unrecognized configuration parameter" in err_msg:
                pass
            elif "must be owner" in err_msg or "permission denied" in err_msg:
                pass
            else:
                raise

    while i < n:
        line = lines[i]

        # Check for COPY ... FROM stdin
        if not in_dollar_tag and copy_regex.match(line):
            if current_stmt_lines:
                stmt = strip_leading_comments(''.join(current_stmt_lines))
                current_stmt_lines = []
                run_stmt(stmt)

            copy_stmt = line.strip()
            if not copy_stmt.endswith(';'):
                copy_stmt += ';'
            data_lines = []
            i += 1
            while i < n:
                dline = lines[i]
                if dline.strip() == '\\.':
                    i += 1
                    break
                data_lines.append(dline)
                i += 1

            data_io = io.StringIO(''.join(data_lines))
            try:
                cursor.copy_expert(copy_stmt, data_io)
            except Exception as ce:
                err_ce = str(ce).lower()
                if "vector" in err_ce or "film_embedding" in err_ce:
                    pass
                else:
                    raise
            continue

        # Check for dollar quote tags
        matches = dollar_regex.findall(line)
        for tag in matches:
            if in_dollar_tag is None:
                in_dollar_tag = tag
            elif in_dollar_tag == tag:
                in_dollar_tag = None

        current_stmt_lines.append(line)

        # Statement finishes on ';' if outside dollar quote tag
        if in_dollar_tag is None:
            stripped = line.rstrip()
            if stripped.endswith(';'):
                stmt = strip_leading_comments(''.join(current_stmt_lines))
                current_stmt_lines = []
                run_stmt(stmt)

        i += 1

    if current_stmt_lines:
        stmt = strip_leading_comments(''.join(current_stmt_lines))
        run_stmt(stmt)


def grant_student_permissions(cursor):
    sandbox_roles.harden_database(cursor)


def reset_target_database(target_db: str = DEFAULT_EXAM_DB, custom_sql_text: Optional[str] = None) -> Tuple[bool, str]:
    import grading_queue
    target_db = (target_db or DEFAULT_EXAM_DB).strip()
    if target_db == "sandbox_pribadi":
        target_db = DEFAULT_EXAM_DB
    if target_db.lower() not in ALLOWED_MASTER_DBS:
        return False, "Target database tidak valid."
    with grading_queue.dataset_maintenance():
        return _reset_target_database_impl(target_db, custom_sql_text)


def _reset_target_database_impl(target_db: str = DEFAULT_EXAM_DB, custom_sql_text: Optional[str] = None) -> Tuple[bool, str]:
    """
    Resets the target PostgreSQL database to a clean initial state or executes a custom SQL dump.
    Supports COPY ... FROM stdin, dollar quotes, schema cleanup, and student_role permissions.
    """
    target_db = (target_db or DEFAULT_EXAM_DB).strip()
    if target_db == "sandbox_pribadi":
        target_db = DEFAULT_EXAM_DB
    if target_db.lower() not in ALLOWED_MASTER_DBS:
        return False, f"Target database '{target_db}' tidak valid. Pilihan yang diizinkan: {', '.join(sorted(ALLOWED_MASTER_DBS))}."

    admin_pass = PG_ADMIN_PASS or None
    conn = None
    try:
        conn = psycopg2.connect(
            dbname=target_db,
            user=PG_ADMIN_USER,
            password=admin_pass,
            host=PG_HOST,
            port=PG_PORT
        )
        conn.autocommit = True
        cur = conn.cursor()
        cur.execute("SET statement_timeout = 120000;")

        data_folder = os.path.join(BASE_DIR, "data")

        def _get_pagila_sql(fname: str) -> str:
            for sub in ["pagila-master", "pagila-0.10.1"]:
                candidate = os.path.join(data_folder, sub, fname)
                if os.path.exists(candidate):
                    return candidate
            return os.path.join(data_folder, fname)

        # Detect whether custom_sql_text is a DDL (schema) or data-only dump (Subtask 4.1)
        is_data_only = False
        if custom_sql_text:
            has_ddl = bool(re.search(r'\bCREATE\s+(?:OR\s+REPLACE\s+)?(?:TABLE|TYPE|DOMAIN|SCHEMA)\b', custom_sql_text, re.IGNORECASE))
            is_data_only = not has_ddl

        if is_data_only:
            # Data-only dump: DO NOT drop schema! Preserve existing tables.
            cur.execute("CREATE SCHEMA IF NOT EXISTS public;")
            cur.execute(sql.SQL("GRANT ALL ON SCHEMA public TO {};").format(sql.Identifier(PG_ADMIN_USER)))
            grant_student_permissions(cur)

            # Check if tables already exist in public schema
            cur.execute("SELECT COUNT(*) FROM information_schema.tables WHERE table_schema = 'public' AND table_type = 'BASE TABLE';")
            table_count = cur.fetchone()[0]

            # If public schema is currently empty (0 tables), ensure baseline schema is loaded first
            if table_count == 0:
                if target_db.lower() == "pagila":
                    schema_path = _get_pagila_sql("pagila-schema.sql")
                    if os.path.exists(schema_path):
                        with open(schema_path, "r", encoding="utf-8", errors="ignore") as f:
                            execute_pg_dump(cur, f.read())
                elif target_db.lower() == "classicmodels":
                    cm_path = os.path.join(data_folder, "classicmodels.sql")
                    if os.path.exists(cm_path):
                        with open(cm_path, "r", encoding="utf-8", errors="ignore") as f:
                            execute_pg_dump(cur, f.read())

            # Now execute the data-only custom dump into existing/prepared tables
            execute_pg_dump(cur, custom_sql_text)
        else:
            # Full schema dump or clean reset: drop and recreate public schema
            if target_db.lower() == "classicmodels":
                cur.execute("DROP SCHEMA IF EXISTS classicmodels CASCADE;")

            cur.execute("DROP SCHEMA IF EXISTS public CASCADE;")
            cur.execute("CREATE SCHEMA public;")
            cur.execute(sql.SQL("GRANT ALL ON SCHEMA public TO {};").format(sql.Identifier(PG_ADMIN_USER)))
            cur.execute("REVOKE CREATE ON SCHEMA public FROM PUBLIC;")
            cur.execute("SET search_path = public;")

            if custom_sql_text:
                execute_pg_dump(cur, custom_sql_text)
            else:
                if target_db.lower() == "pagila":
                    schema_path = _get_pagila_sql("pagila-schema.sql")
                    data_path = _get_pagila_sql("pagila-data.sql")
                    if os.path.exists(schema_path):
                        with open(schema_path, "r", encoding="utf-8", errors="ignore") as f:
                            execute_pg_dump(cur, f.read())
                    if os.path.exists(data_path):
                        with open(data_path, "r", encoding="utf-8", errors="ignore") as f:
                            execute_pg_dump(cur, f.read())
                elif target_db.lower() == "classicmodels":
                    cm_path = os.path.join(data_folder, "classicmodels.sql")
                    if os.path.exists(cm_path):
                        with open(cm_path, "r", encoding="utf-8", errors="ignore") as f:
                            execute_pg_dump(cur, f.read())

        # Ensure permissions and default privileges for student_role
        cur.execute("SET search_path = public;")
        grant_student_permissions(cur)

        # Invalidate connection pools for this DB
        with _PG_POOL_LOCK:
            if target_db in PG_DB_POOLS:
                try:
                    PG_DB_POOLS[target_db].closeall()
                except Exception:
                    pass
                del PG_DB_POOLS[target_db]

        cur.close()
        conn.close()

        invalidate_schema_cache(target_db)

        success_msg = f"Data dari file SQL berhasil dimasukkan ke tabel database '{target_db}'." if is_data_only else f"Database '{target_db}' berhasil di-reset dan data berhasil dimuat ulang."
        return True, success_msg
    except Exception as e:
        if conn and not conn.closed:
            conn.close()
        return False, f"Gagal mereset database '{target_db}': {e}"


def reset_classicmodels_database() -> Tuple[bool, str]:
    return reset_target_database("classicmodels")


def reset_pagila_database() -> Tuple[bool, str]:
    return reset_target_database("pagila")


def empty_target_database(target_db: str = DEFAULT_EXAM_DB) -> Tuple[bool, str]:
    import grading_queue
    if (target_db or DEFAULT_EXAM_DB).strip() not in ALLOWED_STUDENT_DBS:
        return False, "Target database tidak valid."
    with grading_queue.dataset_maintenance():
        return _empty_target_database_impl(target_db)


def _empty_target_database_impl(target_db: str = DEFAULT_EXAM_DB) -> Tuple[bool, str]:
    """
    Clears all tables, views, sequences, and types in the target PostgreSQL database (0 tables).
    Prepares a clean, empty public schema with student_role permissions without loading default datasets.
    """
    target_db = (target_db or DEFAULT_EXAM_DB).strip()
    if target_db == "sandbox_pribadi":
        target_db = DEFAULT_EXAM_DB
    if target_db.lower() not in ALLOWED_MASTER_DBS:
        return False, f"Target database '{target_db}' tidak valid. Pilihan yang diizinkan: {', '.join(sorted(ALLOWED_MASTER_DBS))}."

    admin_pass = PG_ADMIN_PASS or None
    conn = None
    try:
        conn = psycopg2.connect(
            dbname=target_db,
            user=PG_ADMIN_USER,
            password=admin_pass,
            host=PG_HOST,
            port=PG_PORT
        )
        conn.autocommit = True
        cur = conn.cursor()
        cur.execute("SET statement_timeout = 120000;")

        if target_db.lower() == "classicmodels":
            cur.execute("DROP SCHEMA IF EXISTS classicmodels CASCADE;")

        cur.execute("DROP SCHEMA IF EXISTS public CASCADE;")
        cur.execute("CREATE SCHEMA public;")
        cur.execute(sql.SQL("GRANT ALL ON SCHEMA public TO {};").format(sql.Identifier(PG_ADMIN_USER)))
        cur.execute("REVOKE CREATE ON SCHEMA public FROM PUBLIC;")
        cur.execute("SET search_path = public;")

        grant_student_permissions(cur)

        cur.close()
        conn.close()

        with _PG_POOL_LOCK:
            if target_db in PG_DB_POOLS:
                try:
                    PG_DB_POOLS[target_db].closeall()
                except Exception:
                    pass
                del PG_DB_POOLS[target_db]

        return True, f"Database '{target_db}' berhasil dikosongkan total (0 tabel)."
    except Exception as e:
        if conn and not conn.closed:
            conn.close()
        return False, f"Gagal mengosongkan database '{target_db}': {e}"


def get_pg_admin_connection(dbname: str = DEFAULT_EXAM_DB):

    """Returns an administrative connection to PostgreSQL."""

    target_db = DEFAULT_EXAM_DB if dbname == "sandbox_pribadi" else dbname

    conn = psycopg2.connect(

        dbname=target_db,

        user=PG_ADMIN_USER,

        password=PG_ADMIN_PASS or None,

        host=PG_HOST,

        port=PG_PORT,
        connect_timeout=5

    )

    return conn



# Multi-Database pools dictionary: { "pagila": pool, "classicmodels": pool }

_PG_POOL_LOCK = threading.Lock()



PG_DB_POOLS: Dict[str, pool.ThreadedConnectionPool] = {}
PG_STUDENT_POOL: Optional[pool.ThreadedConnectionPool] = None
PG_POOL_MAXCONN: int = int(os.environ.get("PG_POOL_MAXCONN", "50"))

class PoolTracker:
    def __init__(self):
        self.lock = threading.Lock()
        self.wait_times: List[float] = []
        self.exhausted_count: int = 0
        self.total_acquired: int = 0

    def record_acquisition(self, duration_sec: float):
        with self.lock:
            self.total_acquired += 1
            self.wait_times.append(duration_sec)
            if len(self.wait_times) > 100:
                self.wait_times.pop(0)

    def record_exhaustion(self):
        with self.lock:
            self.exhausted_count += 1

    def get_stats(self) -> Dict[str, Any]:
        with self.lock:
            avg_wait_ms = (sum(self.wait_times) / len(self.wait_times) * 1000.0) if self.wait_times else 0.0
            max_wait_ms = (max(self.wait_times) * 1000.0) if self.wait_times else 0.0
            return {
                "total_acquired": self.total_acquired,
                "avg_wait_ms": round(avg_wait_ms, 2),
                "max_wait_ms": round(max_wait_ms, 2),
                "exhausted_count": self.exhausted_count,
            }

_POOL_TRACKER = PoolTracker()

def acquire_pooled_connection(pool_inst: pool.ThreadedConnectionPool, dbname: str = "default"):
    """
    Acquires a connection from the ThreadedConnectionPool while measuring wait time.
    Gracefully handles pool exhaustion with a clear user-facing error message.
    """
    start_t = time.perf_counter()
    try:
        conn = pool_inst.getconn()
        duration = time.perf_counter() - start_t
        _POOL_TRACKER.record_acquisition(duration)
        if duration > 1.0:
            print(f"[WARN] Connection pool acquisition took {duration:.3f}s for db '{dbname}'")
        return conn
    except pool.PoolError as e:
        _POOL_TRACKER.record_exhaustion()
        print(f"[ERROR] Connection pool exhausted for db '{dbname}': {e}")
        raise RuntimeError("Koneksi database sibuk (connection pool penuh). Silakan coba kembali dalam beberapa detik.") from e

def get_pool_stats() -> Dict[str, Any]:
    """Returns runtime metrics for the PostgreSQL connection pool."""
    stats = _POOL_TRACKER.get_stats()
    stats["active_pools"] = list(PG_DB_POOLS.keys())
    return stats

def get_pg_student_pool_for_db(dbname: str = DEFAULT_EXAM_DB) -> pool.ThreadedConnectionPool:
    global PG_DB_POOLS
    target_dbname = DEFAULT_EXAM_DB if dbname == "sandbox_pribadi" else dbname
    if target_dbname not in _HARDENED_DATABASES:
        raise RuntimeError("Database ujian belum selesai diamankan. Hubungi pengawas.")

    if target_dbname not in PG_DB_POOLS or PG_DB_POOLS[target_dbname] is None or PG_DB_POOLS[target_dbname].closed:
        with _PG_POOL_LOCK:
            if target_dbname not in PG_DB_POOLS or PG_DB_POOLS[target_dbname] is None or PG_DB_POOLS[target_dbname].closed:
                PG_DB_POOLS[target_dbname] = pool.ThreadedConnectionPool(
                    minconn=2,
                    maxconn=PG_POOL_MAXCONN,
                    dbname=target_dbname,
                    user=PG_STUDENT_USER,
                    password=PG_STUDENT_PASS or None,
                    host=PG_HOST,
                    port=PG_PORT,
                    connect_timeout=5
                )
    return PG_DB_POOLS[target_dbname]



def get_pg_student_pool() -> pool.ThreadedConnectionPool:

    return get_pg_student_pool_for_db(DEFAULT_EXAM_DB)



def close_pg_pools():

    """Safely closes all pooled PostgreSQL connections across all databases."""

    global PG_DB_POOLS, PG_STUDENT_POOL

    for db_name, pool_inst in list(PG_DB_POOLS.items()):

        if pool_inst and not pool_inst.closed:

            try:

                pool_inst.closeall()

            except Exception:

                pass

    PG_DB_POOLS.clear()

    PG_STUDENT_POOL = None
    sandbox_roles.clear_cache()



def get_pg_student_connection(dbname: str = DEFAULT_EXAM_DB):
    target = DEFAULT_EXAM_DB if dbname == "sandbox_pribadi" else dbname
    if target not in _HARDENED_DATABASES:
        raise RuntimeError("Database ujian belum selesai diamankan. Hubungi pengawas.")
    return psycopg2.connect(dbname=target, user=PG_STUDENT_USER, password=PG_STUDENT_PASS or None,
                            host=PG_HOST, port=PG_PORT, connect_timeout=5)


def get_clean_student_schema_name(nim: Optional[str]) -> str:
    return sandbox_roles.identity(nim)[0]


def init_student_sandbox_schema(nim: Optional[str]) -> str:
    if DEFAULT_EXAM_DB not in _HARDENED_DATABASES:
        raise RuntimeError("Database ujian belum selesai diamankan. Hubungi pengawas.")
    return sandbox_roles.ensure_workspace(nim)[0]


def cleanup_student_sandbox_schema(nim: Optional[str]) -> bool:
    schema, role = sandbox_roles.identity(nim)
    try:
        sandbox_roles.forget_workspace(nim)
        with closing(get_pg_admin_connection()) as conn, conn:
            with conn.cursor() as cursor:
                cursor.execute("SET LOCAL statement_timeout = 5000")
                cursor.execute("SELECT pg_advisory_xact_lock(hashtext(%s))", ("sqlexam:database-acl:" + DEFAULT_EXAM_DB,))
                cursor.execute("SELECT pg_advisory_xact_lock(hashtext(%s))", ("sqlexam:workspace:" + schema,))
                legacy = "sandbox_" + re.sub(r"[^a-zA-Z0-9_]", "", str(nim).strip()).lower()
                legacy = legacy[:63]
                cursor.execute("SELECT 1 FROM pg_namespace WHERE nspname=%s", (legacy,))
                if legacy != schema and cursor.fetchone():
                    with closing(get_sqlite_conn()) as sqlite:
                        owners = [row[0] for row in sqlite.execute("SELECT nim FROM users")
                                  if ("sandbox_" + re.sub(r"[^a-zA-Z0-9_]", "", row[0]).lower())[:63] == legacy]
                    if len(owners) != 1 or owners[0].lower() != str(nim).strip().lower():
                        raise RuntimeError("Sandbox lama ambigu; cleanup dibatalkan tanpa menghapus data.")
                    cursor.execute(sql.SQL("DROP SCHEMA {} CASCADE").format(sql.Identifier(legacy)))
                cursor.execute(sql.SQL("DROP SCHEMA IF EXISTS {} CASCADE").format(sql.Identifier(schema)))
                cursor.execute("SELECT 1 FROM pg_roles WHERE rolname=%s", (role,))
                if cursor.fetchone():
                    cursor.execute(sql.SQL("REVOKE ALL ON DATABASE {} FROM {}").format(sql.Identifier(DEFAULT_EXAM_DB), sql.Identifier(role)))
                    cursor.execute(sql.SQL("DROP ROLE {}").format(sql.Identifier(role)))
        return True
    except Exception as error:
        print(f"Sandbox cleanup failed: {sanitize_db_error_message(error)}")
        return False


def split_sql_statements(sql_text: str) -> List[str]:
    return split_statements(sql_text)


ALLOWED_MASTER_DBS = {"pagila", "classicmodels"}
ALLOWED_STUDENT_DBS = {"pagila", "classicmodels", "sandbox_pribadi"}
MAX_STUDENT_QUERY_LENGTH = 10000
MAX_RESULT_ROWS = 500
MAX_OUTPUT_CELL_CHARS = 4000

def sanitize_db_error_message(err_obj: Any) -> str:
    """
    Sanitizes database error messages to prevent leaking connection strings,
    passwords, host IPs, internal filesystem paths, or system stack traces.
    """
    raw = str(err_obj)
    sensitive = [
        "password", "connection refused", "could not connect", "pool",
        "socket", "authentication failed", "peer authentication",
        "pg_hba.conf", "server closed the connection"
    ]
    if any(k in raw.lower() for k in sensitive):
        return "Terjadi kendala koneksi ke server database. Silakan coba beberapa saat lagi atau hubungi pengawas."
    clean = re.sub(r'[A-Za-z]:\[^\s:]+', '', raw)
    clean = re.sub(r'/[^\s:]+/[^\s:]+', '', clean)
    clean = clean.replace("LINE 1: ", "\nDi baris: ")
    return clean.strip()

def check_dangerous_sql(query_text: str, is_sandbox_student: bool = False) -> Optional[str]:
    try:
        prepare_sql(query_text, sandbox=is_sandbox_student)
    except SQLPolicyError as error:
        return f"Perintah berbahaya terdeteksi: {error}"
    except (ParseError, ValueError):
        pass
    return None


def execute_student_query(query_text: str, active_db: str = DEFAULT_EXAM_DB, nim: Optional[str] = None) -> Dict[str, Any]:
    from query_runner import execute
    return execute(query_text, active_db, nim)


def extract_table_name_from_ddl(ddl_sql: str) -> Optional[str]:

    """

    Extracts the targeted table name from a CREATE TABLE query.

    Handles optional IF NOT EXISTS, schema qualifiers, quotes, and backticks.

    """

    if not ddl_sql:

        return None

    match = re.search(

        r'CREATE\s+TABLE\s+(?:IF\s+NOT\s+EXISTS\s+)?(?:[`"]?[a-zA-Z0-9_]+[`"]?\.)?[`"]?([a-zA-Z0-9_]+)[`"]?',

        ddl_sql,

        re.IGNORECASE

    )

    return match.group(1).lower() if match else None



def extract_all_table_names_from_ddl(ddl_sql: str) -> List[str]:

    """

    Extracts all table names from one or multiple CREATE TABLE statements.

    Preserves declaration order and filters duplicates.

    """

    if not ddl_sql:

        return []

    matches = re.findall(

        r'CREATE\s+TABLE\s+(?:IF\s+NOT\s+EXISTS\s+)?(?:[`"]?[a-zA-Z0-9_]+[`"]?\.)?[`"]?([a-zA-Z0-9_]+)[`"]?',

        ddl_sql,

        re.IGNORECASE

    )

    seen = set()

    result = []

    for m in matches:

        m_clean = m.strip().lower()

        if m_clean not in seen:

            seen.add(m_clean)

            result.append(m.strip())

    return result



def inspect_table_catalog(

    schema_name: str,

    table_name: str,

    conn=None,

    dbname: str = DEFAULT_EXAM_DB

) -> Dict[str, Any]:

    """

    Introspects the exact PostgreSQL catalog structure of a table:

    - columns: name, data_type, udt_name, is_nullable, character_maximum_length, numeric_precision, numeric_scale, column_default

    - constraints: PRIMARY KEY, FOREIGN KEY, UNIQUE

    """

    should_close = False

    if conn is None:

        conn = get_pg_admin_connection(dbname=dbname)

        should_close = True



    try:

        cur = conn.cursor()

        cur.execute("""

            SELECT column_name, data_type, udt_name, is_nullable,

                   character_maximum_length, numeric_precision, numeric_scale,

                   column_default, ordinal_position

            FROM information_schema.columns

            WHERE table_schema = %s AND table_name = %s

            ORDER BY ordinal_position;

        """, (schema_name.lower(), table_name.lower()))

        col_rows = cur.fetchall()



        if not col_rows:

            return {

                "exists": False,

                "table_name": table_name,

                "schema": schema_name,

                "columns": [],

                "constraints": {"primary_key": [], "foreign_keys": [], "unique": []}

            }



        columns = []

        for r in col_rows:

            columns.append({

                "name": r[0],

                "data_type": r[1],

                "udt_name": r[2],

                "is_nullable": (r[3] == "YES"),

                "character_maximum_length": r[4],

                "numeric_precision": r[5],

                "numeric_scale": r[6],

                "column_default": r[7],

                "ordinal_position": r[8]

            })



        cur.execute("""

            SELECT tc.constraint_type, kcu.column_name, tc.constraint_name

            FROM information_schema.table_constraints tc

            JOIN information_schema.key_column_usage kcu

              ON tc.constraint_name = kcu.constraint_name

              AND tc.table_schema = kcu.table_schema

            WHERE tc.table_schema = %s AND tc.table_name = %s

            ORDER BY tc.constraint_type, kcu.ordinal_position;

        """, (schema_name.lower(), table_name.lower()))

        const_rows = cur.fetchall()



        pks = []

        fks = []

        uniques = []

        for ctype, cname, _ in const_rows:

            ctype_upper = ctype.upper()

            if ctype_upper == 'PRIMARY KEY' and cname not in pks:

                pks.append(cname)

            elif ctype_upper == 'FOREIGN KEY' and cname not in fks:

                fks.append(cname)

            elif ctype_upper == 'UNIQUE' and cname not in uniques:

                uniques.append(cname)



        cur.close()

        return {

            "exists": True,

            "table_name": table_name,

            "schema": schema_name,

            "columns": columns,

            "constraints": {

                "primary_key": pks,

                "foreign_keys": fks,

                "unique": uniques

            }

        }

    finally:

        if should_close and conn and not conn.closed:

            conn.close()



def compile_all_reference_ddls(
    expected_ddl_sql: str,
    dbname: str = DEFAULT_EXAM_DB
) -> Dict[str, Any]:
    """
    Compiles the lecturer's expected DDL queries inside an isolated temporary schema.
    Introspects the generated catalog metadata for all declared tables, and cleanly drops the temporary schema.
    Guarantees ZERO schema leaks.
    """
    exp_tables = extract_all_table_names_from_ddl(expected_ddl_sql)
    if not exp_tables:
        return {"_error": "Tidak dapat mendeteksi nama tabel dari kunci acuan DDL dosen."}

    temp_schema = f"_ref_compile_{uuid.uuid4().hex[:12]}"
    conn = get_pg_admin_connection(dbname=dbname)
    conn.autocommit = True
    cur = conn.cursor()

    tables_meta = {}
    try:
        cur.execute(f"CREATE SCHEMA {temp_schema};")
        cur.execute(f"SET search_path TO {temp_schema}, public;")
        cur.execute(expected_ddl_sql)

        for tbl in exp_tables:
            meta = inspect_table_catalog(temp_schema, tbl, conn=conn, dbname=dbname)
            tables_meta[tbl.lower()] = meta

        return tables_meta
    except Exception as e:
        return {"_error": f"Gagal mengeksekusi kunci acuan DDL dosen: {str(e)}"}
    finally:
        try:
            cur.execute(f"DROP SCHEMA IF EXISTS {temp_schema} CASCADE;")
        except Exception:
            pass
        cur.close()
        conn.close()

def compile_reference_ddl(
    expected_ddl_sql: str,
    dbname: str = DEFAULT_EXAM_DB
) -> Dict[str, Any]:
    """
    Compiles the lecturer's expected DDL query inside an isolated temporary schema.
    Introspects the generated catalog metadata, and cleanly drops the temporary schema.
    Guarantees ZERO schema leaks.
    """
    all_meta = compile_all_reference_ddls(expected_ddl_sql, dbname=dbname)
    if "_error" in all_meta:
        return {"exists": False, "error": all_meta["_error"]}
    first_tbl = extract_table_name_from_ddl(expected_ddl_sql)
    if first_tbl and first_tbl.lower() in all_meta:
        return all_meta[first_tbl.lower()]
    return list(all_meta.values())[0] if all_meta else {"exists": False, "error": "Metadata kosong."}



def normalize_type_label(col_info: Dict[str, Any]) -> str:

    """Returns a readable SQL type representation (e.g. VARCHAR(100), NUMERIC(12,2), INT)."""

    dtype = (col_info.get("data_type") or "").upper()

    udt = (col_info.get("udt_name") or "").lower()



    if dtype == "CHARACTER VARYING":

        max_len = col_info.get("character_maximum_length")

        return f"VARCHAR({max_len})" if max_len else "VARCHAR"

    elif dtype == "CHARACTER":

        max_len = col_info.get("character_maximum_length")

        return f"CHAR({max_len})" if max_len else "CHAR"

    elif dtype == "INTEGER" or udt in ("int4", "int"):

        return "INT"

    elif dtype == "SMALLINT" or udt in ("int2",):

        return "SMALLINT"

    elif dtype == "BIGINT" or udt in ("int8",):

        return "BIGINT"

    elif dtype == "NUMERIC":

        prec = col_info.get("numeric_precision")

        scale = col_info.get("numeric_scale")

        if prec is not None and scale is not None:

            return f"NUMERIC({prec},{scale})"

        elif prec is not None:

            return f"NUMERIC({prec})"

        return "NUMERIC"

    elif dtype == "BOOLEAN":

        return "BOOLEAN"

    elif dtype == "TEXT":

        return "TEXT"

    elif "TIMESTAMP" in dtype:

        return "TIMESTAMP"

    elif dtype == "DATE":

        return "DATE"

    return dtype



def compare_table_structures(

    expected_meta: Dict[str, Any],

    student_meta: Dict[str, Any]

) -> Tuple[bool, str, Dict[str, Any]]:

    """

    Compares the reference table structure with the student's table structure.

    Validates:

    1. Table existence

    2. Column presence (all expected columns exist)

    3. Column data types and lengths/precision

    4. PRIMARY KEY constraints (columns participating in PK)

    5. NOT NULL constraints

    Returns (is_correct, feedback_message, diagnostic_details).

    """

    table_name = expected_meta.get("table_name", "tabel")

    details = {

        "table_name": table_name,

        "table_exists": student_meta.get("exists", False),

        "missing_columns": [],

        "extra_columns": [],

        "type_mismatches": [],

        "pk_status": "ok",

        "nullability_mismatches": []

    }



    # 1. Existence check

    if not student_meta.get("exists", False):

        msg = f"Tabel '{table_name}' belum ditemukan di skema database pribadi Anda. Pastikan perintah CREATE TABLE berhasil dijalankan."

        return False, msg, details



    exp_cols = {c["name"].lower(): c for c in expected_meta.get("columns", [])}

    stud_cols = {c["name"].lower(): c for c in student_meta.get("columns", [])}



    # 2. Missing columns check
    missing = [c for c in exp_cols if c not in stud_cols]
    if missing:
        details["missing_columns"] = missing
        if EXAM_HINT_MODE == "guided":
            missing_names = ", ".join(f"'{exp_cols[m]['name']}'" for m in missing)
            msg = f"Struktur tabel '{table_name}' belum lengkap. Kolom berikut belum ditemukan: {missing_names}."
        else:
            msg = f"Struktur tabel '{table_name}' belum lengkap. Masih ada atribut kolom yang diminta pada spesifikasi soal yang belum didefinisikan ke dalam tabel."
        return False, msg, details

    # Extra columns detection (for diagnostic info)
    extra = [c for c in stud_cols if c not in exp_cols]
    details["extra_columns"] = extra

    # 3. Data type & length/precision compatibility check
    INT_FAMILY = {"int4", "int2", "integer", "smallint", "serial"}
    BIGINT_FAMILY = {"int8", "bigint", "bigserial"}

    for col_name, exp_c in exp_cols.items():
        stud_c = stud_cols[col_name]
        exp_dtype = (exp_c.get("data_type") or "").lower()
        stud_dtype = (stud_c.get("data_type") or "").lower()
        exp_udt = (exp_c.get("udt_name") or "").lower()
        stud_udt = (stud_c.get("udt_name") or "").lower()

        type_match = False
        if exp_udt and stud_udt and exp_udt == stud_udt:
            type_match = True
        elif exp_dtype == stud_dtype:
            type_match = True
        elif exp_udt in INT_FAMILY and stud_udt in INT_FAMILY:
            type_match = True
        elif exp_udt in BIGINT_FAMILY and stud_udt in BIGINT_FAMILY:
            type_match = True
        elif exp_dtype in ("character varying", "varchar") and stud_dtype in ("character varying", "varchar"):
            type_match = True

        if not type_match:
            exp_label = normalize_type_label(exp_c)
            stud_label = normalize_type_label(stud_c)
            mismatch_desc = f"Kolom '{stud_c['name']}': tipe data tidak sesuai (Diharapkan {exp_label}, tetapi ditemukan {stud_label})."
            details["type_mismatches"].append(mismatch_desc)
            if EXAM_HINT_MODE == "guided":
                msg = mismatch_desc
            else:
                msg = f"Terdapat tipe data tidak sesuai pada definisi atribut tabel '{table_name}'. Periksa kembali kesesuaian tipe data angka atau teks pada rancangan soal."
            return False, msg, details

        # For VARCHAR/CHAR, check maximum length if expected specifies one
        if exp_dtype in ("character varying", "character"):
            exp_len = exp_c.get("character_maximum_length")
            stud_len = stud_c.get("character_maximum_length")
            if exp_len is not None and stud_len is not None and exp_len != stud_len:
                mismatch_desc = f"Ukuran panjang kolom '{stud_c['name']}' tidak sesuai. Diharapkan {normalize_type_label(exp_c)}, namun ditemukan {normalize_type_label(stud_c)}."
                details["type_mismatches"].append(mismatch_desc)
                if EXAM_HINT_MODE == "guided":
                    msg = mismatch_desc
                else:
                    msg = f"Ukuran panjang kolom pada tabel '{table_name}' belum sesuai batas maksimal karakter yang diminta. Periksa kembali alokasi panjang karakter pada klausul VARCHAR/CHAR Anda."
                return False, msg, details

        # For NUMERIC/DECIMAL, check precision & scale if expected specifies
        if exp_dtype == "numeric":
            exp_scale = exp_c.get("numeric_scale")
            stud_scale = stud_c.get("numeric_scale")
            if exp_scale is not None and stud_scale is not None and exp_scale != stud_scale:
                mismatch_desc = f"Presisi skala desimal kolom '{stud_c['name']}' tidak sesuai. Diharapkan {normalize_type_label(exp_c)}, namun ditemukan {normalize_type_label(stud_c)}."
                details["type_mismatches"].append(mismatch_desc)
                if EXAM_HINT_MODE == "guided":
                    msg = mismatch_desc
                else:
                    msg = f"Presisi skala desimal kolom pada tabel '{table_name}' belum sesuai spesifikasi angka pada narasi soal."
                return False, msg, details

    # 4. Primary Key validation
    exp_pk_cols = [c.lower() for c in expected_meta.get("constraints", {}).get("primary_key", [])]
    stud_pk_cols = [c.lower() for c in student_meta.get("constraints", {}).get("primary_key", [])]

    if exp_pk_cols:
        if not stud_pk_cols:
            details["pk_status"] = "missing"
            if EXAM_HINT_MODE == "guided":
                pk_names = ", ".join(f"'{exp_cols.get(p, {}).get('name', p)}'" for p in exp_pk_cols)
                msg = f"Constraint PRIMARY KEY belum didefinisikan pada tabel '{table_name}'. Diharapkan kolom primary key: {pk_names}."
            else:
                msg = f"Constraint PRIMARY KEY belum didefinisikan pada tabel '{table_name}'. Pastikan identitas unik entitas telah ditentukan sebagai primary key."
            return False, msg, details
        elif set(exp_pk_cols) != set(stud_pk_cols):
            details["pk_status"] = "mismatch"
            if EXAM_HINT_MODE == "guided":
                exp_pk_names = ", ".join(f"'{exp_cols.get(p, {}).get('name', p)}'" for p in exp_pk_cols)
                stud_pk_names = ", ".join(f"'{stud_cols.get(p, {}).get('name', p)}'" for p in stud_pk_cols)
                msg = f"Definisi PRIMARY KEY pada tabel '{table_name}' tidak sesuai. Diharapkan ({exp_pk_names}), namun ditemukan ({stud_pk_names})."
            else:
                msg = f"Definisi batasan constraint PRIMARY KEY pada tabel '{table_name}' belum sesuai rancangan unik entitas. Periksa kembali pemilihan kolom primary key Anda."
            return False, msg, details

    # 5. NOT NULL constraint validation (for non-PK columns)
    for col_name, exp_c in exp_cols.items():
        if col_name in exp_pk_cols:
            continue
        if not exp_c.get("is_nullable", True):
            stud_c = stud_cols.get(col_name)
            if stud_c and stud_c.get("is_nullable", True):
                details["nullability_mismatches"].append(col_name)
                if EXAM_HINT_MODE == "guided":
                    msg = f"Kolom '{stud_c['name']}' pada tabel '{table_name}' seharusnya memiliki constraint NOT NULL (tidak boleh bernilai NULL)."
                else:
                    msg = f"Terdapat atribut wajib pada tabel '{table_name}' yang belum dipasangi constraint NOT NULL. Pastikan kolom yang wajib diisi tidak mengizinkan nilai NULL."
                return False, msg, details

    # 6. UNIQUE constraint validation (for non-PK columns)
    exp_uniques = [c.lower() for c in expected_meta.get("constraints", {}).get("unique", [])]
    stud_uniques = [c.lower() for c in student_meta.get("constraints", {}).get("unique", [])]
    exp_non_pk_uniques = [c for c in exp_uniques if c not in exp_pk_cols]
    for u_col in exp_non_pk_uniques:
        if u_col not in stud_uniques:
            details["unique_status"] = "missing"
            if EXAM_HINT_MODE == "guided":
                u_name = exp_cols.get(u_col, {}).get("name", u_col)
                msg = f"Kolom '{u_name}' pada tabel '{table_name}' seharusnya memiliki constraint UNIQUE (tidak boleh ada nilai duplikat)."
            else:
                msg = f"Tabel '{table_name}' belum memiliki constraint UNIQUE yang dipersyaratkan. Pastikan atribut kontak/data yang tidak boleh bernilai ganda telah dipasangi batasan unik."
            return False, msg, details

    # 7. FOREIGN KEY constraint validation
    exp_fks = [c.lower() for c in expected_meta.get("constraints", {}).get("foreign_keys", [])]
    stud_fks = [c.lower() for c in student_meta.get("constraints", {}).get("foreign_keys", [])]
    for fk_col in exp_fks:
        if fk_col not in stud_fks:
            details["fk_status"] = "missing"
            if EXAM_HINT_MODE == "guided":
                fk_name = exp_cols.get(fk_col, {}).get("name", fk_col)
                msg = f"Kolom '{fk_name}' pada tabel '{table_name}' seharusnya memiliki constraint FOREIGN KEY yang mereferensikan tabel induk."
            else:
                msg = f"Relasi integritas referensial constraint FOREIGN KEY pada tabel '{table_name}' belum terpasang dengan benar. Periksa kembali foreign key yang mengaitkan ke tabel induk."
            return False, msg, details

    msg = f"Sempurna! Struktur tabel '{table_name}' (kolom, tipe data, NOT NULL, PRIMARY KEY, UNIQUE, dan FOREIGN KEY) 100% valid sesuai spesifikasi acuan dosen."
    return True, msg, details





_SCHEMA_CACHE: Dict[str, Dict[str, List[Dict[str, Any]]]] = {}
_SCHEMA_CACHE_LOCK = threading.Lock()

def invalidate_schema_cache(db_name: Optional[str] = None):
    """Invalidates the in-memory schema metadata cache."""
    with _SCHEMA_CACHE_LOCK:
        if db_name:
            _SCHEMA_CACHE.pop(db_name.strip().lower(), None)
        else:
            _SCHEMA_CACHE.clear()

def get_database_schema(db_name: str = DEFAULT_EXAM_DB, nim: Optional[str] = None) -> Dict[str, List[Dict[str, Any]]]:
    """
    Introspects tables and columns dynamically with in-memory caching.
    - If db_name == 'sandbox_pribadi', inspects the student's hashed personal schema.
    - Otherwise, inspects public tables of the selected PostgreSQL database (pagila, classicmodels, etc.) and caches results.
    Returns: { "table_name": [{"name": col, "type": type, "nullable": bool, "pk": bool}] }
    """
    schema_info = {}
    clean_db = (db_name or DEFAULT_EXAM_DB).strip().lower().split('(')[0].strip()
    if clean_db not in ALLOWED_STUDENT_DBS:
        clean_db = DEFAULT_EXAM_DB

    is_sandbox = (clean_db == "sandbox_pribadi")

    if not is_sandbox:
        with _SCHEMA_CACHE_LOCK:
            if clean_db in _SCHEMA_CACHE:
                return _SCHEMA_CACHE[clean_db]

    conn = None

    try:

        if is_sandbox:

            schema_name = get_clean_student_schema_name(nim)

            conn = get_pg_admin_connection(dbname=DEFAULT_EXAM_DB)

            cur = conn.cursor()

            cur.execute("""

                SELECT

                    c.table_name,

                    c.column_name,

                    c.data_type,

                    c.is_nullable,

                    CASE WHEN tc.constraint_type = 'PRIMARY KEY' THEN 1 ELSE 0 END AS is_pk,

                    c.table_schema

                FROM information_schema.columns c

                JOIN information_schema.tables t

                    ON c.table_name = t.table_name

                    AND c.table_schema = t.table_schema

                LEFT JOIN information_schema.key_column_usage kcu

                    ON c.table_name = kcu.table_name

                    AND c.column_name = kcu.column_name

                    AND c.table_schema = kcu.table_schema

                LEFT JOIN information_schema.table_constraints tc

                    ON kcu.constraint_name = tc.constraint_name

                    AND kcu.table_schema = tc.table_schema

                    AND tc.constraint_type = 'PRIMARY KEY'

                WHERE c.table_schema = %s

                  AND t.table_type = 'BASE TABLE'

                  AND c.table_name NOT IN (SELECT relname FROM pg_class WHERE relispartition = true)

                ORDER BY c.table_name, c.ordinal_position;

            """, (schema_name,))

        else:

            conn = get_pg_admin_connection(dbname=clean_db)

            cur = conn.cursor()

            cur.execute("""

                SELECT

                    c.table_name,

                    c.column_name,

                    c.data_type,

                    c.is_nullable,

                    CASE WHEN tc.constraint_type = 'PRIMARY KEY' THEN 1 ELSE 0 END AS is_pk,

                    c.table_schema

                FROM information_schema.columns c

                JOIN information_schema.tables t

                    ON c.table_name = t.table_name

                    AND c.table_schema = t.table_schema

                LEFT JOIN information_schema.key_column_usage kcu

                    ON c.table_name = kcu.table_name

                    AND c.column_name = kcu.column_name

                    AND c.table_schema = kcu.table_schema

                LEFT JOIN information_schema.table_constraints tc

                    ON kcu.constraint_name = tc.constraint_name

                    AND kcu.table_schema = tc.table_schema

                    AND tc.constraint_type = 'PRIMARY KEY'

                WHERE c.table_schema = 'public'

                  AND t.table_type = 'BASE TABLE'

                  AND c.table_name NOT IN (SELECT relname FROM pg_class WHERE relispartition = true)

                ORDER BY c.table_schema, c.table_name, c.ordinal_position;

            """)



        rows = cur.fetchall()

        for r in rows:

            tbl = r[0]

            col = r[1]

            dtype = r[2]

            nullable = (r[3] == "YES")

            is_pk = bool(r[4])



            if tbl not in schema_info:

                schema_info[tbl] = []



            if not any(item["name"] == col for item in schema_info[tbl]):

                schema_info[tbl].append({

                    "name": col,

                    "type": dtype,

                    "nullable": nullable,

                    "pk": is_pk

                })

        cur.close()
        conn.close()

        if not is_sandbox and schema_info:
            with _SCHEMA_CACHE_LOCK:
                _SCHEMA_CACHE[clean_db] = schema_info

    except Exception as e:
        if conn and not conn.closed:
            conn.close()
        print(f"Error inspecting schema for db '{clean_db}': {e}")

    return schema_info


def get_table_data(db_name: str, table_name: str, limit: int = 50, offset: int = 0) -> Dict[str, Any]:
    """
    Fetches actual row data from a specific table in PostgreSQL for admin data inspection.
    Safely escapes identifiers, counts total rows, and serializes datatypes to JSON.
    """
    clean_db = (db_name or DEFAULT_EXAM_DB).strip().lower().split('(')[0].strip()
    if clean_db not in ALLOWED_STUDENT_DBS:
        clean_db = DEFAULT_EXAM_DB

    clean_tbl = (table_name or "").strip().lower()
    if not re.match(r'^[a-z0-9_]+$', clean_tbl):
        raise ValueError(f"Nama tabel '{table_name}' tidak valid.")

    limit = max(1, min(int(limit), 200))
    offset = max(0, int(offset))

    conn = get_pg_admin_connection(dbname=clean_db)
    try:
        cur = conn.cursor()
        count_query = sql.SQL("SELECT COUNT(*) FROM {};").format(sql.Identifier(clean_tbl))
        cur.execute(count_query)
        total_rows = cur.fetchone()[0]

        data_query = sql.SQL("SELECT * FROM {} LIMIT %s OFFSET %s;").format(sql.Identifier(clean_tbl))
        cur.execute(data_query, (limit, offset))

        col_names = [desc[0] for desc in cur.description] if cur.description else []
        raw_rows = cur.fetchall()

        serialized_rows = []
        for row in raw_rows:
            formatted_row = []
            for cell in row:
                if cell is None:
                    formatted_row.append(None)
                elif isinstance(cell, bool):
                    formatted_row.append(cell)
                elif isinstance(cell, int):
                    formatted_row.append(cell)
                elif isinstance(cell, decimal.Decimal):
                    formatted_row.append(str(cell))
                elif isinstance(cell, float):
                    formatted_row.append(cell)
                elif isinstance(cell, (datetime.datetime, datetime.date, datetime.time, dt_time)):
                    formatted_row.append(str(cell))
                elif isinstance(cell, (bytes, memoryview)):
                    formatted_row.append(f"<binary {len(cell)} bytes>")
                elif isinstance(cell, (dict, list)):
                    formatted_row.append(json.dumps(cell))
                else:
                    s_val = str(cell)
                    if len(s_val) > 1000:
                        s_val = s_val[:1000] + "..."
                    formatted_row.append(s_val)
            serialized_rows.append(formatted_row)

        cur.close()
        conn.close()

        return {
            "database": clean_db,
            "table": clean_tbl,
            "columns": col_names,
            "rows": serialized_rows,
            "total_rows": total_rows,
            "limit": limit,
            "offset": offset
        }
    except Exception as e:
        if conn and not conn.closed:
            conn.close()
        raise e


def get_database_fk_relations(db_name: str = DEFAULT_EXAM_DB, nim: Optional[str] = None) -> list:
    """Legacy ERD stub - ERD visualizer removed per Subtask 4.2."""
    return []


def get_classicmodels_schema() -> Dict[str, List[Dict[str, Any]]]:
    """Backward compatibility wrapper."""
    return get_database_schema(db_name="classicmodels")


def get_classicmodels_fk_relations() -> list:
    """Legacy ERD stub - ERD visualizer removed per Subtask 4.2."""
    return []



def verify_system_health() -> Dict[str, Any]:
    health = {"sqlite":False, "postgres_admin":False, "postgres_student":False,
              "databases":{}, "details":{}}
    try:
        with closing(get_sqlite_conn()) as conn:
            version = conn.execute("PRAGMA user_version").fetchone()[0]
            conn.execute("SELECT COUNT(*) FROM questions").fetchone()
            health["sqlite"] = version == SQLITE_SCHEMA_VERSION
            health["details"]["sqlite_version"] = version
    except Exception as error:
        health["details"]["sqlite_error"] = type(error).__name__
    for name in sorted(ALLOWED_MASTER_DBS):
        checks = {"admin":False, "student":False}
        for identity, factory in (("admin",get_pg_admin_connection),("student",get_pg_student_connection)):
            try:
                with closing(factory(name)) as conn, conn.cursor() as cursor:
                    cursor.execute("SET statement_timeout=1000")
                    cursor.execute("SELECT 1")
                    checks[identity] = cursor.fetchone()[0] == 1
            except Exception as error:
                health["details"][name+"_"+identity+"_error"] = type(error).__name__
        health["databases"][name] = checks
    health["postgres_admin"] = bool(health["databases"]) and all(v["admin"] for v in health["databases"].values())
    health["postgres_student"] = bool(health["databases"]) and all(v["student"] for v in health["databases"].values())
    return health


# ==============================================================================

# Waiting Room & Exam State Management

# ==============================================================================



EXAM_STATES = {"waiting", "active", "finished"}


def get_exam_state() -> str:
    with closing(get_sqlite_conn()) as conn:
        row = conn.execute("SELECT value FROM system_settings WHERE key='exam_state'").fetchone()
        return row["value"] if row else "waiting"


def set_exam_state(state: str) -> None:
    with closing(get_sqlite_conn()) as conn, conn:
        conn.execute("BEGIN IMMEDIATE;")
        _set_exam_state(conn, state)


def _exam_readiness_snapshot(conn):
    import grading_queue
    from grading_engine import GRADE_SECONDS
    questions = [dict(row) for row in conn.execute("SELECT q.* FROM questions q JOIN exam_packages p ON UPPER(p.code)=UPPER(q.category) WHERE p.is_active=1 ORDER BY q.category,q.question_number,q.id")]
    packages = [row[0] for row in conn.execute("SELECT code FROM exam_packages WHERE is_active=1 ORDER BY code")]
    fields = ("id", "question_revision", "edit_revision", "category", "question_number", "expected_query",
              "target_database", "grading_policy", "setup_query", "score_weight")
    manifest = {"version": 1, "budget": GRADE_SECONDS, "packages": packages,
                "server": [PG_HOST, PG_PORT, DEFAULT_EXAM_DB],
                "dataset": grading_queue.setting(conn, "dataset_revision"),
                "questions": [[q[field] for field in fields] for q in questions]}
    fingerprint = hashlib.sha256(json.dumps(manifest, sort_keys=True).encode()).hexdigest()
    return questions, fingerprint


def _exam_readiness(conn):
    import grading_queue
    questions, fingerprint = _exam_readiness_snapshot(conn)
    result = {"ready": False, "question_count": len(questions), "validated_at": None,
              "message": "Jalankan Validasi Kesiapan Ujian sebelum memulai."}
    if grading_queue.setting(conn, "grading_maintenance", ""):
        result["message"] = "Dataset sedang dipelihara; validasi ulang setelah selesai."
        return result
    if not questions:
        result["message"] = "Belum ada soal dalam paket aktif."
        return result
    try:
        saved = json.loads(grading_queue.setting(conn, "exam_readiness", "null"))
    except (ValueError, TypeError):
        saved = None
    if isinstance(saved, dict) and saved.get("fingerprint") == fingerprint:
        result.update(ready=True, validated_at=saved.get("validated_at"),
                      message="Soal siap. Ujian dapat dimulai tanpa menjalankan ulang acuan.")
    elif saved:
        result["message"] = "Konfigurasi soal, paket, atau dataset berubah. Validasi kesiapan ulang."
    return result


def get_exam_readiness():
    with closing(get_sqlite_conn()) as conn:
        conn.execute("BEGIN")
        return _exam_readiness(conn)


def validate_exam_readiness():
    from grading_engine import validate_reference
    import grading_queue
    with closing(get_sqlite_conn()) as conn, conn:
        conn.execute("BEGIN IMMEDIATE")
        if grading_queue.setting(conn,"grading_maintenance",""):
            raise ValueError("Dataset sedang dipelihara; validasi belum dapat dijalankan.")
        state = conn.execute("SELECT value FROM system_settings WHERE key='exam_state'").fetchone()
        if state and state[0] == "active":
            raise ValueError("Ujian sedang aktif. Validasi kesiapan dilakukan sebelum ujian dimulai.")
        revision = conn.execute("SELECT value FROM system_settings WHERE key='exam_revision'").fetchone()
        revision = revision[0] if revision else None
        questions, fingerprint = _exam_readiness_snapshot(conn)
        conn.execute("DELETE FROM system_settings WHERE key='exam_readiness'")
    if not questions:
        raise ValueError("Belum ada soal dalam paket aktif.")
    validations = []
    for q in questions:
        try:
            result = validate_reference(q["expected_query"], q["target_database"], q["grading_policy"], q["setup_query"], q["id"])
            validations.append((result["status"], q["id"]))
        except Exception as error:
            reason = "Periksa acuan, prasyarat, dan batas evaluator."
            if isinstance(error, ValueError):
                reason = " ".join(str(error).split())[:300] or reason
            raise ValueError(f"Acuan soal {q['category']} nomor {q['question_number']} (ID {q['id']}) gagal divalidasi. {reason}") from error
    with closing(get_sqlite_conn()) as conn, conn:
        conn.execute("BEGIN IMMEDIATE")
        current_revision = conn.execute("SELECT value FROM system_settings WHERE key='exam_revision'").fetchone()
        _, current_fingerprint = _exam_readiness_snapshot(conn)
        if (grading_queue.setting(conn,"grading_maintenance","")
                or (current_revision[0] if current_revision else None) != revision or current_fingerprint != fingerprint):
            raise ValueError("Konfigurasi atau status ujian berubah selama validasi. Validasi kesiapan ulang.")
        conn.executemany("UPDATE questions SET reference_validation=? WHERE id=?", validations)
        saved = {"fingerprint": fingerprint, "validated_at": datetime.datetime.now(datetime.timezone.utc).isoformat()}
        conn.execute("INSERT INTO system_settings(key,value) VALUES('exam_readiness',?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (json.dumps(saved),))
        conn.execute("INSERT INTO exam_logs(action_type,details) VALUES('exam_validated',?)",
                     (f"Kesiapan {len(questions)} soal pada paket aktif berhasil divalidasi.",))
        return _exam_readiness(conn)


def start_exam_validated():
    with closing(get_sqlite_conn()) as conn, conn:
        conn.execute("BEGIN IMMEDIATE")
        state = conn.execute("SELECT value FROM system_settings WHERE key='exam_state'").fetchone()
        if state and state[0] == "active":
            return
        readiness = _exam_readiness(conn)
        if not readiness["ready"]:
            raise ValueError(readiness["message"])
        _set_exam_state(conn, "active")


def _set_exam_state(conn, state):
    if state not in EXAM_STATES:
        raise ValueError(f"Status ujian tidak valid: {state}")
    old = conn.execute("SELECT value FROM system_settings WHERE key='exam_state'").fetchone()
    if old and old["value"] == state:
        return
    conn.execute("INSERT INTO system_settings (key,value) VALUES ('exam_state',?) "
                 "ON CONFLICT(key) DO UPDATE SET value=excluded.value", (state,))
    conn.execute("INSERT INTO system_settings (key,value) VALUES ('exam_revision','1') "
                 "ON CONFLICT(key) DO UPDATE SET value=CAST(value AS INTEGER)+1")
    action, label = {
        "waiting": ("exam_prepared", "Ujian dalam persiapan."),
        "active": ("exam_started", "Ujian dimulai oleh pengawas."),
        "finished": ("exam_stopped", "Ujian diakhiri oleh pengawas."),
    }[state]
    conn.execute("INSERT INTO exam_logs (action_type,details) VALUES (?,?)", (action, label))


def get_waiting_room_participants() -> list:
    with closing(get_sqlite_conn()) as conn:
        rows = conn.execute("""SELECT nim,name,ip_address,last_active,status FROM users
            WHERE status='waiting' AND last_active >= datetime('now','-20 seconds')
            ORDER BY last_active DESC""").fetchall()
        return [dict(row) for row in rows]


def mark_student_waiting(nim: str, session_token: str, request_ip: str) -> None:
    _mark_student_location(nim, session_token, request_ip, "waiting")


def heartbeat_student_waiting(nim: str, session_token: str, request_ip: str) -> None:
    _mark_student_location(nim, session_token, request_ip, "waiting")


def mark_student_in_exam(nim: str, session_token: str, request_ip: str) -> None:
    _mark_student_location(nim, session_token, request_ip, "online")


def _mark_student_location(nim, session_token, request_ip, location):
    with closing(get_sqlite_conn()) as conn, conn:
        conn.execute("BEGIN IMMEDIATE;")
        user = require_student_access(nim, session_token, request_ip, active=location == "online", conn=conn)
        target = "online" if user["exam_state"] == "active" else "waiting"
        conn.execute("UPDATE users SET status=?,last_active=CURRENT_TIMESTAMP WHERE id=?", (target, user["id"]))
