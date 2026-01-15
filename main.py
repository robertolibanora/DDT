import os
import socket
import threading
import logging
import sys
from pathlib import Path
from contextlib import asynccontextmanager
from fastapi import FastAPI, UploadFile, File, Request, HTTPException, Form, Depends
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.middleware.sessions import SessionMiddleware
from watchdog.observers import Observer
from watchdog.events import FileSystemEventHandler

# PROTEZIONE ANTI-CRASH: Import critici con fallback sicuro
try:
    from app.extract import extract_from_pdf, generate_preview_png
except Exception as e:
    print(f"❌ [CRITICAL] Errore import app.extract: {e}", file=sys.stderr)
    # Fallback: definisce funzioni stub per evitare crash
    def extract_from_pdf(*args, **kwargs):
        raise RuntimeError("extract_from_pdf non disponibile - errore import")
    def generate_preview_png(*args, **kwargs):
        raise RuntimeError("generate_preview_png non disponibile - errore import")

try:
    from app.excel import append_to_excel, read_excel_as_dict, clear_all_ddt
except Exception as e:
    print(f"❌ [CRITICAL] Errore import app.excel: {e}", file=sys.stderr)
    def append_to_excel(*args, **kwargs):
        raise RuntimeError("append_to_excel non disponibile - errore import")
    def read_excel_as_dict(*args, **kwargs):
        raise RuntimeError("read_excel_as_dict non disponibile - errore import")
    def clear_all_ddt(*args, **kwargs):
        raise RuntimeError("clear_all_ddt non disponibile - errore import")

try:
    from app.config import INBOX_DIR, SERVER_IP, DDT_ROLE, IS_WEB_ROLE, IS_WORKER_ROLE
except Exception as e:
    print(f"❌ [CRITICAL] Errore import app.config: {e}", file=sys.stderr)
    # Fallback valori safe
    INBOX_DIR = "/tmp/ddt_inbox"
    SERVER_IP = "127.0.0.1"
    DDT_ROLE = "web"
    IS_WEB_ROLE = True
    IS_WORKER_ROLE = False

try:
    from app.logging_config import setup_logging
    setup_logging()
except Exception as e:
    print(f"❌ [CRITICAL] Errore setup logging: {e}", file=sys.stderr)
    # Fallback: logging base
    logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')

try:
    from app.routers import rules_router, reprocess_router, preview_router, layout_router, models_router
except Exception as e:
    print(f"❌ [CRITICAL] Errore import routers: {e}", file=sys.stderr)
    # Fallback: routers vuoti
    rules_router = None
    reprocess_router = None
    preview_router = None
    layout_router = None
    models_router = None

try:
    from app.corrections import get_file_hash
except Exception as e:
    print(f"❌ [CRITICAL] Errore import app.corrections: {e}", file=sys.stderr)
    import hashlib
    def get_file_hash(file_path):
        """Fallback hash usando hashlib"""
        try:
            with open(file_path, 'rb') as f:
                return hashlib.sha256(f.read()).hexdigest()
        except Exception:
            return "unknown"

try:
    from app.auth import (
        get_session_middleware,
        is_authenticated,
        require_auth,
        login_user,
        logout_user
    )
except Exception as e:
    print(f"❌ [CRITICAL] Errore import app.auth: {e}", file=sys.stderr)
    # Fallback: auth disabilitato
    def get_session_middleware():
        return None
    def is_authenticated(request):
        return True  # Fallback: autenticazione disabilitata
    def require_auth(func):
        return func
    def login_user(*args, **kwargs):
        return None
    def logout_user(*args, **kwargs):
        return None

from fastapi import FastAPI
from typing import Optional

logger = logging.getLogger(__name__)

# Variabili globali per gestione shutdown (tutti i thread/task avviati)
# REGOLA FERREA: TUTTI i thread DEVONO essere daemon=True per permettere shutdown veloce
_global_observer: Optional[Observer] = None
_cleanup_thread: Optional[threading.Thread] = None
_shutdown_in_progress = False
_cleanup_shutdown_flag = threading.Event()  # Flag per fermare il cleanup loop


def stop_watchdog_safely():
    """
    Ferma il watchdog observer in modo sicuro.
    Gestisce timeout e errori durante lo shutdown.
    """
    global _global_observer, _shutdown_in_progress
    
    if _shutdown_in_progress:
        logger.debug("⚠️ [STOP_WATCHDOG] Shutdown già in corso, skip")
        return
    
    _shutdown_in_progress = True
    logger.info("🛑 [STOP_WATCHDOG] Inizio fermata watchdog...")
    
    if _global_observer is None:
        logger.debug("⚠️ [STOP_WATCHDOG] Observer non inizializzato, skip")
        return
    
    try:
        if _global_observer.is_alive():
            logger.info("🛑 [STOP_WATCHDOG] Observer attivo, chiamata stop()...")
            _global_observer.stop()
            logger.info("🛑 [STOP_WATCHDOG] Attesa terminazione observer (timeout 5s)...")
            _global_observer.join(timeout=5.0)
            
            if _global_observer.is_alive():
                logger.warning("⚠️ [STOP_WATCHDOG] Watchdog non terminato entro timeout di 5 secondi")
            else:
                logger.info("✅ [STOP_WATCHDOG] Watchdog fermato correttamente")
        else:
            logger.debug("ℹ️ [STOP_WATCHDOG] Watchdog già fermato")
    except Exception as e:
        logger.error(f"❌ [STOP_WATCHDOG] Errore durante lo shutdown del watchdog: {e}", exc_info=True)
    finally:
        _global_observer = None
        logger.info("✅ [STOP_WATCHDOG] Cleanup completato")


