# Offline SQL Exam System
**Sistem Ujian SQL Praktikum Basis Data — Local Network, No Internet Required**

Platform ujian SQL berbasis web yang dirancang untuk digunakan dalam jaringan Wi-Fi lokal (offline). Dosen/pengawas menjalankan server dari satu laptop, mahasiswa mengakses melalui browser di perangkat masing-masing tanpa membutuhkan koneksi internet.

---

## Daftar Isi

- [Fitur Utama](#fitur-utama)
- [Teknologi](#teknologi)
- [Struktur Proyek](#struktur-proyek)
- [Prasyarat](#prasyarat)
- [Instalasi & Setup Awal](#instalasi--setup-awal)
- [Menjalankan Server](#menjalankan-server)
- [Akses Sistem](#akses-sistem)
- [Panduan Pengawas (Admin)](#panduan-pengawas-admin)
- [Panduan Mahasiswa](#panduan-mahasiswa)
- [Keamanan & Anti-Kecurangan](#keamanan--anti-kecurangan)
- [Konfigurasi Environment](#konfigurasi-environment)
- [Troubleshooting](#troubleshooting)

---

## Fitur Utama

### Untuk Mahasiswa
- Editor SQL dengan syntax highlighting (CodeMirror) dan autocomplete
- Eksekusi query langsung ke database PostgreSQL sandbox (read-only)
- Tampilan hasil query dalam tabel responsif
- Submit jawaban per soal, bisa diperbarui hingga ujian selesai
- Referensi skema database tersedia langsung di halaman ujian

### Untuk Dosen / Pengawas

| Fitur | Deskripsi |
|---|---|
| **Live Monitoring** | Pantau status online/offline mahasiswa secara real-time (refresh 6 detik) |
| **Hentikan Semua Sesi** | Paksa logout seluruh mahasiswa sekaligus dengan satu klik |
| **Bersihkan Sesi Sebelumnya** | Hapus data mahasiswa & submisi untuk mempersiapkan ujian baru |
| **Reset Sesi Per Mahasiswa** | Kick & reset sesi mahasiswa tertentu |
| **Manajemen Soal** | CRUD soal ujian dengan bobot nilai dan kunci jawaban acuan |
| **Reset Database Sandbox** | Kembalikan dataset `classicmodels` ke kondisi awal |
| **Upload SQL Custom** | Unggah file `.sql` untuk dataset ujian yang berbeda |
| **Log Aktivitas** | Rekam semua kejadian: login, submit, pindah tab, percobaan kecurangan |
| **Bersihkan Log** | Hapus riwayat log aktivitas sebelum sesi baru dimulai |
| **Export CSV** | Unduh seluruh jawaban dalam format spreadsheet |
| **Export ZIP (.sql)** | Unduh satu file `.sql` per mahasiswa untuk penilaian offline |

---

## Teknologi

| Layer | Teknologi |
|---|---|
| Backend | Python 3.10+, FastAPI, Uvicorn |
| Database Sistem | SQLite (users, soal, submisi, log) |
| Database Ujian | PostgreSQL 14+ (`classicmodels`) |
| Database Driver | psycopg2-binary |
| Frontend | HTML5, Vanilla CSS, Vanilla JS |
| Editor SQL | CodeMirror 5 (self-hosted, offline) |
| Template Engine | Jinja2 |

---

## Struktur Proyek

```
web-basdat/
│
├── main.py                  # Routing FastAPI: mahasiswa, admin, API
├── database.py              # Engine SQLite + eksekutor sandbox PostgreSQL
├── setup_postgres.sql       # Inisialisasi role & database PostgreSQL
├── requirements.txt         # Dependensi Python
├── run.sh                   # Script satu-klik jalankan server
│
├── data/
│   └── classicmodels.sql    # Schema & dataset master PostgreSQL
│
├── templates/
│   ├── login.html           # Halaman login mahasiswa
│   ├── student.html         # Workspace ujian mahasiswa
│   ├── admin.html           # Dashboard pengawas
│   └── admin_login.html     # Login dosen dengan Master Key
│
└── static/
    ├── css/style.css        # Stylesheet (dark mode, responsive)
    ├── js/student.js        # Controller halaman ujian
    ├── js/admin.js          # Controller dashboard admin
    └── vendor/codemirror/   # CodeMirror (self-hosted, offline)
```

---

## Prasyarat

Pastikan perangkat **host (laptop dosen)** sudah memiliki:

1. **Python 3.10** atau lebih baru
2. **PostgreSQL 14** atau lebih baru, berjalan secara lokal
3. **pip** (Python package manager)

---

## Instalasi & Setup Awal

### Langkah 1 — Masuk ke Direktori Proyek

```bash
cd "web basdat"
```

### Langkah 2 — Install Dependensi Python

```bash
pip install -r requirements.txt
```

### Langkah 3 — Setup PostgreSQL

Jalankan script berikut **satu kali** sebagai superuser PostgreSQL untuk membuat database, role mahasiswa, dan hak aksesnya:

```bash
psql -U postgres -d postgres -f setup_postgres.sql
```

Script ini akan:
- Membuat database `classicmodels`
- Membuat role `student_role` dengan password `student123` (read-only)
- Memberikan hak `SELECT` pada semua tabel, mencabut hak berbahaya

### Langkah 4 — Muat Dataset Ujian

Muat data awal `classicmodels` via Admin Dashboard → Tab **"Database Sandbox"** → klik **"Reset Database Sandbox"**.

Atau langsung via psql:

```bash
psql -U postgres -d classicmodels -f data/classicmodels.sql
```

---

## Menjalankan Server

```bash
chmod +x run.sh
./run.sh
```

Output yang muncul di terminal:

```
==========================================================
    OFFLINE SQL EXAM SYSTEM (PRAKTIKUM BASIS DATA)
==========================================================
Status Jaringan   : Siap (Offline Local Network)
Akses Mahasiswa   : http://192.168.x.x:8000
Akses Dosen/Admin : http://localhost:8000/admin
Master Key Admin  : admin123
==========================================================
Tekan Ctrl+C untuk menghentikan server.
```

> Server otomatis mendeteksi IP lokal laptop dan menampilkan URL yang bisa dibagikan ke mahasiswa.

---

## Akses Sistem

| Pengguna | URL | Kredensial |
|---|---|---|
| **Mahasiswa** | `http://<IP_HOST>:8000` | NIM + Nama Lengkap |
| **Pengawas/Admin** | `http://localhost:8000/admin` | Master Key: `admin123` |

> Ganti `<IP_HOST>` dengan IP lokal yang ditampilkan saat server dijalankan (contoh: `192.168.1.15`).
>
> Master Key admin dapat diubah melalui tabel `system_settings` di SQLite jika diperlukan.

---

## Panduan Pengawas (Admin)

### Alur Kerja Ujian

```
1. Jalankan ./run.sh
2. Buka /admin → Login dengan Master Key
3. Setup Soal  →  Tab "Manajemen Soal Ujian" → tambah/edit soal
4. Reset DB    →  Tab "Database Sandbox" → Reset ke kondisi awal
5. Bagikan URL mahasiswa (tampil di terminal)
6. Pantau      →  Tab "Monitoring Mahasiswa" (auto-refresh 6 detik)
7. Selesai     →  Klik "Hentikan Semua Sesi" → Export hasil
```

### Tombol Aksi Penting

| Tombol | Tab | Fungsi |
|---|---|---|
| ⛔ Hentikan Semua Sesi | Monitoring | Paksa logout semua mahasiswa aktif sekaligus |
| 🗑️ Bersihkan Sesi Sebelumnya | Monitoring | Hapus semua data mahasiswa & jawaban (untuk ujian baru) |
| Reset Sesi | Monitoring | Reset sesi satu mahasiswa tertentu |
| 🗑️ Bersihkan Semua Log | Log Aktivitas | Hapus seluruh riwayat log |
| Reset Database Sandbox | Database | Kembalikan `classicmodels` ke kondisi awal |

### Export Hasil Ujian

- **CSV** — Semua jawaban dalam satu file, siap dibuka di Excel / LibreOffice Calc
- **ZIP (.sql)** — Satu file `.sql` per mahasiswa, berisi semua jawaban terformat rapi

---

## Panduan Mahasiswa

1. Pastikan laptop/HP terhubung ke **Wi-Fi yang sama** dengan laptop dosen
2. Buka browser, ketik URL yang diberikan pengawas: `http://192.168.x.x:8000`
3. Masukkan **NIM** dan **Nama Lengkap** sesuai data → klik Login
4. Baca soal di panel kiri, tulis query SQL di editor tengah
5. Klik **Jalankan Query** untuk melihat hasil sebelum submit
6. Klik **Submit Jawaban** saat sudah yakin (jawaban bisa diperbarui)

> ⚠️ Jangan buka tab baru atau pindah jendela — setiap perpindahan tab tercatat di log pengawas.

---

## Keamanan & Anti-Kecurangan

### Proteksi Sesi & Identitas

| Mekanisme | Cara Kerja |
|---|---|
| **Unique NIM per Sesi** | Satu NIM hanya boleh aktif di **1 perangkat** pada waktu bersamaan. Login dari perangkat lain dengan NIM yang sedang aktif akan diblokir otomatis. |
| **Verifikasi Nama** | Setiap NIM terikat dengan nama saat pertama kali terdaftar. Percobaan login dengan NIM milik orang lain (nama berbeda) langsung ditolak dan dicatat sebagai `nim_hijack_attempt`. |
| **IP Binding** | Token sesi terikat ke IP address. Jika IP berubah di tengah ujian, sesi otomatis tidak valid dan mahasiswa diminta login ulang. |
| **Anti Multi-Account per IP** | Satu IP/perangkat tidak bisa menjalankan lebih dari satu sesi mahasiswa secara bersamaan. |

### Proteksi Database Sandbox

| Mekanisme | Detail |
|---|---|
| **Read-Only Role** | Mahasiswa hanya menggunakan role `student_role` yang memiliki hak `SELECT` saja |
| **Blacklist Perintah** | `DROP`, `ALTER`, `TRUNCATE`, `GRANT`, `COPY`, `VACUUM`, dll. diblokir di sisi server |
| **Statement Timeout** | Query dibatalkan otomatis jika melebihi **3 detik** |
| **Auto Rollback** | Setiap query selalu di-rollback setelah eksekusi untuk menjaga integritas data sandbox |
| **Batas Baris** | Hasil query dibatasi maksimal **500 baris** |

### Deteksi Perilaku Mencurigakan

- Setiap perpindahan tab/jendela browser dicatat sebagai `blur_tab` di log
- Percobaan paste dari clipboard dapat dideteksi dan dicatat
- Semua aktivitas (login, submit, run query, kick) tersimpan di tabel `exam_logs`

---

## Konfigurasi Environment

Semua konfigurasi dapat diatur melalui environment variables sebelum menjalankan server:

| Variable | Default | Keterangan |
|---|---|---|
| `PG_HOST` | `localhost` | Host PostgreSQL |
| `PG_PORT` | `5432` | Port PostgreSQL |
| `PG_DB` | `classicmodels` | Nama database ujian |
| `PG_ADMIN_USER` | `postgres` | User admin PostgreSQL |
| `PG_ADMIN_PASS` | *(kosong)* | Password admin PostgreSQL |
| `PG_STUDENT_USER` | `student_role` | User sandbox mahasiswa |
| `PG_STUDENT_PASS` | `student123` | Password user sandbox |

Contoh penggunaan:

```bash
PG_ADMIN_PASS=rahasia123 ./run.sh
```

---

## Troubleshooting

**Mahasiswa tidak bisa mengakses server**
- Pastikan semua perangkat terhubung ke **jaringan Wi-Fi yang sama**
- Cek apakah Firewall macOS memblokir koneksi masuk: **System Settings → Network → Firewall** → izinkan `python3`
- Jika Wi-Fi kampus mengaktifkan **AP Isolation**, gunakan **Personal Hotspot** dari laptop/HP dosen sebagai gantinya

**Error koneksi PostgreSQL saat server start**
- Pastikan PostgreSQL sudah berjalan: `brew services start postgresql`
- Pastikan `setup_postgres.sql` sudah dieksekusi
- Periksa password di environment variable `PG_ADMIN_PASS`

**Mahasiswa tidak bisa login — "NIM sedang aktif di perangkat lain"**
- Buka **Tab Monitoring** di Admin Dashboard → klik tombol **Reset Sesi** pada baris NIM tersebut
- Setelah direset, mahasiswa bisa login ulang dari perangkat aslinya

**URL `localhost:8000` tidak bisa diakses mahasiswa**
- `localhost` hanya bisa diakses dari laptop host. Mahasiswa harus menggunakan **IP lokal** yang tampil di terminal saat server dijalankan

---

## Lisensi

Proyek ini dibuat untuk keperluan **internal praktikum Basis Data**. Bebas digunakan dan dimodifikasi untuk keperluan akademik.
