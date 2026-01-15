#!/bin/bash

TS=$(date +"%Y%m%d_%H%M%S")
OUT="/var/www/DDT/diagnostics"
FILE="$OUT/diag_$TS.txt"

mkdir -p "$OUT"

exec > >(tee -a "$FILE") 2>&1

echo "=============================="
echo "🧪 DDT DIAGNOSTICS (EXTENDED)"
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
echo "=== SYSTEMD RESTART LOOP CHECK ==="
systemctl show ddt-web -p RestartCount --value || true
systemctl show ddt-worker -p RestartCount --value || true

echo
echo "=== SYSTEMD JOURNAL (WEB) ==="
journalctl -u ddt-web -n 300 --no-pager || true

echo
echo "=== SYSTEMD JOURNAL (WORKER) ==="
journalctl -u ddt-worker -n 300 --no-pager || true

echo
echo "=== PROCESSI PYTHON / UVICORN ==="
ps aux | grep -E "python|uvicorn|worker.py|main.py" | grep -v grep || true

echo
echo "=== PORTE IN ASCOLTO (FOCUS 8080) ==="
ss -ltnp | grep -E "8080|8000|uvicorn" || true

echo
echo "=== FILE LOCK / INBOX (ATTENZIONE A SMB) ==="
lsof +D /var/www/DDT/inbox || true

echo
echo "=== FILE TEMP / QUARANTINE ==="
ls -lh /var/www/DDT/inbox || true
ls -lh /var/www/DDT/inbox_quarantine || true

echo
echo "=== LOG APPLICATIVO DDT (ULTIMI 400) ==="
tail -n 400 /var/www/DDT/logs/ddt.log || true

echo
echo "=== LAYOUT RULES FILE ==="
ls -lh /var/www/DDT/app/layout_rules/layout_rules.json || true
cat /var/www/DDT/app/layout_rules/layout_rules.json || true

echo
echo "=== CONFIG FILE ==="
ls -lh /var/www/DDT/app/global_config.json || true
cat /var/www/DDT/app/global_config.json || true

echo
echo "=== API CHECK (DASHBOARD) ==="
echo "--- GET /api/dashboard (timeout 5s) ---"
curl -s -m 5 -w "\nHTTP_CODE:%{http_code}\nTIME:%{time_total}s\n" http://127.0.0.1:8080/api/dashboard || echo "❌ API NON RISPONDE"

echo
echo "--- GET /api/documents ---"
curl -s -m 5 -w "\nHTTP_CODE:%{http_code}\n" http://127.0.0.1:8080/api/documents || true

echo
echo "--- GET /api/stats ---"
curl -s -m 5 -w "\nHTTP_CODE:%{http_code}\n" http://127.0.0.1:8080/api/stats || true

echo
echo "=== API STRUCTURE VALIDATION ==="
echo "⚠️ Verifica manuale:"
echo "- response NON deve essere {}"
echo "- deve contenere items / total / last_update"
echo "- se vuoto → array []"

echo
echo "=== FREEZE DETECTION ==="
echo "⏳ Controllo processi in D (uninterruptible sleep)"
ps -eo pid,stat,cmd | grep " D " || echo "✅ Nessun processo bloccato"

echo
echo "=== NETWORK PENDING CHECK ==="
echo "Connessioni ESTABLISHED verso 8080:"
ss -tn state established '( sport = :8080 )' || true

echo
echo "=== END DIAGNOSTICS ==="
echo "📁 File salvato in: $FILE"