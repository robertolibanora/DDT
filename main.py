import os
import socket
import threading
import logging
from pathlib import Path
from contextlib import asynccontextmanager
from fastapi import FastAPI, UploadFile, File, Request, HTTPException, Form, Depends
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.middleware.sessions import SessionMiddleware
from watchdog.observers import Observer
from watchdog.events import FileSystemEventHandler

from app.extract import extract_from_pdf
from app.excel import append_to_excel, read_excel_as_dict, clear_all_ddt
from app.config import INBOX_DIR, SERVER_IP
from app.logging_config import setup_logging
from app.routers import rules_router, reprocess_router, preview_router
from app.corrections import get_file_hash
from app.auth import (
    get_session_middleware,
    is_authenticated,
    require_auth,
    login_user,
    logout_user
)

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
    
    def _is_pdf_file(self, path: str) -> bool:
        """Verifica se il path è un file PDF (non una directory)"""
        return os.path.isfile(path) and path.lower().endswith(".pdf")
    
    def _wait_for_file_ready(self, file_path: str, max_wait: int = 10) -> bool:
        """
        Attende che il file sia completamente scritto.
        Alcuni sistemi di file possono generare on_created prima che il file sia finito.
        """
        import time
        for _ in range(max_wait):
            try:
                # Verifica che il file esista e non sia in scrittura
                if os.path.exists(file_path):
                    # Prova ad aprirlo in lettura per verificare che sia accessibile
                    with open(file_path, 'rb') as f:
                        f.read(1)  # Leggi almeno 1 byte per verificare l'accesso
                    return True
            except (OSError, IOError, PermissionError):
                pass
            time.sleep(0.5)  # Aspetta 0.5 secondi prima di riprovare
        return False
    
    def __init__(self):
        """Inizializza l'handler con un set di file già processati"""
        super().__init__()
        self._processed_files = set()  # Set di hash di file già processati
        self._processing_files = set()  # Set di file attualmente in elaborazione (per evitare race condition)
    
    def _is_file_already_processed(self, file_hash: str) -> bool:
        """Verifica se un file è già stato processato"""
        # Controlla nella cache locale
        if file_hash in self._processed_files:
            return True
        
        # Controlla nella coda watchdog (sia pending che processed)
        try:
            from app.watchdog_queue import is_file_hash_in_queue
            if is_file_hash_in_queue(file_hash):
                # Se trovato, aggiungi alla cache locale
                self._processed_files.add(file_hash)
                return True
        except Exception as e:
            logger.debug(f"Errore controllo coda watchdog: {e}")
        
        return False
    
    def _process_pdf(self, file_path: str):
        """Processa un file PDF rilevato dal watchdog - aggiunge alla coda per anteprima"""
        if not self._is_pdf_file(file_path):
            return
        
        # Normalizza il percorso per evitare duplicati
        file_path = os.path.abspath(file_path)
        
        # Attendi che il file sia completamente scritto (aumentato a 15 secondi per file grandi)
        if not self._wait_for_file_ready(file_path, max_wait=15):
            logger.warning(f"⏳ File non pronto dopo l'attesa: {file_path}")
            return
        
        try:
            from app.corrections import get_file_hash
            
            # Calcola hash PRIMA di processare
            file_hash = get_file_hash(file_path)
            
            # Verifica se il file è già stato processato o è in elaborazione
            if file_hash in self._processing_files:
                logger.debug(f"⏭️ File già in elaborazione, salto: {file_path}")
                return
            
            if self._is_file_already_processed(file_hash):
                logger.info(f"⏭️ File già processato, salto: {file_path}")
                return
            
            # Marca come in elaborazione
            self._processing_files.add(file_hash)
            
            logger.info(f"📄 Nuovo DDT rilevato: {file_path}")
            
            import base64
            from app.watchdog_queue import add_to_queue
            
            # Leggi il file PDF
            with open(file_path, 'rb') as f:
                pdf_bytes = f.read()
            
            if len(pdf_bytes) == 0:
                logger.warning(f"⚠️ File PDF vuoto: {file_path}")
                self._processing_files.discard(file_hash)
                return
            
            # Estrai i dati (ma NON salvare ancora)
            data = extract_from_pdf(file_path)
            
            # Verifica se questo numero documento è già in Excel (controllo finale)
            try:
                from app.excel import read_excel_as_dict
                existing_data = read_excel_as_dict()
                for row in existing_data.get("rows", []):
                    if (row.get("numero_documento") == data.get("numero_documento") and 
                        row.get("mittente", "").strip() == data.get("mittente", "").strip()):
                        logger.info(f"⏭️ DDT già presente in Excel (numero: {data.get('numero_documento')}), salto: {file_path}")
                        self._processed_files.add(file_hash)
                        self._processing_files.discard(file_hash)
                        return
            except:
                pass  # Se c'è un errore nella lettura Excel, continua comunque
            
            # Converti PDF in base64
            pdf_base64 = base64.b64encode(pdf_bytes).decode()
            
            # Aggiungi alla coda per l'anteprima
            queue_id = add_to_queue(file_path, data, pdf_base64, file_hash)
            logger.info(f"📋 DDT aggiunto alla coda per anteprima: {queue_id} - {data.get('numero_documento', 'N/A')}")
            
            # Rimuovi da processing e aggiungi a processed dopo un breve delay
            # per permettere al frontend di processarlo
            self._processing_files.discard(file_hash)
            
        except ValueError as e:
            logger.error(f"❌ Errore validazione DDT: {e}")
            self._processing_files.discard(file_hash) if 'file_hash' in locals() else None
        except FileNotFoundError:
            logger.warning(f"⚠️ File non trovato (potrebbe essere stato spostato): {file_path}")
            self._processing_files.discard(file_hash) if 'file_hash' in locals() else None
        except Exception as e:
            logger.error(f"❌ Errore nel parsing DDT: {e}", exc_info=True)
            self._processing_files.discard(file_hash) if 'file_hash' in locals() else None
    
    def on_created(self, event):
        """Gestisce l'evento di creazione file"""
        if not event.is_directory and self._is_pdf_file(event.src_path):
            # Usa un thread separato per non bloccare il watchdog
            thread = threading.Thread(target=self._process_pdf, args=(event.src_path,), daemon=True)
            thread.start()
    
    def on_moved(self, event):
        """Gestisce l'evento di spostamento file (quando un file viene copiato/spostato in inbox)"""
        if not event.is_directory and self._is_pdf_file(event.dest_path):
            # Usa un thread separato per non bloccare il watchdog
            thread = threading.Thread(target=self._process_pdf, args=(event.dest_path,), daemon=True)
            thread.start()
    
    def on_modified(self, event):
        """Gestisce l'evento di modifica file (per file che vengono scritti progressivamente)"""
        if not event.is_directory and self._is_pdf_file(event.src_path):
            # Evita di processare lo stesso file più volte
            # Usa un thread separato per non bloccare il watchdog
            thread = threading.Thread(target=self._process_pdf, args=(event.src_path,), daemon=True)
            thread.start()

