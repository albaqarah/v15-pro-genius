#!/usr/bin/env bash
# ==========================================================
#  V15.3 PRO GENIUS — installer VPS (Ubuntu/Debian)
# ==========================================================
set -e

echo "[1/6] Update paket dasar..."
sudo apt-get update -y
sudo apt-get install -y python3 python3-venv python3-pip

echo "[2/6] Buat virtualenv..."
python3 -m venv venv

echo "[3/6] Install dependency Python..."
./venv/bin/pip install --upgrade pip
./venv/bin/pip install -r requirements.txt

echo "[4/6] Siapkan .env..."
if [ ! -f .env ]; then cp .env.example .env; echo "  -> .env dibuat, isi API key dulu!"; fi
mkdir -p logs

echo "[5/6] Cek sintaks bot..."
./venv/bin/python3 -m py_compile bot_pro_genius_v15.py && echo "  -> OK"

echo "[6/6] Selesai."
cat <<'TIPS'

Langkah berikutnya:
  1. nano .env                 # isi BINANCE / OPENROUTER / EVOMAP / TELEGRAM key
  2. ./venv/bin/python3 selftest.py     # tes koneksi + telegram + AI
  3. pm2 start ecosystem.config.js      # jalankan 24/7
     pm2 logs v15-genius

PENTING: mulai dengan DRY_RUN_MODE=TRUE minimal 48 jam sebelum live.
TIPS
