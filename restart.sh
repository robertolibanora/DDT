#!/bin/bash
set -Eeuo pipefail

# ==============================
# CONFIG
# ==============================
APP_DIR="/var/www/DDT"
SERVICES=("ddt-web" "ddt-worker")
WAIT_STOP=5
WAIT_START=2

# ==============================
# UTILS
# ==============================
log() {
  echo -e "\n🔹 $1"
}

warn() {
  echo -e "⚠️  $1"
}

die() {
  echo -e "❌ $1"
  exit 1
}

# ==============================
# PRECHECK
# ==============================
if [[ $EUID -ne 0 ]]; then
  die "Esegui lo script come root (sudo ./restart_ddt.sh)"
fi

cd "$APP_DIR" || die "Directory $APP_DIR non trovata"

log "Avvio restart controllato DDT"

# ==============================
# STOP SERVIZI (GRACEFUL)
# ==============================
log "Stop servizi (graceful)"
for svc in "${SERVICES[@]}"; do
  systemctl stop "$svc" || warn "Impossibile stoppare $svc"
done

sleep "$WAIT_STOP"

# ==============================
# VERIFICA PROCESSI RESIDUI
# ==============================
log "Verifica processi residui"

PIDS=$(pgrep -f "uvicorn main:app|worker.py" || true)

if [[ -n "$PIDS" ]]; then
  warn "Processi ancora attivi: $PIDS"
  warn "Tentativo SIGTERM"
  kill $PIDS || true
  sleep 2
fi

PIDS=$(pgrep -f "uvicorn main:app|worker.py" || true)

if [[ -n "$PIDS" ]]; then
  warn "SIGTERM fallito → SIGKILL"
  kill -9 $PIDS || true
fi

# ==============================
# CLEANUP
# ==============================
log "Pulizia __pycache__"
find "$APP_DIR" -type d -name "__pycache__" -prune -exec rm -rf {} +

# ==============================
# START SERVIZI (ORDINATO)
# ==============================
log "Start WORKER"
systemctl start ddt-worker
sleep "$WAIT_START"

log "Start WEB"
systemctl start ddt-web
sleep "$WAIT_START"

# ==============================
# HEALTH CHECK
# ==============================
log "Verifica stato servizi"
systemctl --no-pager status ddt-worker
systemctl --no-pager status ddt-web

log "Restart completato con successo"