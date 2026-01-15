#!/bin/bash
set -e

echo "🛑 Stop servizi (force)"
sudo systemctl stop ddt-web || true
sudo systemctl stop ddt-worker || true

sleep 3

echo "🧨 Kill residui python/uvicorn"
sudo pkill -9 -f "uvicorn main:app" || true
sudo pkill -9 -f "worker.py" || true

sleep 2

echo "🧹 Pulizia pycache"
sudo find /var/www/DDT -type d -name "__pycache__" -exec rm -rf {} +

echo "🚀 Start worker"
sudo systemctl start ddt-worker
sleep 2

echo "🌐 Start web"
sudo systemctl start ddt-web
sleep 2

echo "📊 Stato servizi"
systemctl status ddt-worker --no-pager
systemctl status ddt-web --no-pager

echo "✅ Restart completato"