def stop_cleanup_thread_safely():
    """
    Ferma il thread di cleanup STUCK in modo sicuro.
    Imposta il flag di shutdown e attende la terminazione del thread.
    """
    global _cleanup_thread, _cleanup_shutdown_flag
    
    logger.info("🧹 [STOP_CLEANUP] Inizio fermata cleanup thread...")
    
    if _cleanup_thread is None:
        logger.debug("⚠️ [STOP_CLEANUP] Cleanup thread non inizializzato, skip")
        return
    
    try:
        if _cleanup_thread.is_alive():
            logger.info("🧹 [STOP_CLEANUP] Thread attivo, impostazione flag shutdown...")
            # Imposta flag di shutdown per interrompere il loop
            _cleanup_shutdown_flag.set()
            logger.info("🧹 [STOP_CLEANUP] Attesa terminazione thread (timeout 2s)...")
            # Attendi terminazione thread (timeout 2 secondi)
            _cleanup_thread.join(timeout=2.0)
            
            if _cleanup_thread.is_alive():
                logger.warning("⚠️ [STOP_CLEANUP] Cleanup thread non terminato entro timeout di 2 secondi")
            else:
                logger.info("✅ [STOP_CLEANUP] Cleanup thread fermato correttamente")
        else:
            logger.debug("ℹ️ [STOP_CLEANUP] Cleanup thread già fermato")
    except Exception as e:
        logger.error(f"❌ [STOP_CLEANUP] Errore durante lo shutdown del cleanup thread: {e}", exc_info=True)
    finally:
        _cleanup_thread = None
        _cleanup_shutdown_flag.clear()
        logger.info("✅ [STOP_CLEANUP] Cleanup completato")


# Gestione segnali rimossa - uvicorn gestisce SIGTERM/SIGINT automaticamente
                            
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
        """
        Processa un file PDF rilevato dal watchdog - aggiunge alla coda per anteprima.
        
        IMPORTANTE: Questa funzione è SEMPRE eseguita in un thread daemon separato
        (chiamata da on_created/on_moved) per NON bloccare mai il watchdog filesystem.
        Operazioni pesanti (extract_from_pdf, I/O filesystem) sono accettabili qui.
        """
        logger.info(f"📄 [PROCESS_PDF] Avvio processing PDF: {Path(file_path).name}")
        
        if not self._is_pdf_file(file_path):
            logger.debug(f"⏭️ [PROCESS_PDF] File non PDF, ignoro: {file_path}")
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
                elif reason == "already_ready" or reason == "already_ready_for_review":
                    logger.debug(f"⏭️ Documento già READY_FOR_REVIEW (hash={doc_hash[:16]}...), ignoro evento watchdog - {Path(file_path).name}")
                else:
                    logger.info(f"⏭️ Documento non processabile: {reason} (hash={doc_hash[:16]}...) - {Path(file_path).name}")
                return
            
            # REGOLA FERREA: Usa transition_document_state invece di register_document
            from app.processed_documents import transition_document_state
            transition_document_state(
                doc_hash=doc_hash,
                from_state=None,
                to_state=DocumentStatus.PROCESSING,
                reason="Watchdog rilevato nuovo PDF - avvio processing",
                metadata={
                    "file_path": file_path,
                    "file_name": Path(file_path).name
                }
            )
            
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
            # OPERAZIONE PESANTE: extract_from_pdf può richiedere secondi/minuti
            # OK perché siamo già in un thread daemon separato (non blocca watchdog)
            logger.info(f"🔍 [PROCESS_PDF] Avvio estrazione dati da PDF: {Path(file_path).name}")
            data = extract_from_pdf(file_path)
            extraction_mode = data.pop("_extraction_mode", None)  # Estrai extraction_mode dal risultato
            ai_fallback_used = data.pop("_ai_fallback_used", False)  # Estrai ai_fallback_used dal risultato
            ai_fallback_fields = data.pop("_ai_fallback_fields", [])  # Estrai ai_fallback_fields dal risultato
            if ai_fallback_used:
                logger.warning(f"⚠️ [PROCESS_PDF] AI fallback utilizzato: campi={ai_fallback_fields}")
            logger.info(f"✅ [PROCESS_PDF] Estrazione dati completata: {Path(file_path).name} (mode={extraction_mode}, ai_fallback_used={ai_fallback_used})")
            
            # Verifica se questo numero documento è già in Excel (controllo finale)
            try:
                from app.excel import read_excel_as_dict
                existing_data = read_excel_as_dict()
                for row in existing_data.get("rows", []):
                    if (row.get("numero_documento") == data.get("numero_documento") and 
                        row.get("mittente", "").strip() == data.get("mittente", "").strip()):
                        logger.info("⏭️ DDT già presente in Excel (numero: %s), marco come FINALIZED - %s", 
                                  data.get('numero_documento'), Path(file_path).name)
                        from app.processed_documents import mark_document_finalized
                        mark_document_finalized(doc_hash)
                        return
            except (OSError, IOError, PermissionError) as e:
                # Errori di I/O su path critici: logga ma continua (non bloccare il processing)
                # Questo è in un thread daemon, quindi non possiamo sollevare HTTPException
                logger.error("Errore I/O controllo Excel (path critico): %s - continuo processing", str(e))
                # Continua comunque
            except Exception as e:
                logger.debug("Errore controllo Excel: %s", str(e))
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
            
            # Aggiungi alla coda per l'anteprima (con extraction_mode e ai_fallback_used)
            logger.info(f"📋 [PROCESS_PDF] Aggiunta alla coda watchdog: {Path(file_path).name}")
            queue_id = add_to_queue(file_path, data, pdf_base64, doc_hash, extraction_mode, ai_fallback_used=ai_fallback_used, ai_fallback_fields=ai_fallback_fields)
            logger.info(f"✅ [PROCESS_PDF] DDT aggiunto alla coda: queue_id={queue_id} hash={doc_hash[:16]}... numero={data.get('numero_documento', 'N/A')}")
            
            # Marca come READY_FOR_REVIEW quando tutto è pronto (dati estratti + PNG + coda)
            # Questo permette alla dashboard di distinguere PROCESSING (tecnico) da READY_FOR_REVIEW (funzionale)
            from app.processed_documents import mark_document_ready
            mark_document_ready(doc_hash, queue_id, extraction_mode)
            logger.info(f"✅ [PROCESS_PDF] Documento READY_FOR_REVIEW: hash={doc_hash[:16]}... numero={data.get('numero_documento', 'N/A')} extraction_mode={extraction_mode or 'N/A'}")
            
        except ValueError as e:
            logger.error(f"❌ [PROCESS_PDF] Errore validazione DDT: {e}")
            if 'doc_hash' in locals():
                mark_document_error(doc_hash, f"Errore validazione: {str(e)}")
        except FileNotFoundError:
            logger.warning(f"⚠️ [PROCESS_PDF] File non trovato (potrebbe essere stato spostato): {file_path}")
            if 'doc_hash' in locals():
                mark_document_error(doc_hash, "File non trovato")
        except Exception as e:
            logger.error(f"❌ [PROCESS_PDF] Errore nel parsing DDT: {e}", exc_info=True)
            if 'doc_hash' in locals():
                mark_document_error(doc_hash, f"Errore parsing: {str(e)}")
        finally:
            logger.info(f"🏁 [PROCESS_PDF] Processing completato: {Path(file_path).name}")
    
    def on_created(self, event):
        """
        Gestisce SOLO l'evento di creazione file (ignora modified per idempotenza).
        
        IMPORTANTE: _process_pdf() viene SEMPRE eseguito in thread daemon separato
        per NON bloccare mai il watchdog filesystem. Operazioni pesanti sono accettabili.
        """
        # Filtra SOLO file PDF (non directory)
        if event.is_directory:
            return
        
        # Filtra SOLO file .pdf (case-insensitive)
        if not event.src_path.lower().endswith(".pdf"):
            return
        
        # Usa un thread separato per non bloccare il watchdog (NON-BLOCCANTE)
        # REGOLA FERREA: daemon=True per permettere shutdown veloce
        logger.debug(f"📄 [WATCHDOG] Evento on_created: {Path(event.src_path).name}, avvio thread processing...")
        thread = threading.Thread(target=self._process_pdf, args=(event.src_path,), daemon=True)
        thread.start()
        logger.debug(f"✅ [WATCHDOG] Thread processing avviato per: {Path(event.src_path).name}")
    
    def on_moved(self, event):
        """
        Gestisce l'evento di spostamento file (quando un file viene copiato/spostato in inbox).
        
        IMPORTANTE: _process_pdf() viene SEMPRE eseguito in thread daemon separato
        per NON bloccare mai il watchdog filesystem. Operazioni pesanti sono accettabili.
        """
        # Filtra SOLO file PDF (non directory)
        if event.is_directory:
            return
        
        # Filtra SOLO file .pdf (case-insensitive)
        if not event.dest_path.lower().endswith(".pdf"):
            return
        
        # Usa un thread separato per non bloccare il watchdog (NON-BLOCCANTE)
        # REGOLA FERREA: daemon=True per permettere shutdown veloce
        logger.debug(f"📄 [WATCHDOG] Evento on_moved: {Path(event.dest_path).name}, avvio thread processing...")
        thread = threading.Thread(target=self._process_pdf, args=(event.dest_path,), daemon=True)
        thread.start()
        logger.debug(f"✅ [WATCHDOG] Thread processing avviato per: {Path(event.dest_path).name}")
    
    def on_modified(self, event):
        """IGNORA completamente gli eventi modified per evitare loop"""
        # NON processare eventi modified - solo on_created e on_moved
        # Questo previene loop quando il file viene modificato dopo la creazione
        return

