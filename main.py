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

from app.extract import extract_from_pdf, generate_preview_png
from app.excel import append_to_excel, read_excel_as_dict, clear_all_ddt
from app.config import INBOX_DIR, SERVER_IP
from app.logging_config import setup_logging
from app.routers import rules_router, reprocess_router, preview_router, layout_router, models_router
from app.corrections import get_file_hash
from app.auth import (
    get_session_middleware,
    is_authenticated,
    require_auth,
    login_user,
    logout_user
)
from fastapi import FastAPI

app = FastAPI()

@app.get("/health", include_in_schema=False)
def health():
    return {"status": "ok"}
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
        """Inizializza l'handler con il sistema di tracking persistente"""
        super().__init__()
    
    def _process_pdf(self, file_path: str):
        """Processa un file PDF rilevato dal watchdog - aggiunge alla coda per anteprima"""
        if not self._is_pdf_file(file_path):
            return
        
        # Normalizza il percorso per evitare duplicati
        from app.paths import get_inbox_dir
        file_path_obj = Path(file_path).resolve()
        file_path = str(file_path_obj)
        
        # Verifica che il file sia ancora in inbox (potrebbe essere stato spostato)
        inbox_path = get_inbox_dir()
        if not str(file_path_obj).startswith(str(inbox_path.resolve())):
            logger.debug(f"⏭️ File non in inbox, ignoro: {Path(file_path).name}")
            return
        
        # Attendi che il file sia completamente scritto (aumentato a 15 secondi per file grandi)
        if not self._wait_for_file_ready(file_path, max_wait=15):
            logger.warning(f"⏳ File non pronto dopo l'attesa: {file_path}")
            return
        
        try:
            from app.processed_documents import (
                calculate_file_hash,
                should_process_document,
                register_document,
                mark_document_error,
                DocumentStatus,
                is_document_finalized
            )
            
            # Calcola hash SHA256 PRIMA di qualsiasi controllo
            doc_hash = calculate_file_hash(file_path)
            
            # Verifica se il documento è già FINALIZED (doppio controllo per sicurezza)
            if is_document_finalized(doc_hash):
                logger.info(f"⏭️ Documento già FINALIZED (hash={doc_hash[:16]}...), ignoro evento watchdog - {Path(file_path).name}")
                return
            
            # Verifica se il documento dovrebbe essere processato
            should_process, reason = should_process_document(doc_hash)
            
            if not should_process:
                if reason == "already_finalized":
                    logger.info(f"⏭️ Documento già FINALIZED (hash={doc_hash[:16]}...), ignoro evento watchdog - {Path(file_path).name}")
                elif reason == "error_final":
                    logger.info(f"⏭️ Documento in ERROR_FINAL (hash={doc_hash[:16]}...), ignoro evento watchdog - {Path(file_path).name}")
                elif reason == "already_processing":
                    logger.info(f"⏭️ Documento già in PROCESSING (hash={doc_hash[:16]}...), ignoro evento watchdog - {Path(file_path).name}")
                elif reason == "already_ready":
                    logger.debug(f"⏭️ Documento già READY (hash={doc_hash[:16]}...), ignoro evento watchdog - {Path(file_path).name}")
                else:
                    logger.info(f"⏭️ Documento non processabile: {reason} (hash={doc_hash[:16]}...) - {Path(file_path).name}")
                return
            
            # Registra come PROCESSING
            register_document(file_path, doc_hash, DocumentStatus.PROCESSING)
            
            logger.info(f"📄 Nuovo DDT rilevato: hash={doc_hash[:16]}... file={Path(file_path).name}")
            
            import base64
            from app.watchdog_queue import add_to_queue
            
            # Leggi il file PDF
            from app.paths import safe_open
            file_path_obj = Path(file_path).resolve()
            with safe_open(file_path_obj, 'rb') as f:
                pdf_bytes = f.read()
            
            if len(pdf_bytes) == 0:
                logger.warning(f"⚠️ File PDF vuoto: {file_path}")
                mark_document_error(doc_hash, "File PDF vuoto")
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
                        logger.info(f"⏭️ DDT già presente in Excel (numero: {data.get('numero_documento')}), marco come FINALIZED - {Path(file_path).name}")
                        from app.processed_documents import mark_document_finalized
                        mark_document_finalized(doc_hash)
                        return
            except Exception as e:
                logger.debug(f"Errore controllo Excel: {e}")
                # Continua comunque
            
            # Converti PDF in base64
            pdf_base64 = base64.b64encode(pdf_bytes).decode()
            
            # Genera PNG di anteprima
            preview_generated = False
            try:
                preview_path = generate_preview_png(file_path, doc_hash)
                if preview_path:
                    logger.info(f"✅ PNG anteprima generata: {preview_path}")
                    preview_generated = True
                else:
                    logger.warning(f"⚠️ Impossibile generare PNG anteprima per {doc_hash[:16]}...")
            except Exception as e:
                logger.warning(f"⚠️ Errore generazione PNG anteprima: {e}")
            
            # Aggiungi alla coda per l'anteprima
            queue_id = add_to_queue(file_path, data, pdf_base64, doc_hash)
            logger.info(f"📋 DDT aggiunto alla coda per anteprima: queue_id={queue_id} hash={doc_hash[:16]}... numero={data.get('numero_documento', 'N/A')}")
            
            # Marca come READY quando tutto è pronto (dati estratti + PNG + coda)
            # Questo permette alla dashboard di distinguere PROCESSING reali da READY
            from app.processed_documents import mark_document_ready
            mark_document_ready(doc_hash, queue_id)
            logger.info(f"✅ Documento READY per anteprima: hash={doc_hash[:16]}... numero={data.get('numero_documento', 'N/A')}")
            
        except ValueError as e:
            logger.error(f"❌ Errore validazione DDT: {e}")
            if 'doc_hash' in locals():
                mark_document_error(doc_hash, f"Errore validazione: {str(e)}")
        except FileNotFoundError:
            logger.warning(f"⚠️ File non trovato (potrebbe essere stato spostato): {file_path}")
            if 'doc_hash' in locals():
                mark_document_error(doc_hash, "File non trovato")
        except Exception as e:
            logger.error(f"❌ Errore nel parsing DDT: {e}", exc_info=True)
            if 'doc_hash' in locals():
                mark_document_error(doc_hash, f"Errore parsing: {str(e)}")
    
    def on_created(self, event):
        """Gestisce SOLO l'evento di creazione file (ignora modified per idempotenza)"""
        # Filtra SOLO file PDF (non directory)
        if event.is_directory:
            return
        
        # Filtra SOLO file .pdf (case-insensitive)
        if not event.src_path.lower().endswith(".pdf"):
            return
        
        # Usa un thread separato per non bloccare il watchdog
        thread = threading.Thread(target=self._process_pdf, args=(event.src_path,), daemon=True)
        thread.start()
    
    def on_moved(self, event):
        """Gestisce l'evento di spostamento file (quando un file viene copiato/spostato in inbox)"""
        # Filtra SOLO file PDF (non directory)
        if event.is_directory:
            return
        
        # Filtra SOLO file .pdf (case-insensitive)
        if not event.dest_path.lower().endswith(".pdf"):
            return
        
        # Usa un thread separato per non bloccare il watchdog
        thread = threading.Thread(target=self._process_pdf, args=(event.dest_path,), daemon=True)
        thread.start()
    
    def on_modified(self, event):
        """IGNORA completamente gli eventi modified per evitare loop"""
        # NON processare eventi modified - solo on_created e on_moved
        # Questo previene loop quando il file viene modificato dopo la creazione
        return

