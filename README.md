# Offline SQL Exam System
**Sistem Ujian SQL Praktikum Basis Data — Local Network, No Internet Required (Multiplatform: macOS, Linux & Windows)**

Platform ujian SQL berbasis web yang dirancang untuk digunakan dalam jaringan Wi-Fi lokal (offline). Dosen/pengawas menjalankan server dari satu laptop (macOS, Linux, atau Windows), dan mahasiswa mengakses melalui browser di perangkat masing-masing tanpa membutuhkan koneksi internet.

---

## Daftar Isi

- [Fitur Utama](#fitur-utama)
- [Teknologi](#teknologi)
- [Struktur Proyek](#struktur-proyek)
- [Prasyarat](#prasyarat)
- [Instalasi & Setup Awal](#instalasi--setup-awal)
  - [Setup di macOS / Linux](#setup-di-macos--linux)
  - [Setup di Windows](#setup-di-windows)
- [Menjalankan Server](#menjalankan-server)
  - [Di macOS / Linux](#di-macos--linux)
  - [Di Windows (CMD / Double Click)](#di-windows-command-prompt--double-click)
  - [Di Windows (PowerShell)](#di-windows-powershell)
- [Akses Sistem](#akses-sistem)
- [Cara Mengganti Password Master](#cara-mengganti-password-master)
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
- Referensi skema database interaktif tersedia langsung di halaman ujian

### Untuk Dosen / Pengawas

| Fitur | Deskripsi |
|---|---|
| **Live Monitoring** | Pantau status online/offline mahasiswa secara real-time (auto-refresh 6 detik) |
| **Hentikan Semua Sesi** | Paksa logout seluruh mahasiswa sekaligus dengan satu klik |
| **Bersihkan Sesi Sebelumnya** | Hapus seluruh data mahasiswa & jawaban untuk mempersiapkan sesi ujian baru |
| **Reset Sesi Mahasiswa** | Kick & reset sesi mahasiswa tertentu agar bisa login kembali jika koneksi terputus |
| **Ganti Password Master** | Ubah password master admin langsung melalui Web UI atau konfigurasi script |
| **Manajemen Soal** | CRUD soal ujian dengan bobot nilai dan kunci jawaban query referensi |
| **Reset Database Sandbox** | Kembalikan dataset `classicmodels` ke kondisi awal |
| **Upload SQL Custom** | Unggah file `.sql` untuk skema dan dataset ujian yang berbeda |
| **Log Aktivitas & Deteksi** | Rekam semua kejadian: login, submit, perpindahan tab, percobaan pembajakan NIM |
| **Bersihkan Log** | Hapus riwayat log aktivitas sebelum sesi ujian baru dimulai |
| **Export CSV** | Unduh seluruh jawaban mahasiswa dalam format spreadsheet siap nilai |
| **Export ZIP (.sql)** | Unduh satu file `.sql` per mahasiswa untuk arsip dan penilaian offline |

---

## Teknologi

| Layer | Teknologi |
|---|---|
| **Backend** | Python 3.10+, FastAPI, Uvicorn (ASGI) |
| **Database Sistem** | SQLite (`exam_system.db` untuk user, soal, submisi, log, password) |
| **Database Ujian** | PostgreSQL 14+ (`classicmodels` sandbox dataset) |
| **Database Driver** | `psycopg2-binary` |
| **Frontend** | HTML5, Vanilla CSS (Dark mode responsive), Vanilla JavaScript |
| **Editor SQL** | CodeMirror (self-hosted offline, tanpa CDN internet) |

---

## Struktur Proyek

```
web-basdat/
│
├── main.py                  # Routing FastAPI: mahasiswa, admin, REST API
├── database.py              # SQLite system engine + PostgreSQL sandbox runner
├── setup_postgres.sql       # Script inisialisasi role & sandbox database
├── requirements.txt         # Dependensi Python
├── run.sh                   # Runner satu-klik untuk macOS & Linux
├── run.bat                  # Runner satu-klik untuk Windows (Command Prompt)
├── run.ps1                  # Runner untuk Windows (PowerShell)
├── .gitignore               # Konfigurasi ignore file db lokal, pycache, OS files
│
├── data/
│   └── classicmodels.sql    # Schema & dataset master PostgreSQL (classicmodels)
│
├── templates/
│   ├── login.html           # Halaman login mahasiswa (dengan pesan validasi NIM)
│   ├── student.html         # Workspace ujian mahasiswa (CodeMirror + preview)
│   ├── admin.html           # Dashboard pengawas & live monitoring
│   └── admin_login.html     # Halaman login dosen dengan Master Key
│
└── static/
    ├── css/style.css        # Stylesheet modern (Dark theme, glassmorphism)
    ├── js/student.js        # Controller interaktif ujian & anti-cheat
    ├── js/admin.js          # Controller dashboard monitoring pengawas
    └── vendor/codemirror/   # Library CodeMirror (self-hosted offline)
```

---

## Prasyarat

Pastikan perangkat **host (laptop dosen/pengawas)** sudah terpasang:

1. **Python 3.10** atau lebih baru
2. **PostgreSQL 14** atau lebih baru, berjalan sebagai service lokal
3. **pip** (Python package manager)

---

## Instalasi & Setup Awal

### Setup di macOS / Linux

1. **Buka Terminal** dan masuk ke direktori proyek:
   ```bash
   cd "web basdat"
   ```

2. **Buat Virtual Environment (opsional tapi disarankan):**
   ```bash
   python3 -m venv venv
   source venv/bin/activate
   ```

3. **Install Dependensi:**
   ```bash
   pip install -r requirements.txt
   ```

4. **Inisialisasi Database PostgreSQL:**
   Pastikan PostgreSQL berjalan (`brew services start postgresql` di macOS), lalu jalankan:
   ```bash
   psql -U postgres -f setup_postgres.sql
   psql -U postgres -d classicmodels -f data/classicmodels.sql
   ```

---

### Setup di Windows

1. **Buka Command Prompt atau PowerShell** sebagai Administrator dan arahkan ke folder proyek:
   ```cmd
   cd "C:\path\ke\web basdat"
   ```

2. **Buat Virtual Environment (opsional):**
   ```cmd
   python -m venv venv
   venv\Scripts\activate
   ```

3. **Install Dependensi:**
   ```cmd
   pip install -r requirements.txt
   ```

4. **Inisialisasi Database PostgreSQL di Windows:**
   Pastikan service PostgreSQL berjalan (buka `services.msc` → pastikan status **Running** pada postgresql), lalu jalankan perintah:
   ```cmd
   psql -U postgres -f setup_postgres.sql
   psql -U postgres -d classicmodels -f data\classicmodels.sql
   ```
   *(Jika diminta password, masukkan password user `postgres` yang Anda tentukan saat instalasi PostgreSQL).*

---

## Menjalankan Server

Sistem menyediakan runner otomatis untuk semua sistem operasi yang akan otomatis mendeteksi IP Wi-Fi lokal dan menampilkan URL yang siap dibagikan ke mahasiswa.

### Di macOS / Linux

Beri hak eksekusi dan jalankan `run.sh`:

```bash
chmod +x run.sh
./run.sh
```

---

### Di Windows (Command Prompt / Double Click)

Cukup **klik dua kali (double-click)** pada file `run.bat` di File Explorer, atau jalankan melalui CMD:

```cmd
run.bat
```

---

### Di Windows (PowerShell)

Jalankan script PowerShell:

```powershell
.\run.ps1
```

*(Jika muncul peringatan ExecutionPolicy di PowerShell, jalankan `Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass` terlebih dahulu).*

---

### Contoh Tampilan Terminal Saat Server Berjalan

```
==========================================================
    OFFLINE SQL EXAM SYSTEM (PRAKTIKUM BASIS DATA)        
==========================================================
Status Jaringan   : Siap (Offline Local Network)
Akses Mahasiswa   : http://192.168.1.15:8000
Akses Dosen/Admin : http://localhost:8000/admin
Master Key Admin  : admin123
Database Sandbox  : PostgreSQL (classicmodels)
==========================================================
Tekan Ctrl+C untuk menghentikan server.
```

---

## Akses Sistem

| Pengguna | URL | Kredensial |
|---|---|---|
| **Mahasiswa** | `http://<IP_HOST>:8000` | NIM + Nama Lengkap |
| **Pengawas/Admin** | `http://localhost:8000/admin` | Master Key: `admin123` (atau sesuai konfigurasi) |

> **Catatan:** Ganti `<IP_HOST>` dengan IP lokal Wi-Fi yang tercetak di jendela terminal saat server dijalankan (misal `http://192.168.1.15:8000`).

---

## Cara Mengganti Password Master

Password default master admin saat pertama kali diinstal adalah **`admin123`**. Anda dapat mengubahnya menggunakan salah satu dari 3 cara berikut:

### Metode 1: Melalui Web Dashboard Admin (Paling Praktis)
1. Buka `http://localhost:8000/admin` di browser laptop dosen dan login.
2. Di pojok kanan atas header dashboard, klik tombol **"Ganti Password"**.
3. Masukkan password saat ini, password baru (minimal 4 karakter), dan konfirmasi password baru.
4. Klik **"Simpan Password"**. Perubahan langsung tersimpan ke database SQLite.

---

### Metode 2: Menggunakan Environment Variable di Script Runner
Jika Anda ingin menyetel password sebelum server dijalankan:

- **Di macOS / Linux (`run.sh`):**
  Tambahkan baris berikut di baris awal `run.sh`:
  ```bash
  export ADMIN_MASTER_KEY="password_baru_anda"
  ```
- **Di Windows (`run.bat`):**
  Tambahkan baris berikut di baris awal `run.bat`:
  ```cmd
  set "ADMIN_MASTER_KEY=password_baru_anda"
  ```
- **Di Windows (`run.ps1`):**
  Tambahkan baris berikut di baris awal `run.ps1`:
  ```powershell
  $env:ADMIN_MASTER_KEY = "password_baru_anda"
  ```

---

### Metode 3: Melalui Perintah Terminal / Python CLI (1 Baris)
Jalankan perintah ini di terminal / command prompt proyek Anda:

- **macOS / Linux:**
  ```bash
  python3 -c "import database; database.update_admin_master_key('password_baru_anda'); print('Password berhasil diubah!')"
  ```
- **Windows (CMD):**
  ```cmd
  python -c "import database; database.update_admin_master_key('password_baru_anda'); print('Password berhasil diubah!')"
  ```

---

## Panduan Pengawas (Admin)

### Alur Kerja Ujian
1. **Jalankan Server:** Buka `run.sh` (macOS/Linux) atau `run.bat` (Windows).
2. **Login Dashboard:** Akses `http://localhost:8000/admin` dengan Master Key.
3. **Persiapan Soal:** Buka Tab *"2. Manajemen Soal Ujian"* untuk memeriksa atau menambahkan butir soal.
4. **Bersihkan Sesi Sebelumnya:** Jika ada sesi ujian dari kelas sebelumnya, klik tombol **"Bersihkan Sesi"** di header Tab Monitoring.
5. **Bagikan URL:** Bagikan URL `http://<IP_HOST>:8000` kepada mahasiswa yang terhubung ke Wi-Fi yang sama.
6. **Pantau Pelaksanaan:** Amati status aktif, jumlah jawaban masuk, dan peringatan perpindahan tab mahasiswa pada Tab *"1. Monitoring Mahasiswa"*.
7. **Selesai Ujian:** Klik tombol **"Hentikan Semua Sesi"** untuk menutup akses ujian secara serentak.
8. **Unduh Nilai:** Buka Tab *"5. Export Hasil Ujian"* dan unduh berkas CSV atau ZIP (.sql).

### Tombol Aksi Cepat Pengawas

| Tombol | Tab | Fungsi |
|---|---|---|
| **Hentikan Semua Sesi** | Monitoring | Memaksa logout seluruh mahasiswa yang sedang online sekaligus |
| **Bersihkan Sesi** | Monitoring | Menghapus seluruh data mahasiswa & jawaban untuk ujian baru |
| **Reset Sesi** | Monitoring | Me-reset sesi mahasiswa tertentu jika terjadi kendala teknis perangkat |
| **Bersihkan Log** | Log Aktivitas | Mengosongkan riwayat log aktivitas dan insiden kecurangan |
| **Reset Database Sandbox**| Database | Mengembalikan tabel-tabel PostgreSQL ke kondisi awal |
| **Export CSV / ZIP** | Export | Mengunduh lembar jawaban seluruh mahasiswa |

---

## Panduan Mahasiswa

1. Pastikan laptop terhubung ke **jaringan Wi-Fi yang sama** dengan laptop pengawas.
2. Buka browser (Chrome, Firefox, Edge, Safari) dan akses URL yang diberikan (contoh: `http://192.168.1.15:8000`).
3. Masukkan **NIM** dan **Nama Lengkap** → klik **Mulai Ujian**.
4. Baca soal di panel kiri, tulis query SQL di panel editor CodeMirror di tengah.
5. Klik **"Jalankan Query (F8 / Ctrl+Enter)"** untuk menguji query terhadap database sandbox.
6. Klik **"Submit Jawaban Soal Ini"** untuk menyimpan jawaban final ke server.
7. Di panel kanan, mahasiswa dapat melihat struktur tabel, kolom, tipe data, dan Primary Key database sebagai referensi.

---

## Keamanan & Anti-Kecurangan

Sistem mengimplementasikan proteksi berlapis untuk memastikan ujian berjalan jujur:

| Mekanisme | Implementasi |
|---|---|
| **Unique NIM Enforcement** | 1 NIM hanya dapat aktif pada 1 perangkat. Percobaan login ganda dari perangkat lain ditolak otomatis. |
| **Anti-Joki (Name Matching)** | Jika login dengan NIM yang sudah terdaftar tetapi nama berbeda, sistem langsung menolak dan mencatat insiden `nim_hijack_attempt`. |
| **Anti-Joki Multi-Account IP**| Satu IP perangkat mahasiswa tidak diizinkan membuka akun mahasiswa lain secara bersamaan. |
| **PostgreSQL Read-Only Role** | Mahasiswa hanya menggunakan role `student_role` yang dibatasi hak `SELECT` saja. |
| **SQL Statement Sanitizer** | Kata kunci berbahaya (`DROP`, `ALTER`, `TRUNCATE`, `INSERT`, `UPDATE`, `DELETE`, `GRANT`, dll.) diblokir di sisi backend. |
| **Statement Timeout** | Query mahasiswa dibatalkan otomatis jika durasi eksekusinya melebihi 3 detik (mencegah DoS / Cartesian join tak terbatas). |
| **Pendeteksi Blur Tab** | Jika mahasiswa berpindah jendela/tab browser, sistem otomatis mencatat peringatan dan menghitung frekuensi perpindahan tab di dashboard pengawas. |

---

## Konfigurasi Environment

Variabel environment opsional untuk kustomisasi koneksi database atau port server:

| Variable | Default | Keterangan |
|---|---|---|
| `ADMIN_MASTER_KEY` | `admin123` | Password master portal pengawas |
| `PG_HOST` | `localhost` | Host database PostgreSQL |
| `PG_PORT` | `5432` | Port database PostgreSQL |
| `PG_DB` | `classicmodels` | Nama database sandbox ujian |
| `PG_ADMIN_USER` | `postgres` | User superuser/admin PostgreSQL |
| `PG_ADMIN_PASS` | *(kosong)* | Password user admin PostgreSQL |
| `PG_STUDENT_USER` | `student_role` | Role sandbox read-only mahasiswa |
| `PG_STUDENT_PASS` | `student123` | Password role sandbox mahasiswa |

---

## Troubleshooting

#### 1. Mahasiswa tidak bisa membuka halaman web (`Connection timed out` / `Site cannot be reached`)
- Pastikan laptop pengawas dan mahasiswa berada pada **jaringan Wi-Fi yang sama**.
- **Di Windows:** Buka **Windows Defender Firewall** → izinkan Python melalui Private Network, atau nonaktifkan firewall private sementara selama sesi ujian.
- **Di macOS:** Buka **System Settings → Network → Firewall** → pastikan koneksi masuk untuk `python3` diizinkan.
- Jika menggunakan Wi-Fi publik/kampus yang menerapkan **Client Isolation / AP Isolation**, perangkat tidak bisa saling berkomunikasi. **Solusi:** Nyalakan **Personal Hotspot** (Tethering) dari smartphone atau laptop pengawas, lalu minta mahasiswa terhubung ke hotspot tersebut.

#### 2. Mahasiswa mendapat pesan "NIM sedang aktif di perangkat lain"
- Terjadi jika mahasiswa sebelumnya sudah login, lalu laptopnya mati atau browser tertutup tanpa logout.
- **Solusi:** Pengawas membuka Tab **Monitoring** di dashboard admin, cari NIM mahasiswa tersebut, lalu klik tombol **"Reset Sesi"**. Mahasiswa dapat login kembali.

#### 3. Error PostgreSQL di Windows saat menjalankan server
- Pastikan service PostgreSQL sudah berjalan di Windows. Buka Command Prompt Administrator dan ketik:
  ```cmd
  net start postgresql-x64-16
  ```
  *(sesuaikan versi 16/15/14 dengan yang terinstall).*
- Pastikan password admin `postgres` telah disesuaikan jika Anda menentukan password saat instalasi:
  ```cmd
  set "PG_ADMIN_PASS=password_anda"
  run.bat
  ```

---

## Lisensi

Proyek ini dikembangkan untuk kebutuhan internal **Praktikum Basis Data**. Bebas digunakan dan dikembangkan untuk keperluan akademik.