def start_watcher_background(observer: Observer):
    """
    Avvia il watcher in background.
    
    IMPORTANTE: observer.start() è NON-BLOCCANTE (watchdog usa thread interni).
    Questa funzione viene eseguita in un thread daemon separato per sicurezza.
    """
    logger.info("👀 [WATCHDOG] Avvio watchdog observer...")
    try:
        observer.start()
        from app.paths import get_inbox_dir
        inbox_path = get_inbox_dir()
        print(f"👀 Watchdog attivo su {inbox_path} - I file PDF vengono processati automaticamente")
        logger.info(f"✅ [WATCHDOG] Watchdog avviato e monitora: {inbox_path}")
    except Exception as e:
        logger.error(f"❌ [WATCHDOG] Errore nell'avvio del watchdog: {e}", exc_info=True)
        print(f"❌ Errore nell'avvio del watchdog: {e}")

@asynccontextmanager
async def lifespan(app: FastAPI):
    """
    Lifespan FastAPI - gestisce startup e shutdown.
    
    IMPORTANTE: Se DDT_ROLE=web, NON avvia watchdog/processing/cleanup.
    Questi vengono gestiti da worker.py separato.
    """
    # Determina ruolo processo
    role_label = "[WEB]" if IS_WEB_ROLE else "[WORKER]"
    logger.info(f"{role_label} Ruolo processo: {DDT_ROLE.upper()}")
    
    # FIX CRITICO: Startup deve essere NON-BLOCCANTE (< 10ms)
    # Tutte le operazioni lunghe vengono spostate in thread daemon
    
    # Assicurati che la cartella inbox esista (usando sistema paths centralizzato)
    # Se la directory non è scrivibile, get_inbox_dir() solleverà OSError esplicitamente
    # Questo è CRITICO: il sistema deve fallire chiaramente se i path critici non sono scrivibili
    try:
        from app.paths import get_inbox_dir
        inbox_path = get_inbox_dir()
        logger.info("%s Cartella inbox verificata: %s", role_label, str(inbox_path))
    except (OSError, IOError, PermissionError) as e:
        # Path critico non scrivibile: logga e rilancia (il sistema deve fermarsi)
        logger.critical("%s ERRORE CRITICO: Directory inbox non scrivibile: %s", role_label, str(e))
        logger.critical("%s Il sistema non può funzionare senza directory inbox scrivibile", role_label)
        raise
    
    # Sposta operazioni lunghe in thread daemon (NON bloccanti)
    # SOLO per ruoli web: task leggeri (migrazione, layout models, cleanup coda)
    # PROTEZIONE ANTI-CRASH: Ogni task è isolato e non blocca lo startup
    def init_background_tasks():
        """Inizializza task in background (migrazione, layout models, cleanup coda)
        
        PROTEZIONE ANTI-CRASH:
        - Ogni task è isolato in try/except
        - Se un task fallisce, gli altri continuano
        - Il server si avvia comunque anche se alcuni task falliscono
        """
        role_label = "[WEB]" if IS_WEB_ROLE else "[WORKER]"
        logger.info("%s [BACKGROUND_TASKS] Avvio task iniziali in background...", role_label)
        
        # Task 1: Migrazione stati (isolato)
        try:
            logger.info("%s [BACKGROUND_TASKS] Avvio migrazione stati...", role_label)
            from app.processed_documents import migrate_ready_to_ready_for_review
            migrated_count = migrate_ready_to_ready_for_review()
            if migrated_count > 0:
                logger.info("%s [BACKGROUND_TASKS] Migrazione stati completata: %d documento(i) migrato(i)", role_label, migrated_count)
            else:
                logger.info("%s [BACKGROUND_TASKS] Migrazione stati: nessun documento da migrare", role_label)
        except SyntaxError as e:
            logger.error("%s [BACKGROUND_TASKS] ❌ [CRITICAL] SyntaxError in migrazione stati: %s - sistema operativo in modalità degradata", role_label, str(e))
        except ImportError as e:
            logger.error("%s [BACKGROUND_TASKS] ❌ [CRITICAL] ImportError in migrazione stati: %s - sistema operativo in modalità degradata", role_label, str(e))
        except Exception as e:
            logger.error("%s [BACKGROUND_TASKS] Errore migrazione stati: %s", role_label, str(e), exc_info=True)
        
        # Task 2: Caricamento layout models (isolato)
        try:
            logger.info("%s [BACKGROUND_TASKS] Avvio caricamento layout models...", role_label)
            from app.layout_rules.manager import load_layout_rules
            rules = load_layout_rules()
            if rules:
                logger.info("%s [BACKGROUND_TASKS] Layout models disponibili: %d modello(i)", role_label, len(rules))
                # Log per mittente
                try:
                    from app.layout_rules.manager import normalize_sender
                    sender_counts = {}
                    for rule_name, rule in rules.items():
                        try:
                            supplier = rule.match.supplier
                            sender_norm = normalize_sender(supplier)
                            sender_counts[sender_norm] = sender_counts.get(sender_norm, 0) + 1
                        except Exception as e:
                            logger.warning("%s [BACKGROUND_TASKS] Errore processing regola %s: %s", role_label, rule_name, str(e))
                            continue
                    for sender_norm, count in sender_counts.items():
                        logger.info("   📦 %s [BACKGROUND_TASKS] Loaded %d layout model(s) for sender: %s", role_label, count, sender_norm)
                except Exception as e:
                    logger.warning("%s [BACKGROUND_TASKS] Errore logging mittenti: %s", role_label, str(e))
            else:
                logger.warning("%s [BACKGROUND_TASKS] ⚠️ Nessun layout model disponibile - sistema operativo ma userà AI fallback", role_label)
                logger.info("%s [HEARTBEAT] Sistema operativo – nessun documento in elaborazione – 0 layout models", role_label)
        except SyntaxError as e:
            logger.error("%s [BACKGROUND_TASKS] ❌ [CRITICAL] SyntaxError in caricamento layout models: %s - sistema operativo in modalità degradata", role_label, str(e))
            logger.info("%s [HEARTBEAT] Sistema operativo – nessun documento in elaborazione – errore caricamento layout models (SyntaxError)", role_label)
        except ImportError as e:
            logger.error("%s [BACKGROUND_TASKS] ❌ [CRITICAL] ImportError in caricamento layout models: %s - sistema operativo in modalità degradata", role_label, str(e))
            logger.info("%s [HEARTBEAT] Sistema operativo – nessun documento in elaborazione – errore caricamento layout models (ImportError)", role_label)
        except Exception as e:
            logger.error("%s [BACKGROUND_TASKS] Errore caricamento layout models: %s", role_label, str(e), exc_info=True)
            logger.info("%s [HEARTBEAT] Sistema operativo – nessun documento in elaborazione – errore caricamento layout models", role_label)
        
        # Task 3: Carica e pulisci coda watchdog (isolato)
        try:
            logger.info("%s [BACKGROUND_TASKS] Avvio caricamento e pulizia coda watchdog...", role_label)
            from app.watchdog_queue import _load_queue, cleanup_old_items
            _load_queue()
            removed_count = cleanup_old_items()
            if removed_count > 0:
                logger.info("%s [BACKGROUND_TASKS] Pulizia coda watchdog: %d elemento(i) rimosso(i)", role_label, removed_count)
            else:
                logger.info("%s [BACKGROUND_TASKS] Pulizia coda watchdog: nessun elemento da rimuovere", role_label)
        except SyntaxError as e:
            logger.error("%s [BACKGROUND_TASKS] ❌ [CRITICAL] SyntaxError in watchdog queue: %s - sistema operativo in modalità degradata", role_label, str(e))
        except ImportError as e:
            logger.error("%s [BACKGROUND_TASKS] ❌ [CRITICAL] ImportError in watchdog queue: %s - sistema operativo in modalità degradata", role_label, str(e))
        except Exception as e:
            logger.error("%s [BACKGROUND_TASKS] Errore caricamento/pulizia coda watchdog: %s", role_label, str(e), exc_info=True)
        
        logger.info("%s [BACKGROUND_TASKS] Tutti i task iniziali completati", role_label)
        logger.info("%s [HEARTBEAT] Sistema operativo – nessun documento in elaborazione – stato idle", role_label)
    
    # Avvia task iniziali in thread daemon (NON bloccante)
    # SOLO task leggeri (migrazione, layout models, cleanup coda) - NO watchdog/processing/cleanup STUCK
    init_thread = threading.Thread(target=init_background_tasks, daemon=True)
    init_thread.start()
    logger.info(f"{role_label} [LIFESPAN] Task iniziali avviati in background thread (migrazione, layout models, cleanup coda)")
    
    # IMPORTANTE: Se DDT_ROLE=web, NON avviare watchdog/processing/cleanup STUCK
    # Questi vengono gestiti da worker.py separato
    if IS_WORKER_ROLE:
        # Startup - avvia il watchdog in background (SOLO per worker)
        logger.info(f"{role_label} [LIFESPAN] Configurazione watchdog filesystem (worker mode)...")
        global _global_observer
        observer = Observer()
        _global_observer = observer  # Salva riferimento globale per shutdown handler
        
        try:
            handler = DDTHandler()  # Crea un'istanza singola dell'handler per mantenere lo stato
            observer.schedule(handler, inbox_path, recursive=False)
            # REGOLA FERREA: daemon=True per permettere shutdown veloce
            watcher_thread = threading.Thread(target=start_watcher_background, args=(observer,), daemon=True)
            watcher_thread.start()
            logger.info(f"{role_label} [LIFESPAN] Watchdog configurato per monitorare: {inbox_path}")
        except Exception as e:
            logger.error(f"{role_label} [LIFESPAN] Errore nella configurazione del watchdog: {e}", exc_info=True)
            _global_observer = None
        
        # Startup - avvia cleanup periodico per documenti STUCK (SOLO per worker)
        global _cleanup_thread, _cleanup_shutdown_flag
        _cleanup_shutdown_flag.clear()  # Reset flag all'avvio
        
        def stuck_cleanup_loop():
            """
            Loop periodico per controllare e marcare documenti PROCESSING bloccati come STUCK.
            
            IMPORTANTE: Eseguito in thread daemon separato, NON blocca mai il main thread.
            Usa Event.wait() invece di time.sleep() per permettere interruzione immediata.
            """
            import time
            from app.processed_documents import check_and_mark_stuck_documents
            # Esegui cleanup ogni 5 minuti
            cleanup_interval = 300  # 5 minuti
            logger.info(f"{role_label} [CLEANUP_LOOP] Cleanup loop STUCK avviato (thread daemon)")
            
            while not _cleanup_shutdown_flag.is_set():
                try:
                    # Usa wait invece di sleep per permettere interruzione immediata (NON-BLOCCANTE)
                    if _cleanup_shutdown_flag.wait(timeout=cleanup_interval):
                        # Flag di shutdown impostato, esci dal loop
                        logger.info(f"{role_label} [CLEANUP_LOOP] Shutdown richiesto, terminazione...")
                        break
                    
                    # Esegui cleanup solo se shutdown non richiesto
                    if not _cleanup_shutdown_flag.is_set():
                        logger.debug(f"{role_label} [CLEANUP_LOOP] Esecuzione controllo STUCK...")
                        stuck_count = check_and_mark_stuck_documents()
                        if stuck_count > 0:
                            logger.info(f"{role_label} [CLEANUP_LOOP] Cleanup STUCK: {stuck_count} documento(i) marcato(i) come STUCK")
                        else:
                            logger.debug(f"{role_label} [CLEANUP_LOOP] Nessun documento STUCK trovato")
                except Exception as e:
                    logger.error(f"{role_label} [CLEANUP_LOOP] Errore nel cleanup STUCK: {e}", exc_info=True)
            
            logger.info(f"{role_label} [CLEANUP_LOOP] Cleanup loop STUCK terminato")
        
        # REGOLA FERREA: daemon=True per permettere shutdown veloce
        # IMPORTANTE: Loop cleanup in thread daemon separato, NON blocca mai il main thread
        logger.info(f"{role_label} [LIFESPAN] Avvio cleanup thread STUCK...")
        _cleanup_thread = threading.Thread(target=stuck_cleanup_loop, daemon=True)
        _cleanup_thread.start()
        logger.info(f"{role_label} [LIFESPAN] Cleanup periodico STUCK avviato (controllo ogni 5 minuti, thread daemon)")
    else:
        # DDT_ROLE=web: NON avviare watchdog/processing/cleanup STUCK
        logger.info(f"{role_label} [LIFESPAN] Ruolo WEB: watchdog/processing/cleanup STUCK DISABILITATI (gestiti da worker.py)")
        _global_observer = None
        _cleanup_thread = None
    
    # Startup completato - yield immediato (NON bloccante)
    logger.info(f"{role_label} [LIFESPAN] Startup completato, yield a uvicorn")
    yield

