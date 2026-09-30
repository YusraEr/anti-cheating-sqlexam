#!/usr/bin/env bash
# ==============================================================================
# Script Menjalankan Server Offline SQL Exam System
# ==============================================================================

set -e

# Deteksi IP Lokal Wi-Fi Laptop Host
HOST_IP=$(python3 -c "
import socket
s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
try:
    s.connect(('10.255.255.255', 1))
    print(s.getsockname()[0])
except Exception:
    print('127.0.0.1')
finally:
    s.close()
")

echo "=========================================================="
echo "    OFFLINE SQL EXAM SYSTEM (PRAKTIKUM BASIS DATA)        "
echo "=========================================================="
echo "Status Jaringan   : Siap (Offline Local Network)"
echo "Akses Mahasiswa   : http://${HOST_IP}:8000"
echo "Akses Dosen/Admin : http://localhost:8000/admin"
echo "Master Key Admin  : admin123"
echo "Database Sandbox  : PostgreSQL (classicmodels)"
echo "=========================================================="
echo "Tekan Ctrl+C untuk menghentikan server."
echo ""

# Menjalankan server FastAPI pada 0.0.0.0 agar dapat diakses semua laptop client
exec python3 -m uvicorn main:app --host 0.0.0.0 --port 8000 --reload