def start_watcher_background(observer: Observer):
    """Avvia il watcher in background"""
    try:
        observer.start()
        inbox_path = os.path.abspath(INBOX_DIR)
        print(f"👀 Watchdog attivo su {inbox_path} - I file PDF vengono processati automaticamente")
        logger.info(f"Watchdog avviato e monitora: {inbox_path}")
    except Exception as e:
        logger.error(f"❌ Errore nell'avvio del watchdog: {e}", exc_info=True)
        print(f"❌ Errore nell'avvio del watchdog: {e}")

@asynccontextmanager
async def lifespan(app: FastAPI):
    # Assicurati che la cartella inbox esista
    inbox_path = os.path.abspath(INBOX_DIR)
    if not os.path.exists(inbox_path):
        os.makedirs(inbox_path, exist_ok=True)
        logger.info(f"📁 Cartella inbox creata: {inbox_path}")
    
    # Startup - avvia il watchdog in background
    observer = Observer()
    try:
        handler = DDTHandler()  # Crea un'istanza singola dell'handler per mantenere lo stato
        observer.schedule(handler, inbox_path, recursive=False)
        watcher_thread = threading.Thread(target=start_watcher_background, args=(observer,), daemon=True)
        watcher_thread.start()
        logger.info(f"👀 Watchdog configurato per monitorare: {inbox_path}")
    except Exception as e:
        logger.error(f"❌ Errore nella configurazione del watchdog: {e}", exc_info=True)
    
    yield
    
    # Shutdown
    try:
        if observer.is_alive():
            observer.stop()
            observer.join(timeout=5.0)
            logger.info("🛑 Watchdog fermato correttamente")
    except Exception as e:
        logger.warning(f"⚠️ Errore durante lo shutdown del watchdog: {e}")

