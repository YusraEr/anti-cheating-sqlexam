import os
import sqlite3
import datetime
import re
import time
import zipfile
import io
import csv
from typing import Optional, Dict, Any, List, Tuple
import psycopg2
from psycopg2 import sql, extras

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
PG_DB = os.getenv("PG_DB", "classicmodels")
PG_ADMIN_USER = os.getenv("PG_ADMIN_USER", "postgres")
PG_ADMIN_PASS = os.getenv("PG_ADMIN_PASS", "")
PG_STUDENT_USER = os.getenv("PG_STUDENT_USER", "student_role")
PG_STUDENT_PASS = os.getenv("PG_STUDENT_PASS", "student123")

# ==============================================================================
# SQLite System Database Management
# ==============================================================================

def get_sqlite_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(SQLITE_DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON;")
    return conn

def init_system_db():
    conn = get_sqlite_conn()
    with conn:
        conn.executescript("""
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            nim TEXT UNIQUE NOT NULL,
            name TEXT NOT NULL,
            ip_address TEXT,
            session_token TEXT,
            status TEXT DEFAULT 'offline',
            last_active TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS questions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            question_number INTEGER NOT NULL UNIQUE,
            title TEXT NOT NULL,
            description TEXT NOT NULL,
            score_weight INTEGER DEFAULT 10,
            expected_query TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS submissions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            question_id INTEGER NOT NULL REFERENCES questions(id) ON DELETE CASCADE,
            submitted_query TEXT NOT NULL,
            submitted_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            status TEXT DEFAULT 'submitted',
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
        """)

        # Insert or sync default master key
        cur = conn.cursor()
        env_master_key = os.getenv("ADMIN_MASTER_KEY")
        cur.execute("SELECT value FROM system_settings WHERE key = 'admin_master_key';")
        existing_row = cur.fetchone()
        if not existing_row:
            initial_key = env_master_key.strip() if env_master_key else "admin123"
            cur.execute(
                "INSERT INTO system_settings (key, value) VALUES ('admin_master_key', ?);",
                (initial_key,)
            )
        elif env_master_key and env_master_key.strip():
            # Jika user mendefinisikan ADMIN_MASTER_KEY eksplisit via env/script, sinkronkan ke database
            cur.execute(
                "UPDATE system_settings SET value = ? WHERE key = 'admin_master_key';",
                (env_master_key.strip(),)
            )

        # Seed initial exam questions if empty
        cur.execute("SELECT COUNT(*) FROM questions;")
        if cur.fetchone()[0] == 0:
            seed_default_questions(conn)

    conn.close()

def get_admin_master_key() -> str:
    """Mengambil password master admin saat ini dari tabel system_settings."""
    conn = get_sqlite_conn()
    cur = conn.cursor()
    cur.execute("SELECT value FROM system_settings WHERE key = 'admin_master_key';")
    row = cur.fetchone()
    conn.close()
    return row["value"] if row else "admin123"

def update_admin_master_key(new_key: str) -> bool:
    """Memperbarui password master admin dan mencatatnya ke log aktivitas."""
    new_key = new_key.strip()
    conn = get_sqlite_conn()
    with conn:
        cur = conn.cursor()
        cur.execute(
            """
            INSERT INTO system_settings (key, value) VALUES ('admin_master_key', ?)
            ON CONFLICT(key) DO UPDATE SET value = excluded.value;
            """,
            (new_key,)
        )
        cur.execute(
            "INSERT INTO exam_logs (action_type, details) VALUES ('change_master_key', 'Password master admin berhasil diperbarui.');"
        )
    conn.close()
    return True

def seed_default_questions(conn: sqlite3.Connection):
    sample_questions = [
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
    cur = conn.cursor()
    cur.executemany(
        """
        INSERT INTO questions (question_number, title, description, score_weight, expected_query)
        VALUES (?, ?, ?, ?, ?);
        """,
        sample_questions
    )

# ------------------------------------------------------------------------------
# Student User & Session Management (IP Binding, Anti-Joki & Unique NIM)
# ------------------------------------------------------------------------------

def is_user_active(last_active_str: Optional[str], status: str, timeout_seconds: int = 90) -> bool:
    if status != "online" or not last_active_str:
        return False
    try:
        now = datetime.datetime.now(datetime.timezone.utc).replace(tzinfo=None)
        dt = datetime.datetime.fromisoformat(last_active_str.replace("Z", ""))
        return (now - dt).total_seconds() < timeout_seconds
    except Exception:
        return status == "online"

def login_student(nim: str, name: str, ip_address: str, session_token: str) -> Tuple[bool, Optional[Dict[str, Any]], Optional[str]]:
    nim = nim.strip()
    name = name.strip()

    if not nim or not name:
        return False, None, "NIM dan Nama Lengkap wajib diisi!"

    if not re.match(r'^[A-Za-z0-9_\-\.]+$', nim):
        return False, None, "Format NIM tidak valid. Gunakan huruf dan angka tanpa spasi."

    conn = get_sqlite_conn()
    try:
        cur = conn.cursor()

        # 1. Anti-Joki: Pastikan satu IP tidak menjalankan lebih dari 1 mahasiswa aktif sekaligus (kecuali localhost)
        if ip_address not in ("127.0.0.1", "localhost", "::1"):
            cur.execute(
                "SELECT * FROM users WHERE ip_address = ? AND LOWER(nim) != LOWER(?) AND status = 'online';",
                (ip_address, nim)
            )
            other_active_on_ip = cur.fetchall()
            for other_user in other_active_on_ip:
                if is_user_active(other_user["last_active"], other_user["status"]):
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
            user_status = existing_user["status"]
            last_active = existing_user["last_active"]

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
            active = is_user_active(last_active, user_status)
            if active:
                # Jika login dari perangkat/IP yang sama persis: izinkan melanjutkan sesi (misal refresh/tab tertutup)
                if old_ip == ip_address:
                    conn.execute(
                        """
                        UPDATE users
                        SET session_token = ?, status = 'online', last_active = CURRENT_TIMESTAMP
                        WHERE id = ?;
                        """,
                        (session_token, user_id)
                    )
                    conn.execute(
                        """
                        INSERT INTO exam_logs (user_id, action_type, details)
                        VALUES (?, 'resume_session', ?);
                        """,
                        (user_id, f"Mahasiswa {reg_nim} melanjutkan sesi pada IP {ip_address}")
                    )
                else:
                    # Perangkat/IP LAIN mencoba login dengan NIM yang SEDANG AKTIF!
                    # TOLAK login ini untuk melindungi mahasiswa yang sah yang sedang ujian
                    conn.execute(
                        """
                        INSERT INTO exam_logs (user_id, action_type, details)
                        VALUES (?, 'duplicate_nim_blocked', ?);
                        """,
                        (
                            user_id,
                            f"Percobaan login ganda NIM {reg_nim} dari IP {ip_address} DITOLAK karena sedang aktif di IP {old_ip}"
                        )
                    )
                    conn.commit()
                    return False, None, f"NIM {reg_nim} sedang aktif digunakan dalam sesi ujian pada perangkat lain (IP: {old_ip}). Setiap mahasiswa hanya dapat aktif pada 1 sesi ujian dengan NIM unik. Hubungi pengawas jika Anda perlu reset sesi."

            else:
                # 2c. Mahasiswa offline / direset pengawas / timeout: Izinkan login ulang dengan data yang cocok
                conn.execute(
                    """
                    UPDATE users
                    SET ip_address = ?, session_token = ?, status = 'online', last_active = CURRENT_TIMESTAMP
                    WHERE id = ?;
                    """,
                    (ip_address, session_token, user_id)
                )
                conn.execute(
                    """
                    INSERT INTO exam_logs (user_id, action_type, details)
                    VALUES (?, 'login', ?);
                    """,
                    (user_id, f"Mahasiswa {reg_nim} login kembali dari IP {ip_address}")
                )

        else:
            # 3. Registrasi Mahasiswa Baru dengan NIM unik
            cur.execute(
                """
                INSERT INTO users (nim, name, ip_address, session_token, status, last_active)
                VALUES (?, ?, ?, ?, 'online', CURRENT_TIMESTAMP);
                """,
                (nim, name, ip_address, session_token)
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

def validate_student_session(nim: str, session_token: str, request_ip: str) -> Tuple[bool, Optional[Dict[str, Any]], str]:
    conn = get_sqlite_conn()
    cur = conn.cursor()
    cur.execute("SELECT * FROM users WHERE LOWER(nim) = LOWER(?);", (nim,))
    user = cur.fetchone()
    conn.close()

    if not user:
        return False, None, "Pengguna tidak ditemukan."

    user_dict = dict(user)

    # Cek apakah sesi dihentikan atau di-reset oleh pengawas
    if user_dict.get("session_token") == "KICKED_BY_ADMIN":
        return False, user_dict, "Sesi ujian Anda telah dihentikan oleh pengawas."

    if user_dict.get("status") == "offline":
        return False, user_dict, "Sesi Anda telah berakhir atau berstatus offline."

    # Verifikasi token sesi unik
    if user_dict.get("session_token") != session_token:
        return False, user_dict, "NIM Anda sedang dibuka dari perangkat atau tab browser lain!"

    # Verifikasi IP binding
    if user_dict.get("ip_address") != request_ip:
        return False, user_dict, f"Alamat IP Anda berubah ({request_ip} vs {user_dict.get('ip_address')}). Silakan login ulang."

    return True, user_dict, "Valid"

def update_student_heartbeat(user_id: int):
    conn = get_sqlite_conn()
    with conn:
        conn.execute(
            """
            UPDATE users 
            SET last_active = CURRENT_TIMESTAMP, status = 'online'
            WHERE id = ?;
            """,
            (user_id,)
        )
    conn.close()

def logout_student(nim: str, session_token: str):
    conn = get_sqlite_conn()
    with conn:
        cur = conn.cursor()
        cur.execute("SELECT id FROM users WHERE LOWER(nim) = LOWER(?) AND session_token = ?;", (nim, session_token))
        row = cur.fetchone()
        if row:
            user_id = row["id"]
            conn.execute("UPDATE users SET status = 'offline', session_token = NULL WHERE id = ?;", (user_id,))
            conn.execute(
                "INSERT INTO exam_logs (user_id, action_type, details) VALUES (?, 'logout', 'Mahasiswa logout manual');",
                (user_id,)
            )
    conn.close()

def kick_student(nim: str) -> bool:
    conn = get_sqlite_conn()
    with conn:
        cur = conn.cursor()
        cur.execute(
            """
            UPDATE users 
            SET session_token = 'KICKED_BY_ADMIN', status = 'offline' 
            WHERE LOWER(nim) = LOWER(?);
            """,
            (nim,)
        )
        cur.execute(
            """
            INSERT INTO exam_logs (action_type, details)
            VALUES ('kick', ?);
            """,
            (f"Pengawas me-reset sesi untuk NIM {nim}",)
        )
    conn.close()
    return True

def stop_all_active_sessions() -> int:
    """Hentikan semua sesi mahasiswa yang sedang berlangsung sekaligus."""
    conn = get_sqlite_conn()
    with conn:
        cur = conn.cursor()
        cur.execute(
            """
            UPDATE users 
            SET session_token = 'KICKED_BY_ADMIN', status = 'offline'
            WHERE status = 'online' OR (session_token IS NOT NULL AND session_token != 'KICKED_BY_ADMIN');
            """
        )
        count = cur.rowcount
        cur.execute(
            """
            INSERT INTO exam_logs (action_type, details)
            VALUES ('stop_all_sessions', ?);
            """,
            (f"Pengawas menghentikan semua sesi ujian aktif ({count} sesi dinonaktifkan)",)
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

def get_all_questions(include_expected_query: bool = False) -> List[Dict[str, Any]]:
    conn = get_sqlite_conn()
    cur = conn.cursor()
    cur.execute("SELECT * FROM questions ORDER BY question_number ASC;")
    rows = cur.fetchall()
    conn.close()

    result = []
    for r in rows:
        item = dict(r)
        if not include_expected_query:
            item.pop("expected_query", None)
        result.append(item)
    return result

def get_question_by_id(question_id: int) -> Optional[Dict[str, Any]]:
    conn = get_sqlite_conn()
    cur = conn.cursor()
    cur.execute("SELECT * FROM questions WHERE id = ?;", (question_id,))
    row = cur.fetchone()
    conn.close()
    return dict(row) if row else None

def save_question(
    question_number: int,
    title: str,
    description: str,
    score_weight: int,
    expected_query: Optional[str] = None,
    question_id: Optional[int] = None
) -> int:
    conn = get_sqlite_conn()
    with conn:
        cur = conn.cursor()
        if question_id:
            cur.execute(
                """
                UPDATE questions
                SET question_number = ?, title = ?, description = ?, score_weight = ?, expected_query = ?
                WHERE id = ?;
                """,
                (question_number, title, description, score_weight, expected_query, question_id)
            )
            ret_id = question_id
        else:
            cur.execute(
                """
                INSERT INTO questions (question_number, title, description, score_weight, expected_query)
                VALUES (?, ?, ?, ?, ?);
                """,
                (question_number, title, description, score_weight, expected_query)
            )
            ret_id = cur.lastrowid
    conn.close()
    return ret_id

def delete_question(question_id: int):
    conn = get_sqlite_conn()
    with conn:
        conn.execute("DELETE FROM questions WHERE id = ?;", (question_id,))
    conn.close()

# ------------------------------------------------------------------------------
# Submissions Management
# ------------------------------------------------------------------------------

def save_submission(user_id: int, question_id: int, submitted_query: str) -> Dict[str, Any]:
    conn = get_sqlite_conn()
    with conn:
        cur = conn.cursor()
        cur.execute(
            """
            INSERT INTO submissions (user_id, question_id, submitted_query, submitted_at, status)
            VALUES (?, ?, ?, CURRENT_TIMESTAMP, 'submitted')
            ON CONFLICT(user_id, question_id) DO UPDATE SET
                submitted_query = excluded.submitted_query,
                submitted_at = CURRENT_TIMESTAMP,
                status = 'submitted';
            """,
            (user_id, question_id, submitted_query)
        )
        cur.execute(
            """
            INSERT INTO exam_logs (user_id, action_type, details)
            VALUES (?, 'submit', ?);
            """,
            (user_id, f"Jawaban untuk Soal ID {question_id} berhasil disubmit")
        )
    conn.close()
    return {"status": "success", "message": "Jawaban berhasil disimpan"}

def get_student_submissions(user_id: int) -> Dict[int, Dict[str, Any]]:
    conn = get_sqlite_conn()
    cur = conn.cursor()
    cur.execute("SELECT * FROM submissions WHERE user_id = ?;", (user_id,))
    rows = cur.fetchall()
    conn.close()
    return {r["question_id"]: dict(r) for r in rows}

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
        COUNT(s.id) AS total_submitted,
        (
            SELECT COUNT(*) 
            FROM exam_logs l 
            WHERE l.user_id = u.id AND l.action_type = 'blur_tab'
        ) AS tab_switches
    FROM users u
    LEFT JOIN submissions s ON u.id = s.user_id
    GROUP BY u.id
    ORDER BY u.nim ASC;
    """
    cur.execute(query)
    rows = cur.fetchall()
    conn.close()

    result = []
    now = datetime.datetime.utcnow()
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

def get_recent_exam_logs(limit: int = 40) -> List[Dict[str, Any]]:
    conn = get_sqlite_conn()
    cur = conn.cursor()
    cur.execute(
        """
        SELECT l.id, l.user_id, u.nim, u.name, l.action_type, l.details, l.timestamp
        FROM exam_logs l
        LEFT JOIN users u ON l.user_id = u.id
        ORDER BY l.id DESC
        LIMIT ?;
        """,
        (limit,)
    )
    rows = cur.fetchall()
    conn.close()
    return [dict(r) for r in rows]

# ------------------------------------------------------------------------------
# Result Export: CSV and ZIP (.sql files per student)
# ------------------------------------------------------------------------------

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
            s.submitted_query,
            s.submitted_at
        FROM submissions s
        JOIN users u ON s.user_id = u.id
        JOIN questions q ON s.question_id = q.id
        ORDER BY u.nim ASC, q.question_number ASC;
        """
    )
    rows = cur.fetchall()
    conn.close()

    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow([
        "NIM", "Nama Mahasiswa", "IP Address", 
        "Nomor Soal", "Judul Soal", "Bobot Nilai", 
        "Query Jawaban", "Waktu Submit"
    ])
    for r in rows:
        writer.writerow([
            r["nim"], r["name"], r["ip_address"],
            r["question_number"], r["question_title"], r["score_weight"],
            r["submitted_query"], r["submitted_at"]
        ])
    return output.getvalue()

def export_submissions_zip_bytes() -> bytes:
    conn = get_sqlite_conn()
    cur = conn.cursor()
    cur.execute("SELECT id, nim, name FROM users ORDER BY nim ASC;")
    users = cur.fetchall()

    zip_buffer = io.BytesIO()
    with zipfile.ZipFile(zip_buffer, "w", zipfile.ZIP_DEFLATED) as zip_file:
        for u in users:
            user_id = u["id"]
            nim = u["nim"]
            name = re.sub(r'[^a-zA-Z0-9_-]', '_', u["name"])
            
            cur.execute(
                """
                SELECT q.question_number, q.title, q.score_weight, s.submitted_query, s.submitted_at
                FROM questions q
                LEFT JOIN submissions s ON q.id = s.question_id AND s.user_id = ?
                ORDER BY q.question_number ASC;
                """,
                (user_id,)
            )
            q_rows = cur.fetchall()

            file_content = [
                f"-- ============================================================",
                f"-- Lembar Jawaban Ujian Praktikum Basis Data",
                f"-- NIM  : {nim}",
                f"-- Nama : {u['name']}",
                f"-- Diekspor pada: {datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
                f"-- ============================================================\n"
            ]

            for qr in q_rows:
                q_num = qr["question_number"]
                q_title = qr["title"]
                q_weight = qr["score_weight"]
                ans = qr["submitted_query"] or "-- BELUM DIJAWAB"
                submit_time = qr["submitted_at"] or "-"

                file_content.append(f"-- ------------------------------------------------------------")
                file_content.append(f"-- SOAL NO. {q_num}: {q_title} (Bobot: {q_weight})")
                file_content.append(f"-- Waktu Submit: {submit_time}")
                file_content.append(f"-- ------------------------------------------------------------")
                
                clean_ans = ans.strip()
                if not clean_ans.startswith("--") and not clean_ans.endswith(";"):
                    clean_ans += ";"
                file_content.append(f"{clean_ans}\n")

            sql_data = "\n".join(file_content)
            zip_file.writestr(f"{nim}_{name}.sql", sql_data)

    conn.close()
    zip_buffer.seek(0)
    return zip_buffer.getvalue()

# ==============================================================================
# PostgreSQL Exam Database (Sandbox & Execution Engine)
# ==============================================================================

def init_postgres_db() -> Tuple[bool, str]:
    """
    Menginisialisasi PostgreSQL secara otomatis pada run pertama kali:
    1. Mengecek koneksi ke server PostgreSQL.
    2. Membuat database 'classicmodels' jika belum ada.
    3. Membuat role 'student_role' dengan izin read-only jika belum ada.
    4. Mengisi dataset awal dari data/classicmodels.sql jika database masih kosong.
    """
    admin_pass = PG_ADMIN_PASS or None
    
    # 1. Coba koneksi ke PostgreSQL
    conn = None
    try:
        conn = psycopg2.connect(
            dbname=PG_DB,
            user=PG_ADMIN_USER,
            password=admin_pass,
            host=PG_HOST,
            port=PG_PORT
        )
    except psycopg2.OperationalError as e:
        err_str = str(e)
        # Jika database belum ada, buat database secara otomatis
        if "does not exist" in err_str or "tidak ada" in err_str:
            fallback_conn = None
            for default_db in ("postgres", "template1"):
                try:
                    fallback_conn = psycopg2.connect(
                        dbname=default_db,
                        user=PG_ADMIN_USER,
                        password=admin_pass,
                        host=PG_HOST,
                        port=PG_PORT
                    )
                    break
                except Exception:
                    continue

            if not fallback_conn:
                return False, f"PostgreSQL berjalan, tetapi database '{PG_DB}' belum ada dan gagal terhubung ke database default (postgres/template1) untuk membuatnya: {err_str}"

            try:
                fallback_conn.autocommit = True
                cur = fallback_conn.cursor()
                cur.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(PG_DB)))
                cur.close()
                fallback_conn.close()

                # Hubungkan ke database yang baru dibuat
                conn = psycopg2.connect(
                    dbname=PG_DB,
                    user=PG_ADMIN_USER,
                    password=admin_pass,
                    host=PG_HOST,
                    port=PG_PORT
                )
            except Exception as create_err:
                return False, f"Gagal membuat database '{PG_DB}': {create_err}"
        else:
            return False, f"PostgreSQL belum dapat diakses ({PG_HOST}:{PG_PORT}): {err_str}"
    except Exception as e:
        return False, f"Gagal koneksi PostgreSQL: {e}"

    # 2. Setup student_role dan muat dataset jika database masih kosong
    try:
        conn.autocommit = True
        cur = conn.cursor()

        # Buat atau sinkronkan student_role
        cur.execute(f"""
        DO $$
        BEGIN
            IF NOT EXISTS (SELECT FROM pg_catalog.pg_roles WHERE rolname = '{PG_STUDENT_USER}') THEN
                CREATE ROLE {PG_STUDENT_USER} WITH LOGIN PASSWORD '{PG_STUDENT_PASS}';
            ELSE
                ALTER ROLE {PG_STUDENT_USER} WITH LOGIN PASSWORD '{PG_STUDENT_PASS}';
            END IF;
        END
        $$;
        """)

        # Periksa apakah sudah ada tabel di user schemas
        cur.execute("""
            SELECT COUNT(*) 
            FROM information_schema.tables 
            WHERE table_schema NOT IN ('information_schema', 'pg_catalog', 'pg_toast')
              AND table_schema NOT LIKE 'pg_%%'
              AND table_type = 'BASE TABLE';
        """)
        table_count = cur.fetchone()[0]
        cur.close()
        conn.close()

        # Jika database masih baru/kosong, isi dataset awal secara otomatis
        if table_count == 0:
            ok, msg = reset_classicmodels_database()
            if not ok:
                return False, f"Database '{PG_DB}' dibuat, namun gagal memuat dataset: {msg}"
            return True, f"Database '{PG_DB}', role '{PG_STUDENT_USER}', dan dataset master berhasil diinisialisasi otomatis."

        return True, f"Database '{PG_DB}' dan role '{PG_STUDENT_USER}' siap digunakan ({table_count} tabel aktif)."
    except Exception as e:
        if conn and not conn.closed:
            conn.close()
        return False, f"Gagal setup PostgreSQL: {e}"

def get_pg_admin_connection():
    """Returns an administrative connection to PostgreSQL."""
    conn = psycopg2.connect(
        dbname=PG_DB,
        user=PG_ADMIN_USER,
        password=PG_ADMIN_PASS or None,
        host=PG_HOST,
        port=PG_PORT
    )
    return conn

def get_pg_student_connection():
    """Returns a sandboxed student connection to PostgreSQL."""
    try:
        # Prefer direct connection with student_role
        conn = psycopg2.connect(
            dbname=PG_DB,
            user=PG_STUDENT_USER,
            password=PG_STUDENT_PASS or None,
            host=PG_HOST,
            port=PG_PORT
        )
    except Exception:
        # Fallback: connect as admin and explicitly switch role to student_role
        conn = get_pg_admin_connection()
        conn.autocommit = False
        cur = conn.cursor()
        cur.execute("SET ROLE student_role;")
        cur.close()
    return conn

def check_dangerous_sql(query_text: str) -> Optional[str]:
    """Inspects query text for prohibited operations."""
    cleaned = re.sub(r'--.*?$|/\*.*?\*/', '', query_text, flags=re.MULTILINE)
    
    # Strictly prohibited commands
    forbidden_patterns = [
        r'\bDROP\b',
        r'\bALTER\b',
        r'\bTRUNCATE\b',
        r'\bGRANT\b',
        r'\bREVOKE\b',
        r'\bCREATE\s+(ROLE|USER|DATABASE|EXTENSION)\b',
        r'\bCOPY\b',
        r'\bVACUUM\b',
        r'\bpg_read_file\b',
        r'\bpg_write_file\b',
        r'\blo_import\b',
        r'\blo_export\b',
        r'\bdblink\b'
    ]
    for pat in forbidden_patterns:
        if re.search(pat, cleaned, re.IGNORECASE):
            match_word = re.search(pat, cleaned, re.IGNORECASE).group(0)
            return f"Perintah berbahaya terdeteksi ('{match_word}'). Operasi ini dilarang pada sistem ujian!"
    return None

def execute_student_query(query_text: str) -> Dict[str, Any]:
    """
    Executes a student query within the PostgreSQL sandbox.
    Features:
    - Dangerous command detection
    - 3-second statement_timeout
    - Transaction isolation with automatic ROLLBACK
    - Row truncation limit (max 500 rows)
    """
    query_text = query_text.strip()
    if not query_text:
        return {"success": False, "error": "Query SQL tidak boleh kosong."}

    # Remove trailing semicolon if single query
    if query_text.endswith(";"):
        query_text = query_text[:-1].strip()

    danger_error = check_dangerous_sql(query_text)
    if danger_error:
        return {"success": False, "error": danger_error}

    start_time = time.time()
    conn = None
    try:
        conn = get_pg_student_connection()
        # Enforce transaction mode and autocommit = False
        conn.autocommit = False
        
        cur = conn.cursor()
        # Enforce 3000 ms (3 seconds) statement timeout
        cur.execute("SET statement_timeout = 3000;")
        
        # Execute student query
        cur.execute(query_text)
        
        columns = []
        rows = []
        row_count = 0
        
        if cur.description:
            columns = [desc[0] for desc in cur.description]
            fetched = cur.fetchmany(501)
            row_count = len(fetched)
            is_truncated = False
            if row_count > 500:
                is_truncated = True
                fetched = fetched[:500]
                row_count = 500

            # Convert non-serializable objects (Decimal, Date, etc.)
            for r in fetched:
                row_items = []
                for val in r:
                    if isinstance(val, (datetime.date, datetime.datetime)):
                        row_items.append(val.isoformat())
                    elif hasattr(val, '__float__'):
                        # Decimal or float
                        row_items.append(float(val))
                    elif val is None:
                        row_items.append(None)
                    else:
                        row_items.append(str(val))
                rows.append(row_items)
        else:
            row_count = cur.rowcount

        execution_time_ms = round((time.time() - start_time) * 1000, 2)
        
        # ALWAYS ROLLBACK to preserve sandbox data integrity
        conn.rollback()
        cur.close()
        
        return {
            "success": True,
            "columns": columns,
            "rows": rows,
            "row_count": row_count,
            "is_truncated": is_truncated if cur.description else False,
            "execution_time_ms": execution_time_ms,
            "message": f"Query berhasil dieksekusi ({row_count} baris) dalam {execution_time_ms} ms."
        }

    except psycopg2.extensions.QueryCanceledError:
        if conn:
            conn.rollback()
        return {
            "success": False,
            "error": "Query Timeout (Maksimal 3 detik)! Query Anda memakan waktu terlalu lama atau menghasilkan infinite loop/kartesian tak terbatas."
        }
    except Exception as e:
        if conn:
            conn.rollback()
        return {
            "success": False,
            "error": f"PostgreSQL Error: {str(e)}"
        }
    finally:
        if conn:
            conn.close()

def get_classicmodels_schema() -> Dict[str, List[Dict[str, Any]]]:
    """
    Introspects tables and columns in classicmodels across all active user schemas.
    Returns: { "table_name": [{"name": col, "type": type, "nullable": bool, "pk": bool}] }
    """
    schema_info = {}
    try:
        conn = get_pg_admin_connection()
        cur = conn.cursor()
        
        # Query user tables and columns across all non-system schemas
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
            WHERE c.table_schema NOT IN ('information_schema', 'pg_catalog', 'pg_toast')
              AND c.table_schema NOT LIKE 'pg_%%'
              AND t.table_type = 'BASE TABLE'
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
            
            # Avoid duplicate column entries
            if not any(item["name"] == col for item in schema_info[tbl]):
                schema_info[tbl].append({
                    "name": col,
                    "type": dtype,
                    "nullable": nullable,
                    "pk": is_pk
                })
                
        cur.close()
        conn.close()
    except Exception as e:
        print(f"Error inspecting schema: {e}")
    return schema_info

def reset_classicmodels_database(custom_sql_text: Optional[str] = None) -> Tuple[bool, str]:
    """
    Restores the classicmodels database to the initial clean state.
    Drops all user schemas (public, classicmodels, etc.) with CASCADE
    so no duplicate keys or residual schemas remain.
    """
    try:
        if custom_sql_text:
            sql_content = custom_sql_text
        else:
            if not os.path.exists(CLASSICMODELS_SQL_PATH):
                return False, f"File {CLASSICMODELS_SQL_PATH} tidak ditemukan."
            with open(CLASSICMODELS_SQL_PATH, "r", encoding="utf-8") as f:
                sql_content = f.read()

        conn = get_pg_admin_connection()
        conn.autocommit = True
        cur = conn.cursor()

        # 1. Bersihkan SEMUA user schema lama (public, classicmodels, dan schema custom lainnya)
        cur.execute("""
            SELECT schema_name 
            FROM information_schema.schemata 
            WHERE schema_name NOT IN ('information_schema', 'pg_catalog', 'pg_toast')
              AND schema_name NOT LIKE 'pg_%%';
        """)
        schemas_to_drop = [r[0] for r in cur.fetchall()]
        for s in schemas_to_drop:
            cur.execute(sql.SQL("DROP SCHEMA IF EXISTS {} CASCADE;").format(sql.Identifier(s)))

        # 2. Buat ulang schema public yang bersih dan reset search_path
        cur.execute("""
            CREATE SCHEMA public;
            GRANT ALL ON SCHEMA public TO postgres;
            GRANT ALL ON SCHEMA public TO public;
            SET search_path TO public;
        """)

        # 3. Eksekusi script SQL baru
        cur.execute(sql_content)
        
        # 4. Ambil semua user schema yang aktif setelah script dieksekusi
        cur.execute("""
            SELECT schema_name 
            FROM information_schema.schemata 
            WHERE schema_name NOT IN ('information_schema', 'pg_catalog', 'pg_toast')
              AND schema_name NOT LIKE 'pg_%%';
        """)
        active_schemas = [r[0] for r in cur.fetchall()]

        # 5. Berikan hak akses read-only kepada student_role untuk setiap user schema yang aktif
        for s in active_schemas:
            cur.execute(sql.SQL("""
                DO $$
                BEGIN
                    IF EXISTS (SELECT FROM pg_catalog.pg_roles WHERE rolname = 'student_role') THEN
                        GRANT CONNECT ON DATABASE classicmodels TO student_role;
                        GRANT USAGE ON SCHEMA {schema} TO student_role;
                        GRANT SELECT ON ALL TABLES IN SCHEMA {schema} TO student_role;
                        ALTER DEFAULT PRIVILEGES IN SCHEMA {schema} GRANT SELECT ON TABLES TO student_role;
                        REVOKE CREATE ON SCHEMA {schema} FROM student_role;
                    END IF;
                END
                $$;
            """).format(schema=sql.Identifier(s)))

        # 6. Set search_path pada level database dan role student_role
        search_paths = [f'"{s}"' for s in active_schemas]
        if "public" not in active_schemas:
            search_paths.append('"public"')
        path_str = ", ".join(search_paths)

        cur.execute(f'ALTER DATABASE classicmodels SET search_path TO "$user", {path_str};')
        cur.execute(f"""
            DO $$
            BEGIN
                IF EXISTS (SELECT FROM pg_catalog.pg_roles WHERE rolname = 'student_role') THEN
                    EXECUTE 'ALTER ROLE student_role SET search_path TO "$user", {path_str}';
                END IF;
            END
            $$;
        """)

        cur.close()
        conn.close()
        return True, "Database sandbox berhasil dibersihkan dan dimuat ulang ke kondisi awal."
    except Exception as e:
        return False, f"Gagal mereset database: {str(e)}"

def verify_system_health() -> Dict[str, Any]:
    """Verifies connections to SQLite and PostgreSQL."""
    health = {"sqlite": False, "postgres_admin": False, "postgres_student": False, "details": {}}
    
    # SQLite check
    try:
        conn = get_sqlite_conn()
        cur = conn.cursor()
        cur.execute("SELECT COUNT(*) FROM questions;")
        health["sqlite"] = True
        health["details"]["sqlite_questions"] = cur.fetchone()[0]
        conn.close()
    except Exception as e:
        health["details"]["sqlite_error"] = str(e)

    # Postgres Admin check
    try:
        conn = get_pg_admin_connection()
        cur = conn.cursor()
        cur.execute("SELECT current_database(), current_user;")
        row = cur.fetchone()
        health["postgres_admin"] = True
        health["details"]["pg_admin_db"] = row[0]
        health["details"]["pg_admin_user"] = row[1]
        conn.close()
    except Exception as e:
        health["details"]["pg_admin_error"] = str(e)

    # Postgres Student check
    try:
        conn = get_pg_student_connection()
        cur = conn.cursor()
        cur.execute("SELECT 1;")
        health["postgres_student"] = True
        conn.close()
    except Exception as e:
        health["details"]["pg_student_error"] = str(e)

    return health
