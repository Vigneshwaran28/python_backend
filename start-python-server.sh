#!/usr/bin/env bash
set -e

DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" >/dev/null 2>&1 && pwd)"
cd "$DIR"

echo "======================================================="
echo "  TaskFlow Python Server (Dual Backend Replica)"
echo "  Connecting to Neon PostgreSQL Cloud Database"
echo "======================================================="

if [ ! -d "venv" ]; then
    echo "[Setup] Creating virtual environment..."
    python3 -m venv venv
    source venv/bin/activate
    echo "[Setup] Installing dependencies..."
    pip install -r requirements.txt
else
    source venv/bin/activate
fi

echo "[Startup] Starting Flask API server on http://localhost:5000 ..."
python app.py
