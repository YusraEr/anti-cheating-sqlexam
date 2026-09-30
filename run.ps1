# ==============================================================================
# Script Menjalankan Server Offline SQL Exam System (PowerShell Windows)
# ==============================================================================

$ErrorActionPreference = "Stop"

# Deteksi Python Command
$pyCmd = "python"
if (-not (Get-Command "python" -ErrorAction SilentlyContinue)) {
    if (Get-Command "py" -ErrorAction SilentlyContinue) {
        $pyCmd = "py"
    } else {
        Write-Error "Python tidak ditemukan! Pastikan Python sudah terinstall dan ditambahkan ke PATH."
        exit 1
    }
}

# Deteksi IP Lokal Wi-Fi Laptop Host
$hostIp = & $pyCmd -c @"
import socket
s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
try:
    s.connect(('10.255.255.255', 1))
    print(s.getsockname()[0])
except Exception:
    print('127.0.0.1')
finally:
    s.close()
"@
if (-not $hostIp) { $hostIp = "127.0.0.1" }

# Deteksi Master Key Admin
$masterKey = & $pyCmd -c "import database; print(database.get_admin_master_key())" 2>$null
if (-not $masterKey) {
    if ($env:ADMIN_MASTER_KEY) {
        $masterKey = $env:ADMIN_MASTER_KEY
    } else {
        $masterKey = "admin123"
    }
}

Write-Host "==========================================================" -ForegroundColor Cyan
Write-Host "    OFFLINE SQL EXAM SYSTEM (PRAKTIKUM BASIS DATA)        " -ForegroundColor Yellow
Write-Host "==========================================================" -ForegroundColor Cyan
Write-Host "Status Jaringan   : Siap (Offline Local Network)" -ForegroundColor Green
Write-Host "Akses Mahasiswa   : http://${hostIp}:8000" -ForegroundColor White
Write-Host "Akses Dosen/Admin : http://localhost:8000/admin" -ForegroundColor White
Write-Host "Master Key Admin  : ${masterKey}" -ForegroundColor Magenta
Write-Host "Database Sandbox  : PostgreSQL (classicmodels)" -ForegroundColor White
Write-Host "==========================================================" -ForegroundColor Cyan
Write-Host "Tekan Ctrl+C untuk menghentikan server.`n"

& $pyCmd -m uvicorn main:app --host 0.0.0.0 --port 8000 --reload
