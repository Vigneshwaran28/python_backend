@echo off
title TaskFlow Python Flask Backend (Port 5000)
cd /d "%~dp0"

echo =======================================================
echo   TaskFlow Python Server (Dual Backend Replica)
echo   Connecting to Neon PostgreSQL Cloud Database
echo =======================================================
echo.

if not exist "venv\Scripts\activate.bat" (
    echo [Setup] Creating virtual environment...
    python -m venv venv
    if errorlevel 1 (
        echo [Error] Python 3.10+ is required to create venv.
        pause
        exit /b 1
    )
    call venv\Scripts\activate.bat
    echo [Setup] Installing dependencies from requirements.txt...
    pip install -r requirements.txt
) else (
    call venv\Scripts\activate.bat
)

echo [Startup] Starting Flask API server on http://localhost:5000 ...
python app.py
pause