app = FastAPI(lifespan=lifespan)

@app.on_event("shutdown")
async def shutdown_event():
    """
    Handler FastAPI ufficiale per shutdown.
    Ferma watchdog e cleanup thread senza bloccare (SOLO se DDT_ROLE=worker).
    Uvicorn gestisce automaticamente SIGTERM/SIGINT.
    """
    role_label = "[WEB]" if IS_WEB_ROLE else "[WORKER]"
    logger.critical(f"{role_label} [SHUTDOWN] Shutdown richiesto, arresto thread/observer...")
    
    # SOLO per worker: ferma cleanup thread e watchdog
    if IS_WORKER_ROLE:
        # Ferma cleanup thread PRIMA del watchdog (ordine inverso rispetto startup)
        try:
            logger.info(f"{role_label} [SHUTDOWN] Fermata cleanup thread...")
            stop_cleanup_thread_safely()
            logger.info(f"{role_label} [SHUTDOWN] Cleanup thread fermato")
        except Exception as e:
            logger.error(f"{role_label} [SHUTDOWN] Errore durante shutdown cleanup thread: {e}", exc_info=True)
        
        # Ferma watchdog observer
        try:
            logger.info(f"{role_label} [SHUTDOWN] Fermata watchdog observer...")
            stop_watchdog_safely()
            logger.info(f"{role_label} [SHUTDOWN] Watchdog observer fermato")
        except Exception as e:
            logger.error(f"{role_label} [SHUTDOWN] Errore durante shutdown watchdog: {e}", exc_info=True)
    else:
        logger.info(f"{role_label} [SHUTDOWN] Ruolo WEB: nessun thread/observer da fermare")
    
    logger.critical(f"{role_label} [SHUTDOWN] Shutdown completato (tutti i thread/task fermati)")

