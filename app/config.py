import os
import socket
from dotenv import load_dotenv

load_dotenv()

# Root directory del progetto (default: /var/www/DDT)
# NOTA: Non importare paths qui per evitare importazioni circolari
# paths.py userà questa variabile d'ambiente direttamente
BASE_DIR = os.getenv("DDT_BASE_DIR", "/var/www/DDT")

OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
MODEL = os.getenv("MODEL", "gpt-4o-mini")

# Path assoluti per filesystem produzione
# NOTA: Questi vengono inizializzati lazy quando necessario per evitare importazioni circolari
# Usa le funzioni da app.paths invece di queste costanti quando possibile
EXCEL_DIR = os.path.join(BASE_DIR, "excel")
EXCEL_FILE = os.path.join(EXCEL_DIR, "ddt.xlsx")
INBOX_DIR = os.path.join(BASE_DIR, "inbox")
# Directory per documenti processati (struttura: processati/gg-mm-yyyy/)
PROCESSATI_SUBDIR = os.getenv("PROCESSATI_SUBDIR", "processed")
PROCESSATI_DIR = os.path.join(BASE_DIR, PROCESSATI_SUBDIR)

# Credenziali amministratore
ADMIN_USERNAME = os.getenv("ADMIN_USERNAME")
ADMIN_PASSWORD = os.getenv("ADMIN_PASSWORD")
SESSION_SECRET_KEY = os.getenv("SESSION_SECRET_KEY")

# Configurazione IP - calcola automaticamente l'IP locale
def get_local_ip():
    """
    Ottiene l'IP locale della macchina sulla rete.
    
    IMPORTANTE: Usa timeout per evitare blocchi durante l'import del modulo.
    Fallback veloce a 127.0.0.1 se la rete non è disponibile.
    """
    try:
        # Crea un socket e si connette a un server esterno per determinare l'IP locale
        # Timeout breve per evitare blocchi (1 secondo)
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.settimeout(1.0)  # Timeout 1 secondo per evitare blocchi
        try:
            # Non invia effettivamente dati, solo determina quale interfaccia userebbe
            s.connect(("8.8.8.8", 80))
            ip = s.getsockname()[0]
            s.close()
            return ip
        except (socket.timeout, OSError):
            s.close()
            raise  # Rilancia per usare fallback
    except Exception:
        # Fallback: prova a ottenere l'hostname e risolverlo (veloce)
        try:
            hostname = socket.gethostname()
            ip = socket.gethostbyname(hostname)
            # Se è 127.0.0.1, prova un altro metodo
            if ip == "127.0.0.1":
                # Prova a ottenere l'IP da tutte le interfacce (con timeout)
                s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
                s.settimeout(0.1)  # Timeout molto breve (100ms)
                try:
                    s.connect(('10.254.254.254', 1))
                    ip = s.getsockname()[0]
                except Exception:
                    ip = "127.0.0.1"
                finally:
                    s.close()
            return ip
        except Exception:
            # Fallback finale: usa localhost
            return "127.0.0.1"

# Usa l'IP dalla variabile d'ambiente se presente, altrimenti calcolalo automaticamente
SERVER_IP = os.getenv("SERVER_IP") or get_local_ip()

# Ruolo del processo: "web" (solo FastAPI) o "worker" (watchdog + processing)
# Default: "web" per backward compatibility
DDT_ROLE = os.getenv("DDT_ROLE", "web").lower()
IS_WEB_ROLE = DDT_ROLE == "web"
IS_WORKER_ROLE = DDT_ROLE == "worker"
