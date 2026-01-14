import os
import socket
from dotenv import load_dotenv

load_dotenv()

# Root directory del progetto (default: /var/www/DDT)
BASE_DIR = os.getenv("DDT_BASE_DIR", "/var/www/DDT")

OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
MODEL = os.getenv("MODEL", "gpt-4o-mini")

# Path assoluti per filesystem produzione
EXCEL_DIR = os.path.join(BASE_DIR, "excel")
EXCEL_FILE = os.path.join(EXCEL_DIR, "ddt.xlsx")
INBOX_DIR = os.path.join(BASE_DIR, "inbox")
# Directory per documenti processati (struttura: processati/gg-mm-yyyy/)
PROCESSATI_DIR = os.path.join(BASE_DIR, os.getenv("PROCESSATI_SUBDIR", "processati"))

# Credenziali amministratore
ADMIN_USERNAME = os.getenv("ADMIN_USERNAME")
ADMIN_PASSWORD = os.getenv("ADMIN_PASSWORD")
SESSION_SECRET_KEY = os.getenv("SESSION_SECRET_KEY")

# Configurazione IP - calcola automaticamente l'IP locale
def get_local_ip():
    """Ottiene l'IP locale della macchina sulla rete"""
    try:
        # Crea un socket e si connette a un server esterno per determinare l'IP locale
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        # Non invia effettivamente dati, solo determina quale interfaccia userebbe
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        # Fallback: prova a ottenere l'hostname e risolverlo
        try:
            hostname = socket.gethostname()
            ip = socket.gethostbyname(hostname)
            # Se è 127.0.0.1, prova un altro metodo
            if ip == "127.0.0.1":
                # Prova a ottenere l'IP da tutte le interfacce
                s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
                s.settimeout(0)
                try:
                    s.connect(('10.254.254.254', 1))
                    ip = s.getsockname()[0]
                except Exception:
                    ip = "127.0.0.1"
                finally:
                    s.close()
            return ip
        except Exception:
            return "127.0.0.1"

# Usa l'IP dalla variabile d'ambiente se presente, altrimenti calcolalo automaticamente
SERVER_IP = os.getenv("SERVER_IP") or get_local_ip()