@app.get("/health", include_in_schema=False)
def health():
    return {"status": "ok"}

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
    """
    Endpoint per upload manuale di DDT PDF - salva file in inbox e registra come QUEUED.
    
    IMPORTANTE: Il WEB server NON processa mai PDF. Questo endpoint:
    - Salva il file in inbox
    - Registra il documento come QUEUED
    - Restituisce risposta immediata
    
    Il processing completo viene eseguito dal worker.
    """
    if not file.filename.lower().endswith('.pdf'):
        raise HTTPException(status_code=400, detail="Il file deve essere un PDF")
    
    import tempfile
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
        
        logger.info(f"📤 [WEB] Upload manuale file: {file.filename} ({len(content)} bytes)")
        
        # 1. Calcola hash PRIMA di qualsiasi operazione
        from app.processed_documents import (
            calculate_file_hash,
            should_process_document,
            DocumentStatus,
            is_document_finalized,
            transition_document_state
        )
        
        # Calcola hash dal file temporaneo
        file_hash = calculate_file_hash(tmp_path)
        
        # Verifica se documento già finalizzato
        if is_document_finalized(file_hash):
            logger.info(f"⏭️ [WEB] Documento già FINALIZED (hash={file_hash[:16]}...), ignoro upload - {file.filename}")
            raise HTTPException(status_code=400, detail="Documento già finalizzato")
        
        # Verifica se documento dovrebbe essere processato
        should_process, reason = should_process_document(file_hash)
        if not should_process:
            if reason == "already_finalized":
                logger.info(f"⏭️ [WEB] Documento già FINALIZED (hash={file_hash[:16]}...), ignoro upload - {file.filename}")
                raise HTTPException(status_code=400, detail="Documento già finalizzato")
            elif reason == "error_final":
                logger.info(f"⏭️ [WEB] Documento in ERROR_FINAL (hash={file_hash[:16]}...), ignoro upload - {file.filename}")
                raise HTTPException(status_code=400, detail="Documento in errore definitivo")
            elif reason == "already_processing":
                logger.info(f"⏭️ [WEB] Documento già in PROCESSING (hash={file_hash[:16]}...), ignoro upload - {file.filename}")
                raise HTTPException(status_code=400, detail="Documento già in elaborazione")
            elif reason == "already_ready" or reason == "already_ready_for_review":
                logger.info(f"⏭️ [WEB] Documento già READY_FOR_REVIEW (hash={file_hash[:16]}...), ignoro upload - {file.filename}")
                raise HTTPException(status_code=400, detail="Documento già pronto per revisione")
            elif reason == "queued_ready_for_processing":
                logger.info(f"⏭️ [WEB] Documento già QUEUED (hash={file_hash[:16]}...), ignoro upload - {file.filename}")
                raise HTTPException(status_code=400, detail="Documento già in coda per elaborazione")
            else:
                logger.info(f"⏭️ [WEB] Documento non processabile: {reason} (hash={file_hash[:16]}...) - {file.filename}")
                raise HTTPException(status_code=400, detail=f"Documento non processabile: {reason}")
        
        # 2. Salva il file nella cartella inbox
        from app.paths import get_inbox_dir, safe_copy
        inbox_path = get_inbox_dir()
        
        # Genera un nome file basato sul timestamp per facilitare la ricerca
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        safe_filename = f"UPLOAD_{timestamp}_{file.filename}"
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
        logger.info(f"📁 [WEB] File salvato in inbox: {inbox_saved_path.name}")
        
        # 3. Registra come QUEUED (il worker lo processerà)
        try:
            transition_document_state(
                doc_hash=file_hash,
                from_state=None,
                to_state=DocumentStatus.QUEUED,
                reason="Upload manuale - file in coda per processing da worker",
                metadata={
                    "file_path": str(inbox_saved_path),
                    "file_name": file.filename
                }
            )
            logger.info(f"✅ [WEB] Upload queued: hash={file_hash[:16]}... file={file.filename}")
        except Exception as e:
            logger.error(f"❌ [WEB] Errore registrazione upload: {e}", exc_info=True)
            raise HTTPException(status_code=500, detail=f"Errore durante la registrazione: {str(e)}")
        
        # 4. Restituisci risposta immediata (NON processare PDF qui)
        return JSONResponse({
            "success": True,
            "file_hash": file_hash,
            "file_name": file.filename,
            "file_path": str(inbox_saved_path),
            "status": "QUEUED",
            "message": "File caricato con successo. Il processing verrà eseguito dal worker."
        })
        
    except HTTPException:
        # Rilancia HTTPException così com'è
        raise
    except Exception as e:
        logger.error(f"❌ [WEB] Errore durante upload: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Errore durante l'upload: {str(e)}")
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
# PROTEZIONE ANTI-CRASH: Montaggio router isolato - se un router fallisce, gli altri continuano
try:
    if rules_router:
        app.include_router(rules_router.router)
        logger.info("✅ Router 'rules' montato correttamente")
    else:
        logger.warning("⚠️ Router 'rules' non disponibile - skip montaggio")