app = FastAPI(lifespan=lifespan)
templates = Jinja2Templates(directory="app/templates")

# Aggiungi middleware per le sessioni (2 ore di durata)
from app.config import SESSION_SECRET_KEY
app.add_middleware(SessionMiddleware, secret_key=SESSION_SECRET_KEY, max_age=7200, same_site="lax", https_only=False)

# Monta la cartella static per CSS e altri file statici
app.mount("/static", StaticFiles(directory="app/static"), name="static")

# Dependency per verificare autenticazione
async def check_auth(request: Request):
    """Dependency per verificare che l'utente sia autenticato"""
    if not is_authenticated(request):
        raise HTTPException(status_code=401, detail="Autenticazione richiesta")
    return True


# ============================================
# ROUTE PUBBLICHE (senza autenticazione)
# ============================================

@app.get("/login", response_class=HTMLResponse)
async def login_page(request: Request):
    """Pagina di login"""
    # Se già autenticato, reindirizza alla dashboard
    if is_authenticated(request):
        return RedirectResponse(url="/dashboard", status_code=302)
    return templates.TemplateResponse("login.html", {"request": request})

@app.post("/login")
async def login(request: Request, username: str = Form(...), password: str = Form(...)):
    """Endpoint per il login"""
    try:
        if login_user(request, username, password):
            # Controlla se la richiesta viene da fetch/JavaScript (ha header Accept: application/json)
            accept_header = request.headers.get("accept", "")
            if "application/json" in accept_header or request.headers.get("x-requested-with") == "XMLHttpRequest":
                # Restituisci JSON per richieste AJAX/fetch
                return JSONResponse(
                    status_code=200,
                    content={"success": True, "message": "Login riuscito", "redirect": "/dashboard"}
                )
            else:
                # Redirect HTML per richieste normali del browser
                return RedirectResponse(url="/dashboard", status_code=302)
        else:
            # Se le credenziali sono sbagliate, restituisci JSON per gestione JS
            return JSONResponse(
                status_code=401,
                content={"success": False, "detail": "Credenziali non valide"}
            )
    except Exception as e:
        logger.error(f"Errore durante il login: {e}", exc_info=True)
        return JSONResponse(
            status_code=500,
            content={"success": False, "detail": "Errore interno del server"}
        )

@app.post("/logout")
async def logout(request: Request):
    """Endpoint per il logout"""
    logout_user(request)
    return RedirectResponse(url="/login", status_code=302)

@app.get("/logout")
async def logout_get(request: Request):
    """Endpoint GET per il logout"""
    logout_user(request)
    return RedirectResponse(url="/login", status_code=302)

# ============================================
# ROUTE PROTETTE (richiedono autenticazione)
# ============================================

# IMPORTANTE: Registra le route HTML PRIMA dei router API per evitare conflitti
@app.get("/", response_class=HTMLResponse)
async def home(request: Request):
    """Pagina principale - Reindirizza al login o alla dashboard"""
    # Se autenticato, vai alla dashboard
    if is_authenticated(request):
        return RedirectResponse(url="/dashboard", status_code=302)
    # Altrimenti vai al login
    return RedirectResponse(url="/login", status_code=302)

