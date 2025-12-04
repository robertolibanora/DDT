import os
from dotenv import load_dotenv

load_dotenv()

OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
MODEL = os.getenv("MODEL", "gpt-4o-mini")
EXCEL_FILE = "ddt.xlsx"
INBOX_DIR = "inbox"

# Credenziali amministratore
ADMIN_USERNAME = os.getenv("ADMIN", "admin")
ADMIN_PASSWORD = os.getenv("ADMIN_PASSWORD", "admin123")
SESSION_SECRET_KEY = os.getenv("SESSION_SECRET_KEY", "your-secret-key-change-in-production")

# Configurazione IP
SERVER_IP = os.getenv("SERVER_IP", "192.168.10.72")