def start_watcher_background(observer: Observer):
    """Avvia il watcher in background"""
    try:
        observer.start()
        from app.paths import get_inbox_dir
        inbox_path = get_inbox_dir()
        print(f"👀 Watchdog attivo su {inbox_path} - I file PDF vengono processati automaticamente")
        logger.info(f"Watchdog avviato e monitora: {inbox_path}")
    except Exception as e:
        logger.error(f"❌ Errore nell'avvio del watchdog: {e}", exc_info=True)
        print(f"❌ Errore nell'avvio del watchdog: {e}")

@asynccontextmanager
async def lifespan(app: FastAPI):
    # Assicurati che la cartella inbox esista (usando sistema paths centralizzato)
    from app.paths import get_inbox_dir
    inbox_path = get_inbox_dir()
    logger.info(f"📁 Cartella inbox verificata: {inbox_path}")
    
    # Carica layout models all'avvio per loggare disponibilità
    try:
        from app.layout_rules.manager import load_layout_rules
        rules = load_layout_rules()
        if rules:
            logger.info(f"📐 Layout models disponibili all'avvio: {len(rules)} modello(i)")
            # Log per mittente
            from app.layout_rules.manager import normalize_sender
            sender_counts = {}
            for rule_name, rule in rules.items():
                supplier = rule.match.supplier
                sender_norm = normalize_sender(supplier)
                sender_counts[sender_norm] = sender_counts.get(sender_norm, 0) + 1
            for sender_norm, count in sender_counts.items():
                logger.info(f"   📦 Loaded {count} layout model(s) for sender: {sender_norm}")
        else:
            logger.info("📐 Nessun layout model disponibile all'avvio")
    except Exception as e:
        logger.warning(f"⚠️ Errore caricamento layout models all'avvio: {e}")
    
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
from app.paths import get_app_dir
templates = Jinja2Templates(directory=str(get_app_dir() / "templates"))

# Aggiungi middleware per le sessioni (2 ore di durata)
from app.config import SESSION_SECRET_KEY
app.add_middleware(SessionMiddleware, secret_key=SESSION_SECRET_KEY, max_age=7200, same_site="lax", https_only=False)