@app.post("/upload")
async def upload_ddt(request: Request, file: UploadFile = File(...), auth: bool = Depends(check_auth)):
    """Endpoint per upload manuale di DDT PDF - salva copia in inbox e restituisce anteprima"""
    if not file.filename.lower().endswith('.pdf'):
        raise HTTPException(status_code=400, detail="Il file deve essere un PDF")
    
    import tempfile
    import base64
    import shutil
    from datetime import datetime
    
    tmp_path = None
    inbox_saved_path = None
    
    try:
        # Salva temporaneamente il file
        with tempfile.NamedTemporaryFile(delete=False, suffix=".pdf") as tmp_file:
            tmp_path = tmp_file.name
            content = await file.read()
            if not content:
                raise HTTPException(status_code=400, detail="Il file è vuoto")
            tmp_file.write(content)
        
        logger.info(f"Upload file: {file.filename} ({len(content)} bytes)")
        
        # Processa il file - estrai dati
        try:
            data = extract_from_pdf(tmp_path)
            file_hash = get_file_hash(tmp_path) if tmp_path else None
            
            # Salva una copia nella cartella inbox per permettere la riapertura dell'anteprima
            inbox_path = Path(INBOX_DIR)
            inbox_path.mkdir(parents=True, exist_ok=True)
            
            # Genera un nome file basato sul numero documento e mittente per facilitare la ricerca
            numero_doc = data.get("numero_documento", "").strip() or "UNKNOWN"
            mittente_short = (data.get("mittente", "").strip()[:30] or "UNKNOWN").replace("/", "_").replace("\\", "_")
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            
            # Nome file: numero_documento_mittente_timestamp.pdf
            safe_filename = f"{numero_doc}_{mittente_short}_{timestamp}.pdf"
            # Rimuovi caratteri non validi per i nomi file
            safe_filename = "".join(c for c in safe_filename if c.isalnum() or c in "._- ")
            safe_filename = safe_filename.replace(" ", "_")
            
            inbox_saved_path = inbox_path / safe_filename
            
            # Se il file esiste già, aggiungi un contatore
            counter = 1
            original_inbox_path = inbox_saved_path
            while inbox_saved_path.exists():
                name_part = original_inbox_path.stem
                inbox_saved_path = inbox_path / f"{name_part}_{counter}.pdf"
                counter += 1
            
            # Copia il file nella cartella inbox
            shutil.copy2(tmp_path, inbox_saved_path)
            logger.info(f"📁 Copia salvata in inbox: {inbox_saved_path.name}")
            
            # Converti PDF in base64 per visualizzarlo
            pdf_base64 = base64.b64encode(content).decode()
            
            logger.info(f"Dati estratti per anteprima: {data.get('numero_documento', 'N/A')}")
            
            return JSONResponse({
                "success": True,
                "extracted_data": data,
                "file_hash": file_hash or "manual_upload",
                "file_name": file.filename,
                "file_path": str(inbox_saved_path),  # Percorso salvato in inbox
                "pdf_base64": pdf_base64,
                "pdf_mime": "application/pdf"
            })
        except ValueError as e:
            logger.error(f"Errore validazione durante upload: {e}")
            raise HTTPException(status_code=422, detail=f"Dati estratti non validi: {str(e)}")
        except Exception as e:
            logger.error(f"Errore durante elaborazione upload: {e}", exc_info=True)
            raise HTTPException(status_code=500, detail=f"Errore durante l'elaborazione: {str(e)}")
    finally:
        # Elimina il file temporaneo (ora abbiamo la copia in inbox)
        if tmp_path and os.path.exists(tmp_path):
            try:
                os.unlink(tmp_path)
            except Exception as e:
                logger.warning(f"Impossibile eliminare file temporaneo {tmp_path}: {e}")

@app.get("/dashboard", response_class=HTMLResponse)
async def dashboard(request: Request):
    """Dashboard - visualizza tutti i DDT"""
    # Verifica autenticazione e reindirizza se necessario
    if not is_authenticated(request):
        return RedirectResponse(url="/login", status_code=302)
    return templates.TemplateResponse("dashboard.html", {"request": request})

