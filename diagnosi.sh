#!/bin/bash

TS=$(date +"%Y%m%d_%H%M%S")
OUT="/var/www/DDT/diagnostics"
FILE="$OUT/diag_$TS.txt"

mkdir -p "$OUT"

exec > >(tee -a "$FILE") 2>&1

echo "=============================="
echo "🧪 DDT DIAGNOSTICS"
echo "🕒 Timestamp: $TS"
echo "=============================="

echo
echo "=== SYSTEM INFO ==="
uname -a
uptime
free -h
df -h /var/www/DDT

echo
echo "=== PYTHON ==="
which python || true
python --version || true
/var/www/DDT/venv/bin/python --version || true

echo
echo "=== SYSTEMD STATUS ==="
systemctl status ddt-web --no-pager || true
systemctl status ddt-worker --no-pager || true
systemctl status ddt-reader.service --no-pager || true

echo
echo "=== SYSTEMD JOURNAL (WEB) ==="
journalctl -u ddt-web -n 200 --no-pager || true

echo
echo "=== SYSTEMD JOURNAL (WORKER) ==="
journalctl -u ddt-worker -n 200 --no-pager || true

echo
echo "=== PROCESSI PYTHON / UVICORN ==="
ps aux | grep -E "python|uvicorn" | grep -v grep || true

echo
echo "=== PORTE IN ASCOLTO ==="
ss -ltnp || true

echo
echo "=== FILE LOCK / INBOX ==="
lsof +D /var/www/DDT/inbox || true

echo
echo "=== LOG APPLICATIVO DDT ==="
tail -n 300 /var/www/DDT/logs/ddt.log || true

echo
echo "=== LAYOUT RULES FILE ==="
ls -lh /var/www/DDT/app/layout_rules/layout_rules.json
cat /var/www/DDT/app/layout_rules/layout_rules.json

echo
echo "=== CONFIG FILE ==="
ls -lh /var/www/DDT/app/global_config.json || true
cat /var/www/DDT/app/global_config.json || true

echo
echo "=== END DIAGNOSTICS ==="
echo "File salvato in: $FILE"