# Monta la cartella static per CSS e altri file statici
from app.paths import get_app_dir
app.mount("/static", StaticFiles(directory=str(get_app_dir() / "static")), name="static")

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
            from app.paths import get_inbox_dir, safe_copy
            inbox_path = get_inbox_dir()
            
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
            
            # Copia il file nella cartella inbox usando safe_copy
            tmp_path_obj = Path(tmp_path).resolve()
            inbox_saved_path = safe_copy(tmp_path_obj, inbox_saved_path)
            logger.info(f"📁 Copia salvata in inbox: {inbox_saved_path.name}")
            
            # Genera PNG di anteprima se abbiamo l'hash
            if file_hash:
                try:
                    preview_path = generate_preview_png(str(inbox_saved_path), file_hash)
                    if preview_path:
                        logger.info(f"✅ PNG anteprima generata: {preview_path}")
                    else:
                        logger.warning(f"⚠️ Impossibile generare PNG anteprima per {file_hash}")
                except Exception as e:
                    logger.warning(f"⚠️ Errore generazione PNG anteprima: {e}")
            
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

@app.get("/layout-trainer", response_class=HTMLResponse)
async def layout_trainer_page(request: Request):
    """Pagina per insegnare il layout DDT"""
    # Verifica autenticazione e reindirizza se necessario
    if not is_authenticated(request):
        return RedirectResponse(url="/login", status_code=302)
    return templates.TemplateResponse("layout_trainer.html", {"request": request})

@app.get("/models", response_class=HTMLResponse)
async def models_page(request: Request):
    """Pagina per visualizzare i modelli di layout salvati"""
    # Verifica autenticazione e reindirizza se necessario
    if not is_authenticated(request):
        return RedirectResponse(url="/login", status_code=302)
    return templates.TemplateResponse("models.html", {"request": request})

# Include i router per regole, reprocessing e anteprima (dopo le route HTML per evitare conflitti)
app.include_router(rules_router.router)
app.include_router(reprocess_router.router)
app.include_router(preview_router.router)
app.include_router(layout_router.router)
app.include_router(models_router.router)

@app.get("/data")
async def get_data(request: Request, auth: bool = Depends(check_auth)):
    """Endpoint API per ottenere tutti i DDT in formato JSON"""
    try:
        return read_excel_as_dict()
    except Exception as e:
        logger.error(f"Errore lettura dati: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Errore durante la lettura dei dati: {str(e)}")

@app.get("/api/document-status/{file_hash}")
async def get_document_status_endpoint(file_hash: str, request: Request, auth: bool = Depends(check_auth)):
    """Endpoint per ottenere lo stato di un documento"""
    try:
        from app.processed_documents import get_document_status
        status = get_document_status(file_hash)
        return JSONResponse({
            "success": True,
            "file_hash": file_hash,
            "status": status
        })
    except Exception as e:
        logger.error(f"Errore lettura stato documento: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Errore durante la lettura dello stato: {str(e)}")

@app.get("/api/watchdog-queue")
async def get_watchdog_queue(request: Request, auth: bool = Depends(check_auth)):
    """Endpoint per ottenere gli elementi in coda dal watchdog - garantisce base64 per rete locale"""
    try:
        from app.watchdog_queue import get_pending_items, cleanup_old_items
        from app.config import INBOX_DIR
        import base64
        
        # Pulisci elementi vecchi periodicamente (ogni volta che si accede alla coda)
        cleanup_old_items()
        
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
                        from app.paths import get_inbox_dir, safe_open
                        pdf_path = None
                        if file_path:
                            pdf_path = Path(file_path)
                            # Se è relativo, prova nella cartella inbox
                            if not pdf_path.is_absolute():
                                inbox_dir = get_inbox_dir()
                                pdf_path = inbox_dir / pdf_path.name
                        
                        # Se non trovato, prova con il file_name nella cartella inbox
                        if not pdf_path or not pdf_path.exists():
                            if file_name:
                                inbox_dir = get_inbox_dir()
                                pdf_path = inbox_dir / file_name
                        
                        # Se trovato, leggi e converti in base64
                        if pdf_path and pdf_path.exists():
                            pdf_path = pdf_path.resolve()
                            with safe_open(pdf_path, 'rb') as f:
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
    """Marca un elemento della coda come processato e FINALIZZA il documento"""
    try:
        from app.watchdog_queue import mark_as_processed, remove_item, get_item_by_id
        from app.processed_documents import mark_document_finalized
        
        # Ottieni l'item dalla coda per recuperare l'hash
        item = get_item_by_id(queue_id)
        if not item:
            raise HTTPException(status_code=404, detail=f"Elemento coda {queue_id} non trovato")
        
        doc_hash = item.get("file_hash")
        if not doc_hash:
            logger.warning(f"⚠️ Item {queue_id} senza file_hash, marco solo come processato")
            mark_as_processed(queue_id)
        else:
            # Marca come processato nella coda
            mark_as_processed(queue_id)
            
            # FINALIZZA il documento nel sistema di tracking
            mark_document_finalized(doc_hash, queue_id)
            logger.info(f"✅ Documento FINALIZED: queue_id={queue_id} hash={doc_hash[:16]}... file={item.get('file_name', 'N/A')}")
        
        # Rimuovi dopo un po' per evitare accumulo
        remove_item(queue_id)
        return JSONResponse({"success": True})
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Errore processamento coda: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Errore durante il processamento: {str(e)}")

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