@app.get("/upload", response_class=HTMLResponse)
async def upload_page(request: Request):
    """Pagina upload DDT"""
    # Verifica autenticazione e reindirizza se necessario
    if not is_authenticated(request):
        return RedirectResponse(url="/login", status_code=302)
    return templates.TemplateResponse("upload.html", {"request": request})

@app.get("/rules", response_class=HTMLResponse)
async def rules_page(request: Request):
    """Pagina gestione regole - DEVE essere prima del router API"""
    # Verifica autenticazione e reindirizza se necessario
    if not is_authenticated(request):
        return RedirectResponse(url="/login", status_code=302)
    return templates.TemplateResponse("rules.html", {"request": request})

# Include i router per regole, reprocessing e anteprima (dopo le route HTML per evitare conflitti)
app.include_router(rules_router.router)
app.include_router(reprocess_router.router)
app.include_router(preview_router.router)

@app.get("/data")
async def get_data(request: Request, auth: bool = Depends(check_auth)):
    """Endpoint API per ottenere tutti i DDT in formato JSON"""
    try:
        return read_excel_as_dict()
    except Exception as e:
        logger.error(f"Errore lettura dati: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Errore durante la lettura dei dati: {str(e)}")

@app.get("/api/watchdog-queue")
async def get_watchdog_queue(request: Request, auth: bool = Depends(check_auth)):
    """Endpoint per ottenere gli elementi in coda dal watchdog - garantisce base64 per rete locale"""
    try:
        from app.watchdog_queue import get_pending_items
        from app.config import INBOX_DIR
        import base64
        
        items = get_pending_items()
        
        # Assicurati che ogni item abbia il pdf_base64 (per compatibilità rete locale)
        for item in items:
            # Se manca il base64 o è vuoto, rigeneralo dal file
            if not item.get("pdf_base64") or len(item.get("pdf_base64", "")) < 100:
                file_path = item.get("file_path")
                file_name = item.get("file_name")
                
                if file_path or file_name:
                    try:
                        # Prova prima con il file_path completo
                        pdf_path = None
                        if file_path:
                            pdf_path = Path(file_path)
                            # Se è relativo, prova nella cartella inbox
                            if not pdf_path.is_absolute():
                                pdf_path = Path(INBOX_DIR) / pdf_path.name
                        
                        # Se non trovato, prova con il file_name nella cartella inbox
                        if not pdf_path or not pdf_path.exists():
                            if file_name:
                                pdf_path = Path(INBOX_DIR) / file_name
                        
                        # Se trovato, leggi e converti in base64
                        if pdf_path and pdf_path.exists():
                            with open(pdf_path, 'rb') as f:
                                pdf_bytes = f.read()
                            item["pdf_base64"] = base64.b64encode(pdf_bytes).decode()
                            logger.info(f"✅ PDF base64 rigenerato per item {item.get('id')} da {pdf_path}")
                        else:
                            logger.warning(f"⚠️ File PDF non trovato per item {item.get('id')}: {file_path or file_name}")
                    except Exception as e:
                        logger.warning(f"⚠️ Impossibile rigenerare base64 per {item.get('id')}: {e}")
        
        return JSONResponse({
            "success": True,
            "items": items
        })
    except Exception as e:
        logger.error(f"Errore lettura coda watchdog: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Errore durante la lettura della coda: {str(e)}")

@app.post("/api/watchdog-queue/{queue_id}/process")
async def process_queue_item(queue_id: str, request: Request, auth: bool = Depends(check_auth)):
    """Marca un elemento della coda come processato"""
    try:
        from app.watchdog_queue import mark_as_processed, remove_item
        mark_as_processed(queue_id)
        # Rimuovi dopo un po' per evitare accumulo
        remove_item(queue_id)
        return JSONResponse({"success": True})
    except Exception as e:
        logger.error(f"Errore processamento coda: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Errore durante il processamento: {str(e)}")

