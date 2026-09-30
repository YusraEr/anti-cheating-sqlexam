-- ==============================================================================
-- Setup Script: Offline SQL Exam System (PostgreSQL Sandbox)
-- Jalankan script ini sebagai superuser (postgres):
-- psql -U postgres -d postgres -f setup_postgres.sql
-- ==============================================================================

-- 1. Buat Database classicmodels jika belum ada
SELECT 'CREATE DATABASE classicmodels'
WHERE NOT EXISTS (SELECT FROM pg_database WHERE datname = 'classicmodels')\gexec

-- Sambungkan ke database classicmodels
\connect classicmodels

-- 2. Buat Role student_role (Read-Only)
DO $$
BEGIN
    IF NOT EXISTS (SELECT FROM pg_catalog.pg_roles WHERE rolname = 'student_role') THEN
        CREATE ROLE student_role WITH LOGIN PASSWORD 'student123';
        RAISE NOTICE 'Role student_role berhasil dibuat.';
    ELSE
        ALTER ROLE student_role WITH LOGIN PASSWORD 'student123';
        RAISE NOTICE 'Role student_role sudah ada, password disinkronkan.';
    END IF;
END
$$;

-- 3. Batasi Hak Akses Mahasiswa (Prinsip Least Privilege)
-- Berikan hak akses CONNECT ke database classicmodels
GRANT CONNECT ON DATABASE classicmodels TO student_role;

-- Berikan hak akses USAGE pada schema public
GRANT USAGE ON SCHEMA public TO student_role;

-- Berikan HANYA hak akses SELECT (Read-Only) pada semua tabel
GRANT SELECT ON ALL TABLES IN SCHEMA public TO student_role;

-- Pastikan tabel-tabel baru yang dibuat di masa mendatang otomatis mendapatkan hak SELECT
ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT SELECT ON TABLES TO student_role;

-- Cabut hak berbahaya dari student_role
REVOKE CREATE ON SCHEMA public FROM student_role;

\echo '---------------------------------------------------------'
\echo 'Konfigurasi PostgreSQL untuk Sistem Ujian Selesai!'
\echo 'Database    : classicmodels'
\echo 'User Ujian  : student_role'
\echo 'Password    : student123'
\echo 'Hak Akses   : SELECT ONLY (Read-Only Sandbox)'
\echo '---------------------------------------------------------'
