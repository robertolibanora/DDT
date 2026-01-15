#!/bin/bash

echo "=============================="
echo "🔄 Restart completo DDT"
echo "=============================="

set -e

echo "🛑 Stop servizi..."
sudo systemctl stop ddt-web || true
sudo systemctl stop ddt-reader.service || true
sudo systemctl stop ddt-worker || true

echo "🧹 Kill processi residui (python/uvicorn)..."
sudo pkill -9 -f uvicorn || true
sudo pkill -9 -f ddt || true

echo "🔄 Reload systemd..."
sudo systemctl daemon-reexec
sudo systemctl daemon-reload

echo "🚀 Avvio worker..."
sudo systemctl start ddt-worker
sleep 3

echo "🚀 Avvio web..."
sudo systemctl start ddt-web
sleep 3

echo "🧪 Verifica stato servizi..."
sudo systemctl status ddt-worker --no-pager
sudo systemctl status ddt-web --no-pager

echo "🌐 Verifica porta 8080..."
sudo ss -ltnp | grep :8080 || echo "⚠️ Porta 8080 non ancora attiva"

echo "✅ Restart DDT completato"