@app.get("/api/ddt/{numero_documento}/reopen")
async def reopen_ddt_preview(numero_documento: str, request: Request, mittente: str = None, auth: bool = Depends(check_auth)):
    """
    Riapre l'anteprima di un DDT già salvato dalla dashboard
    Trova il file PDF corrispondente, estrae i dati e restituisce tutto per la modal
    Cerca prima nella coda watchdog, poi nella cartella inbox
    """
    try:
        import base64
        from app.corrections import get_file_hash
        from app.watchdog_queue import get_all_items
        
        # Normalizza i parametri per il matching
        numero_documento_norm = numero_documento.strip()
        mittente_norm = mittente.strip().upper() if mittente else None
        
        logger.info(f"Ricerca DDT '{numero_documento_norm}' per riapertura anteprima (mittente: {mittente_norm or 'qualsiasi'})")
        
        # PRIMA: Cerca nella coda watchdog (potrebbe avere il PDF base64 già salvato)
        try:
            all_items = get_all_items()
            logger.info(f"📋 Coda watchdog contiene {len(all_items)} elementi")
            for item in all_items:
                extracted_data = item.get("extracted_data", {})
                item_numero = str(extracted_data.get("numero_documento", "")).strip()
                item_mittente = extracted_data.get("mittente", "").strip().upper()
                
                logger.debug(f"  - Item {item.get('id')}: numero={item_numero}, mittente={item_mittente}")
                
                # Match numero documento
                if item_numero == numero_documento_norm:
                    logger.info(f"  ✅ Match numero documento trovato! Verifico mittente...")
                    # Se mittente è fornito, verifica anche quello (case-insensitive)
                    if not mittente_norm or item_mittente == mittente_norm:
                        pdf_base64 = item.get("pdf_base64")
                        if pdf_base64 and len(pdf_base64) > 100:
                            logger.info(f"✅ DDT trovato nella coda watchdog: {item.get('id')}")
                            return JSONResponse({
                                "success": True,
                                "extracted_data": extracted_data,
                                "pdf_base64": pdf_base64,
                                "file_hash": item.get("file_hash", ""),
                                "file_name": item.get("file_name", "documento.pdf")
                            })
                    else:
                        logger.info(f"  ⚠️ Mittente non corrisponde: '{item_mittente}' != '{mittente_norm}'")
        except Exception as e:
            logger.error(f"Errore ricerca nella coda watchdog: {e}", exc_info=True)
        
        # TERZA: Cerca nelle correzioni salvate per trovare il file_hash corrispondente
        try:
            from app.corrections import _load_corrections
            corrections_data = _load_corrections()
            corrections = corrections_data.get("corrections", {})
            logger.info(f"📝 Verifico {len(corrections)} correzioni salvate...")
            
            for correction_id, correction in corrections.items():
                corrected_data = correction.get("corrected_data", {})
                corr_numero = str(corrected_data.get("numero_documento", "")).strip()
                corr_mittente = corrected_data.get("mittente", "").strip().upper()
                
                # Match numero documento e mittente
                if corr_numero == numero_documento_norm:
                    if not mittente_norm or corr_mittente == mittente_norm:
                        # Trovato! Ora cerca il file con questo hash
                        file_path_from_correction = correction.get("file_path", "")
                        if file_path_from_correction and os.path.exists(file_path_from_correction):
                            logger.info(f"✅ File trovato tramite correzione: {file_path_from_correction}")
                            # Estrai i dati dal PDF
                            extracted_data = extract_from_pdf(file_path_from_correction)
                            # Leggi il file PDF e convertilo in base64
                            with open(file_path_from_correction, 'rb') as f:
                                pdf_bytes = f.read()
                            pdf_base64 = base64.b64encode(pdf_bytes).decode()
                            file_hash = get_file_hash(file_path_from_correction)
                            file_name = Path(file_path_from_correction).name
                            
                            return JSONResponse({
                                "success": True,
                                "extracted_data": extracted_data,
                                "pdf_base64": pdf_base64,
                                "file_hash": file_hash,
                                "file_name": file_name
                            })
        except Exception as e:
            logger.debug(f"Errore ricerca nelle correzioni: {e}")
        
        # SECONDA: Cerca il file PDF nella cartella inbox
        pdf_path = None
        inbox_path = Path(INBOX_DIR)
        
        if not inbox_path.exists():
            logger.warning(f"Cartella inbox non trovata: {inbox_path}")
            raise HTTPException(
                status_code=404,
                detail="Cartella inbox non trovata."
            )
        
        # Cerca tra tutti i PDF nella cartella inbox
        pdf_files = list(inbox_path.glob("*.pdf"))
        logger.info(f"📁 Trovati {len(pdf_files)} file PDF nella cartella inbox")
        
        for pdf_file in pdf_files:
            try:
                logger.debug(f"  Verifico file: {pdf_file.name}")
                # Estrai i dati per verificare il numero documento e mittente
                temp_data = extract_from_pdf(str(pdf_file))
                temp_numero = str(temp_data.get("numero_documento", "")).strip()
                temp_mittente = temp_data.get("mittente", "").strip().upper()
                
                logger.debug(f"    Numero: '{temp_numero}' (cercato: '{numero_documento_norm}')")
                logger.debug(f"    Mittente: '{temp_mittente}' (cercato: '{mittente_norm or 'qualsiasi'}')")
                
                # Match numero documento
                if temp_numero == numero_documento_norm:
                    logger.info(f"  ✅ Match numero documento trovato in {pdf_file.name}! Verifico mittente...")
                    # Se mittente è fornito, verifica anche quello (case-insensitive)
                    if not mittente_norm or temp_mittente == mittente_norm:
                        pdf_path = str(pdf_file)
                        logger.info(f"✅ File PDF trovato: {pdf_path}")
                        break
                    else:
                        logger.info(f"  ⚠️ Mittente non corrisponde: '{temp_mittente}' != '{mittente_norm}'")
            except Exception as e:
                logger.warning(f"Errore verifica file {pdf_file}: {e}")
                continue
        
        if not pdf_path or not os.path.exists(pdf_path):
            logger.error(f"❌ File PDF non trovato per DDT '{numero_documento_norm}' (mittente: {mittente_norm or 'qualsiasi'})")
            logger.error(f"   Cercato in: {inbox_path}")
            logger.error(f"   File PDF trovati nella cartella: {[f.name for f in pdf_files]}")
            raise HTTPException(
                status_code=404,
                detail=f"File PDF per DDT '{numero_documento_norm}' non trovato nella cartella inbox. Il file potrebbe essere stato spostato o eliminato dopo il salvataggio."
            )
        
        logger.info(f"Riapertura anteprima DDT '{numero_documento_norm}' da file: {pdf_path}")
        
        # Estrai i dati dal PDF
        extracted_data = extract_from_pdf(pdf_path)
        
        # Leggi il file PDF e convertilo in base64
        with open(pdf_path, 'rb') as f:
            pdf_bytes = f.read()
        pdf_base64 = base64.b64encode(pdf_bytes).decode()
        
        # Calcola hash del file
        file_hash = get_file_hash(pdf_path)
        file_name = Path(pdf_path).name
        
        return JSONResponse({
            "success": True,
            "extracted_data": extracted_data,
            "pdf_base64": pdf_base64,
            "file_hash": file_hash,
            "file_name": file_name
        })
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Errore riapertura anteprima DDT: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Errore durante la riapertura dell'anteprima: {str(e)}")

@app.post("/data/clear")
async def delete_all_ddt(request: Request, auth: bool = Depends(check_auth)):
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
    local_ip = SERVER_IP  # Usa l'IP configurato
    
    # Stampa le informazioni prima di avviare il server
    print("\n" + "="*60)
    print("🚀 Server FastAPI avviato")
    print("="*60)
    print(f"📍 Host: {host} (tutte le interfacce)")
    print(f"🌐 IP Configurato: {local_ip}")
    print(f"🔌 Porta: {port}")
    print(f"🔗 URL Locale: http://127.0.0.1:{port}")
    print(f"🔗 URL Rete: http://{local_ip}:{port}")
    print("="*60 + "\n")
    
    # Avvia il server su tutte le interfacce (0.0.0.0)
    uvicorn.run(app, host=host, port=port)