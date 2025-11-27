import os
import socket
import threading
import logging
from contextlib import asynccontextmanager
from fastapi import FastAPI, UploadFile, File, Request, HTTPException
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from watchdog.observers import Observer
from watchdog.events import FileSystemEventHandler

from app.extract import extract_from_pdf
from app.excel import append_to_excel, read_excel_as_dict, clear_all_ddt
from app.config import INBOX_DIR
from app.logging_config import setup_logging

# Configura logging
setup_logging()
logger = logging.getLogger(__name__)

def get_local_ip():
    """Ottiene l'IP locale della macchina"""
    try:
        # Connessione a un indirizzo remoto per ottenere l'IP locale
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        try:
            return socket.gethostbyname(socket.gethostname())
        except Exception:
            return "127.0.0.1"

class DDTHandler(FileSystemEventHandler):
    """Handler per il monitoraggio automatico dei PDF nella cartella inbox"""
    def on_created(self, event):
        if event.src_path.lower().endswith(".pdf"):
            logger.info(f"📄 Nuovo DDT rilevato: {event.src_path}")
            try:
                data = extract_from_pdf(event.src_path)
                append_to_excel(data)
                logger.info(f"✅ DDT processato con successo: {data.get('numero_documento', 'N/A')}")
            except ValueError as e:
                logger.error(f"❌ Errore validazione DDT: {e}")
            except Exception as e:
                logger.error(f"❌ Errore nel parsing DDT: {e}", exc_info=True)

def start_watcher_background(observer: Observer):
    """Avvia il watcher in background"""
    observer.start()
    print("👀 Watchdog attivo su /inbox - I file PDF vengono processati automaticamente")

@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup - avvia il watchdog in background
    observer = Observer()
    observer.schedule(DDTHandler(), INBOX_DIR, recursive=False)
    watcher_thread = threading.Thread(target=start_watcher_background, args=(observer,), daemon=True)
    watcher_thread.start()
    
    yield
    
    # Shutdown
    observer.stop()
    observer.join()
    print("🛑 Watchdog fermato")

app = FastAPI(lifespan=lifespan)
templates = Jinja2Templates(directory="app/templates")

# Monta la cartella static per CSS e altri file statici
app.mount("/static", StaticFiles(directory="app/static"), name="static")

if not os.path.exists(INBOX_DIR):
    os.makedirs(INBOX_DIR)

@app.get("/", response_class=HTMLResponse)
async def home(request: Request):
    """Pagina principale - Dashboard"""
    return templates.TemplateResponse("dashboard.html", {"request": request})

@app.post("/upload")
async def upload_ddt(file: UploadFile = File(...)):
    """Endpoint per upload manuale di DDT PDF"""
    if not file.filename.lower().endswith('.pdf'):
        raise HTTPException(status_code=400, detail="Il file deve essere un PDF")
    
    import tempfile
    tmp_path = None
    
    try:
        # Salva temporaneamente il file
        with tempfile.NamedTemporaryFile(delete=False, suffix=".pdf") as tmp_file:
            tmp_path = tmp_file.name
            content = await file.read()
            if not content:
                raise HTTPException(status_code=400, detail="Il file è vuoto")
            tmp_file.write(content)
        
        logger.info(f"Upload file: {file.filename} ({len(content)} bytes)")
        
        # Processa il file
        try:
            data = extract_from_pdf(tmp_path)
            append_to_excel(data)
            logger.info(f"DDT caricato con successo: {data.get('numero_documento', 'N/A')}")
            return {"status": "ok", "estratti": data}
        except ValueError as e:
            logger.error(f"Errore validazione durante upload: {e}")
            raise HTTPException(status_code=422, detail=f"Dati estratti non validi: {str(e)}")
        except Exception as e:
            logger.error(f"Errore durante elaborazione upload: {e}", exc_info=True)
            raise HTTPException(status_code=500, detail=f"Errore durante l'elaborazione: {str(e)}")
    finally:
        # Rimuovi il file temporaneo
        if tmp_path and os.path.exists(tmp_path):
            try:
                os.remove(tmp_path)
            except Exception as e:
                logger.warning(f"Impossibile rimuovere file temporaneo: {e}")

@app.get("/dashboard", response_class=HTMLResponse)
async def dashboard(request: Request):
    """Dashboard - visualizza tutti i DDT"""
    return templates.TemplateResponse("dashboard.html", {"request": request})

@app.get("/upload", response_class=HTMLResponse)
async def upload_page(request: Request):
    """Pagina upload DDT"""
    return templates.TemplateResponse("upload.html", {"request": request})

@app.get("/data")
async def get_data():
    """Endpoint API per ottenere tutti i DDT in formato JSON"""
    try:
        return read_excel_as_dict()
    except Exception as e:
        logger.error(f"Errore lettura dati: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Errore durante la lettura dei dati: {str(e)}")

@app.post("/data/clear")
async def delete_all_ddt():
    """Endpoint per cancellare tutti i DDT dal file Excel"""
    try:
        result = clear_all_ddt()
        logger.info(f"Tutti i DDT cancellati: {result.get('rows_deleted', 0)} righe")
        return result
    except ValueError as e:
        logger.error(f"Errore validazione durante cancellazione: {e}")
        raise HTTPException(status_code=400, detail=str(e))
    except IOError as e:
        logger.error(f"Errore I/O durante cancellazione: {e}")
        raise HTTPException(status_code=500, detail=str(e))
    except Exception as e:
        logger.error(f"Errore durante la cancellazione: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Errore durante la cancellazione: {str(e)}")

if __name__ == "__main__":
    import uvicorn
    
    # Usa 0.0.0.0 per permettere connessioni da tutte le interfacce di rete
    # Questo permette l'accesso sia da localhost che dalla rete locale
    host = "0.0.0.0"  # Ascolta su tutte le interfacce
    port = int(os.getenv("UVICORN_PORT", "8000"))
    local_ip = get_local_ip()
    
    # Stampa le informazioni prima di avviare il server
    print("\n" + "="*60)
    print("🚀 Server FastAPI avviato")
    print("="*60)
    print(f"📍 Host: {host} (tutte le interfacce)")
    print(f"🌐 IP Locale: {local_ip}")
    print(f"🔌 Porta: {port}")
    print(f"🔗 URL Locale: http://127.0.0.1:{port}")
    print(f"🔗 URL Rete: http://{local_ip}:{port}")
    print("="*60 + "\n")
    
    # Avvia il server su tutte le interfacce (0.0.0.0)
    uvicorn.run(app, host=host, port=port)