#!/bin/bash
set -e

echo "🛑 Stop servizi"
sudo systemctl stop ddt-web
sudo systemctl stop ddt-worker

echo "🧹 Pulizia pycache"
sudo find /var/www/DDT -type d -name "__pycache__" -exec rm -rf {} +

echo "🚀 Start worker"
sudo systemctl start ddt-worker
sleep 2

echo "🌐 Start web"
sudo systemctl start ddt-web
sleep 2

echo "📊 Stato servizi"
sudo systemctl status ddt-worker --no-pager
sudo systemctl status ddt-web --no-pager

echo "✅ Restart completato"