except SyntaxError as e:
    logger.error("❌ [CRITICAL] SyntaxError in rules_router: %s - router non montato, sistema operativo in modalità degradata", str(e))
except ImportError as e:
    logger.error("❌ [CRITICAL] ImportError in rules_router: %s - router non montato, sistema operativo in modalità degradata", str(e))
except Exception as e:
    logger.error("❌ Errore montaggio router 'rules': %s", str(e), exc_info=True)

try:
    if reprocess_router:
        app.include_router(reprocess_router.router)
        logger.info("✅ Router 'reprocess' montato correttamente")
    else:
        logger.warning("⚠️ Router 'reprocess' non disponibile - skip montaggio")
except SyntaxError as e:
    logger.error("❌ [CRITICAL] SyntaxError in reprocess_router: %s - router non montato, sistema operativo in modalità degradata", str(e))
except ImportError as e:
    logger.error("❌ [CRITICAL] ImportError in reprocess_router: %s - router non montato, sistema operativo in modalità degradata", str(e))
except Exception as e:
    logger.error("❌ Errore montaggio router 'reprocess': %s", str(e), exc_info=True)

try:
    if preview_router:
        app.include_router(preview_router.router)
        logger.info("✅ Router 'preview' montato correttamente")
    else:
        logger.warning("⚠️ Router 'preview' non disponibile - skip montaggio")
except SyntaxError as e:
    logger.error("❌ [CRITICAL] SyntaxError in preview_router: %s - router non montato, sistema operativo in modalità degradata", str(e))
except ImportError as e:
    logger.error("❌ [CRITICAL] ImportError in preview_router: %s - router non montato, sistema operativo in modalità degradata", str(e))
except Exception as e:
    logger.error("❌ Errore montaggio router 'preview': %s", str(e), exc_info=True)

try:
    if layout_router:
        app.include_router(layout_router.router)
        logger.info("✅ Router 'layout' montato correttamente")
    else:
        logger.warning("⚠️ Router 'layout' non disponibile - skip montaggio")
except SyntaxError as e:
    logger.error("❌ [CRITICAL] SyntaxError in layout_router: %s - router non montato, sistema operativo in modalità degradata", str(e))
except ImportError as e:
    logger.error("❌ [CRITICAL] ImportError in layout_router: %s - router non montato, sistema operativo in modalità degradata", str(e))
except Exception as e:
    logger.error("❌ Errore montaggio router 'layout': %s", str(e), exc_info=True)

