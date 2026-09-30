@echo off
setlocal enabledelayedexpansion
title Offline SQL Exam System Server

echo Memeriksa instalasi Python...
where python >nul 2>nul
if %errorlevel% neq 0 (
    where py >nul 2>nul
    if %errorlevel% neq 0 (
        echo [ERROR] Python tidak ditemukan di PATH sistem Windows!
        echo Silakan download dan install Python dari https://www.python.org/
        pause
        exit /b 1
    ) else (
        set "PY_CMD=py"
    )
) else (
    set "PY_CMD=python"
)

:: Deteksi IP lokal Wi-Fi Laptop Host
for /f "delims=" %%i in ('%PY_CMD% -c "import socket; s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM); s.connect(('10.255.255.255', 1)); print(s.getsockname()[0]); s.close()" 2^>nul') do set "HOST_IP=%%i"
if "%HOST_IP%"=="" set "HOST_IP=127.0.0.1"

:: Deteksi Master Key Admin
for /f "delims=" %%k in ('%PY_CMD% -c "import database; print(database.get_admin_master_key())" 2^>nul') do set "MASTER_KEY=%%k"
if "%MASTER_KEY%"=="" (
    if defined ADMIN_MASTER_KEY (
        set "MASTER_KEY=%ADMIN_MASTER_KEY%"
    ) else (
        set "MASTER_KEY=admin123"
    )
)

echo ==========================================================
echo     OFFLINE SQL EXAM SYSTEM (PRAKTIKUM BASIS DATA)        
echo ==========================================================
echo Status Jaringan   : Siap (Offline Local Network)
echo Akses Mahasiswa   : http://%HOST_IP%:8000
echo Akses Dosen/Admin : http://localhost:8000/admin
echo Master Key Admin  : %MASTER_KEY%
echo Database Sandbox  : PostgreSQL (classicmodels)
echo ==========================================================
echo Tekan Ctrl+C untuk menghentikan server.
echo.

%PY_CMD% -m uvicorn main:app --host 0.0.0.0 --port 8000 --reload
pause