try:
    if models_router:
        app.include_router(models_router.router)
        logger.info("✅ Router 'models' montato correttamente")
    else:
        logger.warning("⚠️ Router 'models' non disponibile - skip montaggio")
except SyntaxError as e:
    logger.error("❌ [CRITICAL] SyntaxError in models_router: %s - router non montato, sistema operativo in modalità degradata", str(e))
except ImportError as e:
    logger.error("❌ [CRITICAL] ImportError in models_router: %s - router non montato, sistema operativo in modalità degradata", str(e))
except Exception as e:
    logger.error("❌ Errore montaggio router 'models': %s", str(e), exc_info=True)

@app.get("/data")
async def get_data(request: Request, auth: bool = Depends(check_auth)):
    """
    Endpoint API per ottenere tutti i DDT in formato JSON.
    
    IMPORTANTE: NON maschera OSError su path critici (excel directory).
    Se la directory excel non è scrivibile, solleva HTTPException 500 esplicitamente.
    """
    try:
        data = read_excel_as_dict()
        # Garantisce struttura completa anche se read_excel_as_dict() ritorna None o {}
        if not data or not isinstance(data, dict):
            logger.warning("read_excel_as_dict() ha ritornato None o struttura non valida, uso fallback")
            data = {"rows": []}
        
        # Assicura che 'rows' sia sempre presente e sia una lista
        if "rows" not in data or not isinstance(data.get("rows"), list):
            logger.warning("Struttura dati incompleta, normalizzo a lista vuota")
            data = {"rows": []}
        
        # Log informativo se dataset vuoto (non è un errore)
        if len(data.get("rows", [])) == 0:
            logger.info("Dataset DDT vuoto - nessun documento presente")
        
        return JSONResponse(data)
    except (OSError, IOError, PermissionError) as e:
        # Errori di I/O su path critici: NON mascherare, solleva HTTPException 500
        logger.error("Errore I/O lettura dati Excel: %s", str(e), exc_info=True)
        raise HTTPException(
            status_code=500,
            detail=f"Errore accesso directory Excel: {str(e)}. Verifica i permessi di scrittura su /var/www/DDT/excel"
        )
    except Exception as e:
        # Altri errori: fallback per non bloccare il frontend
        logger.error("Errore lettura dati: %s", str(e), exc_info=True)
        return JSONResponse({
            "rows": [],
            "error": "fallback",
            "error_message": str(e)
        })

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

@app.get("/api/stuck-documents")
async def get_stuck_documents_endpoint(request: Request, auth: bool = Depends(check_auth)):
    """Endpoint per ottenere tutti i documenti in stato STUCK"""
    try:
        from app.processed_documents import get_stuck_documents
        stuck_docs = get_stuck_documents()
        return JSONResponse({
            "success": True,
            "count": len(stuck_docs),
            "documents": stuck_docs
        })
    except Exception as e:
        logger.error(f"Errore lettura documenti STUCK: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Errore durante la lettura dei documenti STUCK: {str(e)}")

@app.post("/api/stuck-documents/{file_hash}/reprocess")
async def reprocess_stuck_document_endpoint(file_hash: str, request: Request, auth: bool = Depends(check_auth)):
    """
    Endpoint per riprocessare manualmente un documento STUCK (STUCK → PROCESSING).
    
    Azione manuale utente: riavvia il processing di un documento bloccato.
    """
    try:
        from app.processed_documents import (
            get_document_status, 
            DocumentStatus,
            transition_document_state
        )
        
        # Verifica che sia STUCK
        current_status = get_document_status(file_hash)
        if not current_status or current_status != DocumentStatus.STUCK.value:
            raise HTTPException(
                status_code=400, 
                detail=f"Documento non in stato STUCK (stato attuale: {current_status})"
            )
        
        # Transizione STUCK → PROCESSING
        transition_document_state(
            doc_hash=file_hash,
            from_state=DocumentStatus.STUCK,
            to_state=DocumentStatus.PROCESSING,
            reason="Riprocessamento manuale da STUCK (azione utente)",
            metadata=None
        )
        
        logger.info(f"✅ Documento STUCK riprocessato: hash={file_hash[:16]}... (azione utente)")
        
        return JSONResponse({
            "success": True,
            "message": f"Documento {file_hash[:16]}... riprocessato con successo (STUCK → PROCESSING)"
        })
    except HTTPException:
        raise
    except ValueError as e:
        logger.error(f"Errore validazione transizione STUCK → PROCESSING: {e}")
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.error(f"Errore riprocessamento documento STUCK: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Errore durante il riprocessamento: {str(e)}")

@app.post("/api/stuck-documents/{file_hash}/reset")
async def reset_stuck_document_endpoint(file_hash: str, request: Request, auth: bool = Depends(check_auth)):
    """
    DEPRECATO: Usa /reprocess invece.
    Endpoint per resettare manualmente un documento STUCK a NEW (permette riprocessamento).
    Mantenuto per backward compatibility.
    """
    try:
        from app.processed_documents import reset_stuck_to_new
        success = reset_stuck_to_new(file_hash)
        if success:
            return JSONResponse({
                "success": True,
                "message": f"Documento {file_hash[:16]}... reset a NEW con successo"
            })
        else:
            raise HTTPException(status_code=404, detail="Documento non trovato o non in stato STUCK")
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Errore reset documento STUCK: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Errore durante il reset: {str(e)}")

@app.post("/api/stuck-documents/{file_hash}/convert-to-error")
async def convert_stuck_to_error_endpoint(
    file_hash: str, 
    request: Request,
    error_message: str = Form(...),
    auth: bool = Depends(check_auth)
):
    """
    Endpoint per convertire un documento STUCK in ERROR_FINAL.
    
    Usa questo quando:
    - Il documento STUCK ha un errore strutturale irreversibile
    - Dopo tentativi di riprocessamento falliti
    - Quando si determina che il problema non è temporaneo
    
    Args:
        file_hash: Hash del documento
        error_message: Messaggio di errore definitivo (obbligatorio)
    """
    try:
        if not error_message or not error_message.strip():
            raise HTTPException(status_code=400, detail="error_message è obbligatorio")
        
        from app.processed_documents import convert_stuck_to_error_final
        success = convert_stuck_to_error_final(file_hash, error_message)
        
        if success:
            return JSONResponse({
                "success": True,
                "message": f"Documento {file_hash[:16]}... convertito a ERROR_FINAL con successo",
                "error_message": error_message
            })
        else:
            raise HTTPException(status_code=404, detail="Documento non trovato o non in stato STUCK")
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Errore conversione STUCK → ERROR_FINAL: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Errore durante la conversione: {str(e)}")

@app.get("/api/watchdog-queue")
async def get_watchdog_queue(request: Request, auth: bool = Depends(check_auth)):
    """
    Endpoint per ottenere gli elementi in coda dal watchdog - garantisce base64 per rete locale.
    
    REGOLA FERREA: Ritorna SEMPRE una struttura completa, anche in caso di errore.
    """
    try:
        from app.watchdog_queue import get_pending_items, cleanup_old_items
        from app.config import INBOX_DIR
        import base64
        
        # Pulisci elementi vecchi periodicamente (ogni volta che si accede alla coda)
        cleanup_old_items()
        
        items = get_pending_items()
        
        # Garantisce che items sia sempre una lista
        if not isinstance(items, list):
            logger.warning("get_pending_items() ha ritornato tipo non valido, normalizzo a lista vuota")
            items = []
        
        # Log informativo se coda vuota (non è un errore)
        if len(items) == 0:
            logger.debug("Coda watchdog vuota - nessun documento in attesa")
        
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
        # REGOLA FERREA: In caso di errore, ritorna struttura completa con campo error
        # NON sollevare HTTPException per non bloccare il frontend
        return JSONResponse({
            "success": False,
            "items": [],
            "error": "fallback",
            "error_message": str(e)
        })

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

@app.get("/api/pending-documents-count")
async def get_pending_documents_count(request: Request, auth: bool = Depends(check_auth)):
    """
    Endpoint per ottenere il numero di documenti in attesa di intervento.
    
    Restituisce il conteggio di documenti in stati:
    - QUEUED: in coda per processing
    - PROCESSING: in elaborazione
    - READY_FOR_REVIEW: pronti per revisione
    - STUCK: bloccati e richiedono azione manuale
    
    REGOLA FERREA: Ritorna SEMPRE una struttura completa, anche in caso di errore.
    """
    try:
        from app.processed_documents import count_pending_documents
        count = count_pending_documents()
        
        # Normalizza count a intero (garantisce tipo corretto)
        count = int(count) if count is not None else 0
        
        # Log informativo se nessun documento in attesa (non è un errore)
        if count == 0:
            logger.debug("Nessun documento in attesa di intervento")
        
        return JSONResponse({
            "success": True,
            "count": count,
            "has_pending": count > 0
        })
    except Exception as e:
        logger.error(f"Errore conteggio documenti in attesa: {e}", exc_info=True)
        # REGOLA FERREA: In caso di errore, ritorna struttura completa con campo error
        # NON sollevare HTTPException per non bloccare il frontend
        return JSONResponse({
            "success": False,
            "count": 0,
            "has_pending": False,
            "error": "fallback",
            "error_message": str(e)
        })

@app.get("/api/config/output-date")
async def get_output_date(request: Request, auth: bool = Depends(check_auth)):
    """
    Endpoint per ottenere la data attiva corrente per la cartella di output.
    
    Restituisce la data in formato gg-mm-yyyy che viene usata per tutti i documenti processati.
    
    REGOLA FERREA: Ritorna SEMPRE una struttura completa, anche in caso di errore.
    """
    try:
        from app.global_config import get_active_output_date
        date_str = get_active_output_date()
        
        # Garantisce che date_str sia sempre una stringa (anche se None)
        if date_str is None:
            logger.warning("get_active_output_date() ha ritornato None, uso fallback")
            date_str = ""
        
        return JSONResponse({
            "success": True,
            "output_date": date_str,
            "format": "gg-mm-yyyy"
        })
    except Exception as e:
        logger.error(f"Errore lettura data output: {e}", exc_info=True)
        # REGOLA FERREA: In caso di errore, ritorna struttura completa con campo error
        # NON sollevare HTTPException per non bloccare il frontend
        return JSONResponse({
            "success": False,
            "output_date": "",
            "format": "gg-mm-yyyy",
            "error": "fallback",
            "error_message": str(e)
        })

@app.post("/api/config/output-date")
async def set_output_date(
    request: Request,
    output_date: str = Form(...),
    auth: bool = Depends(check_auth)
):
    """
    Endpoint per impostare la data attiva per la cartella di output.
    
    Questa data viene usata per TUTTI i documenti processati da questo momento in poi.
    
    Args:
        output_date: Data in formato gg-mm-yyyy (es: "15-01-2026")
    """
    try:
        from app.global_config import set_active_output_date
        set_active_output_date(output_date)
        logger.info(f"📅 [WEB] Data output aggiornata da operatore: {output_date}")
        return JSONResponse({
            "success": True,
            "message": f"Data cartella di output aggiornata: {output_date}",
            "output_date": output_date
        })
    except ValueError as e:
        logger.error(f"Errore validazione data output: {e}")
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.error(f"Errore aggiornamento data output: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Errore durante l'aggiornamento: {str(e)}")

@app.post("/data/clear")
async def delete_all_ddt(request: Request, auth: bool = Depends(check_auth)):
    """
    Endpoint per cancellare tutti i DDT dal file Excel
    
    IMPORTANTE: NON maschera OSError su path critici (excel directory).
    Se la directory non è scrivibile, solleva HTTPException 500 esplicito.
    """
    try:
        result = clear_all_ddt()
        logger.info("Tutti i DDT cancellati: %d righe", result.get('rows_deleted', 0))
        return result
    except (OSError, IOError, PermissionError) as e:
        # Errori di I/O su path critici: solleva HTTPException 500 esplicito
        logger.error("Errore I/O durante cancellazione: %s", str(e), exc_info=True)
        raise HTTPException(
            status_code=500,
            detail=f"Errore accesso directory Excel: {str(e)}. Verifica i permessi di scrittura su /var/www/DDT/excel"
        )
    except ValueError as e:
        logger.error("Errore validazione durante cancellazione: %s", str(e))
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.error("Errore durante la cancellazione: %s", str(e), exc_info=True)
        raise HTTPException(status_code=500, detail=f"Errore durante la cancellazione: {str(e)}")

if __name__ == "__main__":
    print("Avvio tramite systemd + uvicorn CLI")