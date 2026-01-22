#!/usr/bin/env python3
"""
Worker process per DDT Reader.

Gestisce:
- Watchdog filesystem per monitorare inbox
- Processing PDF automatico
- Cleanup periodico documenti STUCK

IMPORTANTE: Questo processo NON avvia FastAPI.
Per il web server, usa main.py con DDT_ROLE=web.
"""

import os
import threading
import logging
import signal
import sys
from pathlib import Path
from typing import Dict, Any
from watchdog.observers import Observer
from watchdog.events import FileSystemEventHandler

# Imposta DDT_ROLE=worker prima di importare config
os.environ["DDT_ROLE"] = "worker"

from app.config import IS_WORKER_ROLE
from app.logging_config import setup_logging
from app.paths import get_inbox_dir

# Configura logging
setup_logging()
logger = logging.getLogger(__name__)

# Variabili globali per gestione shutdown
_global_observer: Observer | None = None
_cleanup_thread: threading.Thread | None = None
_queued_processing_thread: threading.Thread | None = None
_shutdown_in_progress = False
_cleanup_shutdown_flag = threading.Event()
_queued_processing_shutdown_flag = threading.Event()
_shutdown_event = threading.Event()  # Event principale per shutdown

# Semaforo per limitare concorrenza processing PDF (evita saturazione CPU/RAM)
# Default: max 2 PDF processati simultaneamente (configurabile via env var)
_MAX_CONCURRENT_PDF_PROCESSING = int(os.getenv("DDT_MAX_CONCURRENT_PDF", "2"))
_pdf_processing_semaphore = threading.Semaphore(_MAX_CONCURRENT_PDF_PROCESSING)


class DDTHandler(FileSystemEventHandler):
    """
    Handler per eventi filesystem watchdog.
    Processa automaticamente i PDF quando vengono creati/spostati in inbox.
    """
    
    def _process_pdf(self, file_path: str):
        """
        Processa un PDF rilevato dal watchdog.
        
        IMPORTANTE: Questa funzione viene SEMPRE eseguita in thread daemon separato
        per NON bloccare mai il watchdog filesystem. Operazioni pesanti sono accettabili.
        
        Usa semaforo per limitare concorrenza e evitare saturazione CPU/RAM.
        """
        # Flag per tracciare se il semaforo è stato acquisito (evita double-release)
        acquired = False
        
        # Acquisisci semaforo per limitare concorrenza (max _MAX_CONCURRENT_PDF_PROCESSING simultanei)
        if not _pdf_processing_semaphore.acquire(timeout=300):  # Timeout 5 minuti
            logger.error(f"❌ [WORKER] [PROCESS_PDF] Timeout acquisizione semaforo per {Path(file_path).name} - troppi PDF in processing")
            return
        
        # Semaforo acquisito con successo
        acquired = True
        
        try:
            logger.debug(f"📄 [WORKER] [PROCESS_PDF] Rilevato nuovo PDF: {Path(file_path).name}")
            
            from app.processed_documents import (
                calculate_file_hash,
                should_process_document,
                mark_document_error,
                DocumentStatus,
                is_document_finalized
            )
            
            # Calcola hash SHA256 PRIMA di qualsiasi controllo
            doc_hash = calculate_file_hash(file_path)
            
            # Verifica se il documento è già FINALIZED (doppio controllo per sicurezza)
            if is_document_finalized(doc_hash):
                logger.info(f"⏭️ [WORKER] [PROCESS_PDF] Documento già FINALIZED (hash={doc_hash[:16]}...), ignoro evento watchdog - {Path(file_path).name}")
                return
            
            # Verifica se il documento dovrebbe essere processato
            should_process, reason = should_process_document(doc_hash)
            
            if not should_process:
                if reason == "already_finalized":
                    logger.info(f"⏭️ [WORKER] [PROCESS_PDF] Documento già FINALIZED (hash={doc_hash[:16]}...), ignoro evento watchdog - {Path(file_path).name}")
                elif reason == "error_final":
                    logger.info(f"⏭️ [WORKER] [PROCESS_PDF] Documento in ERROR_FINAL (hash={doc_hash[:16]}...), ignoro evento watchdog - {Path(file_path).name}")
                elif reason == "already_processing":
                    logger.info(f"⏭️ [WORKER] [PROCESS_PDF] Documento già in PROCESSING (hash={doc_hash[:16]}...), ignoro evento watchdog - {Path(file_path).name}")
                elif reason == "already_ready" or reason == "already_ready_for_review":
                    logger.debug(f"⏭️ [WORKER] [PROCESS_PDF] Documento già READY_FOR_REVIEW (hash={doc_hash[:16]}...), ignoro evento watchdog - {Path(file_path).name}")
                else:
                    logger.info(f"⏭️ [WORKER] [PROCESS_PDF] Documento non processabile: {reason} (hash={doc_hash[:16]}...) - {Path(file_path).name}")
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
            
            logger.info(f"📄 [WORKER] [PROCESS_PDF] Nuovo DDT rilevato: hash={doc_hash[:16]}... file={Path(file_path).name}")
            
            import base64
            from app.watchdog_queue import add_to_queue
            
            # Leggi il file PDF
            from app.paths import safe_open
            file_path_obj = Path(file_path).resolve()
            with safe_open(file_path_obj, 'rb') as f:
                pdf_bytes = f.read()
            
            if len(pdf_bytes) == 0:
                logger.warning(f"⚠️ [WORKER] [PROCESS_PDF] File PDF vuoto: {file_path}")
                mark_document_error(doc_hash, "File PDF vuoto")
                return
            
            # Estrai i dati (ma NON salvare ancora)
            # OPERAZIONE PESANTE: extract_from_pdf può richiedere secondi/minuti
            # OK perché siamo già in un thread daemon separato (non blocca watchdog)
            logger.info(f"🔍 [WORKER] [PROCESS_PDF] Avvio estrazione dati da PDF: {Path(file_path).name}")
            from app.extract import extract_from_pdf, generate_preview_png
            data = extract_from_pdf(file_path)
            extraction_mode = data.pop("_extraction_mode", None)  # Estrai extraction_mode dal risultato
            ai_fallback_used = data.pop("_ai_fallback_used", False)  # Estrai ai_fallback_used dal risultato
            ai_fallback_fields = data.pop("_ai_fallback_fields", [])  # Estrai ai_fallback_fields dal risultato
            if ai_fallback_used:
                logger.warning(f"⚠️ [WORKER] [PROCESS_PDF] AI fallback utilizzato: campi={ai_fallback_fields}")
            logger.debug(f"✅ [WORKER] [PROCESS_PDF] Estrazione dati completata: {Path(file_path).name} (mode={extraction_mode}, ai_fallback_used={ai_fallback_used})")
            
            # Verifica se questo numero documento è già in Excel (controllo finale)
            try:
                from app.excel import read_excel_as_dict
                existing_data = read_excel_as_dict()
                for row in existing_data.get("rows", []):
                    if (row.get("numero_documento") == data.get("numero_documento") and 
                        row.get("mittente", "").strip() == data.get("mittente", "").strip()):
                        logger.info(f"⏭️ [WORKER] [PROCESS_PDF] DDT già presente in Excel (numero: {data.get('numero_documento')}), marco come FINALIZED - {Path(file_path).name}")
                        from app.processed_documents import mark_document_finalized
                        mark_document_finalized(doc_hash)
                        return
            except Exception as e:
                logger.debug(f"[WORKER] [PROCESS_PDF] Errore controllo Excel: {e}")
                # Continua comunque
            
            # Converti PDF in base64
            pdf_base64 = base64.b64encode(pdf_bytes).decode()
            
            # Genera PNG di anteprima
            preview_generated = False
            try:
                preview_path = generate_preview_png(file_path, doc_hash)
                if preview_path:
                    logger.info(f"✅ [WORKER] [PROCESS_PDF] PNG anteprima generata: {preview_path}")
                    preview_generated = True
                else:
                    logger.warning(f"⚠️ [WORKER] [PROCESS_PDF] Impossibile generare PNG anteprima per {doc_hash[:16]}...")
            except Exception as e:
                logger.warning(f"⚠️ [WORKER] [PROCESS_PDF] Errore generazione PNG anteprima: {e}")
            
            # Aggiungi alla coda per l'anteprima (con extraction_mode e ai_fallback_used)
            logger.debug(f"📋 [WORKER] [PROCESS_PDF] Aggiunta alla coda watchdog: {Path(file_path).name}")
            queue_id = add_to_queue(file_path, data, pdf_base64, doc_hash, extraction_mode, ai_fallback_used=ai_fallback_used, ai_fallback_fields=ai_fallback_fields)
            logger.info(f"✅ [WORKER] [PROCESS_PDF] DDT aggiunto alla coda: queue_id={queue_id} hash={doc_hash[:16]}... numero={data.get('numero_documento', 'N/A')}")
            
            # Marca come READY_FOR_REVIEW quando tutto è pronto (dati estratti + PNG + coda)
            # Questo permette alla dashboard di distinguere PROCESSING (tecnico) da READY_FOR_REVIEW (funzionale)
            from app.processed_documents import mark_document_ready
            mark_document_ready(doc_hash, queue_id, extraction_mode)
            logger.debug(f"✅ [WORKER] [PROCESS_PDF] Documento READY_FOR_REVIEW: hash={doc_hash[:16]}... numero={data.get('numero_documento', 'N/A')} extraction_mode={extraction_mode or 'N/A'}")
            
        except ValueError as e:
            logger.error(f"❌ [WORKER] [PROCESS_PDF] Errore validazione DDT: {e}")
            if 'doc_hash' in locals():
                mark_document_error(doc_hash, f"Errore validazione: {str(e)}")
        except FileNotFoundError:
            logger.warning(f"⚠️ [WORKER] [PROCESS_PDF] File non trovato (potrebbe essere stato spostato): {file_path}")
            if 'doc_hash' in locals():
                mark_document_error(doc_hash, "File non trovato")
        except Exception as e:
            logger.error(f"❌ [WORKER] [PROCESS_PDF] Errore nel parsing DDT: {e}", exc_info=True)
            if 'doc_hash' in locals():
                mark_document_error(doc_hash, f"Errore parsing: {str(e)}")
        finally:
            logger.debug(f"🏁 [WORKER] [PROCESS_PDF] Processing completato: {Path(file_path).name}")
            # Rilascia semaforo solo se acquisito (evita double-release)
            if acquired:
                _pdf_processing_semaphore.release()
                logger.debug(f"🔓 [WORKER] [PROCESS_PDF] Semaforo rilasciato per {Path(file_path).name}")
            else:
                logger.debug(f"⚠️ [WORKER] [PROCESS_PDF] Semaforo non rilasciato (non acquisito) per {Path(file_path).name}")
    
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
        logger.debug(f"📄 [WORKER] [WATCHDOG] Evento on_created: {Path(event.src_path).name}, avvio thread processing...")
        thread = threading.Thread(target=self._process_pdf, args=(event.src_path,), daemon=True)
        thread.start()
        logger.debug(f"✅ [WORKER] [WATCHDOG] Thread processing avviato per: {Path(event.src_path).name}")
    
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
        logger.debug(f"📄 [WORKER] [WATCHDOG] Evento on_moved: {Path(event.dest_path).name}, avvio thread processing...")
        thread = threading.Thread(target=self._process_pdf, args=(event.dest_path,), daemon=True)
        thread.start()
        logger.debug(f"✅ [WORKER] [WATCHDOG] Thread processing avviato per: {Path(event.dest_path).name}")
    
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
    logger.info("👀 [WORKER] [WATCHDOG] Avvio watchdog observer...")
    try:
        observer.start()
        inbox_path = get_inbox_dir()
        print(f"👀 [WORKER] Watchdog attivo su {inbox_path} - I file PDF vengono processati automaticamente")
        logger.info(f"✅ [WORKER] [WATCHDOG] Watchdog avviato e monitora: {inbox_path}")
    except Exception as e:
        logger.error(f"❌ [WORKER] [WATCHDOG] Errore nell'avvio del watchdog: {e}", exc_info=True)
        print(f"❌ [WORKER] Errore nell'avvio del watchdog: {e}")


def stop_watchdog_safely():
    """
    Ferma il watchdog observer in modo sicuro.
    Gestisce timeout e errori durante lo shutdown.
    """
    global _global_observer, _shutdown_in_progress
    
    if _shutdown_in_progress:
        logger.debug("⚠️ [WORKER] [STOP_WATCHDOG] Shutdown già in corso, skip")
        return
    
    _shutdown_in_progress = True
    logger.info("🛑 [WORKER] [STOP_WATCHDOG] Inizio fermata watchdog...")
    
    if _global_observer is None:
        logger.debug("⚠️ [WORKER] [STOP_WATCHDOG] Observer non inizializzato, skip")
        return
    
    try:
        if _global_observer.is_alive():
            logger.info("🛑 [WORKER] [STOP_WATCHDOG] Observer attivo, chiamata stop()...")
            _global_observer.stop()
            logger.info("🛑 [WORKER] [STOP_WATCHDOG] Attesa terminazione observer (timeout 5s)...")
            _global_observer.join(timeout=5.0)
            
            if _global_observer.is_alive():
                logger.warning("⚠️ [WORKER] [STOP_WATCHDOG] Watchdog non terminato entro timeout di 5 secondi")
            else:
                logger.info("✅ [WORKER] [STOP_WATCHDOG] Watchdog fermato correttamente")
        else:
            logger.debug("ℹ️ [WORKER] [STOP_WATCHDOG] Watchdog già fermato")
    except Exception as e:
        logger.error(f"❌ [WORKER] [STOP_WATCHDOG] Errore durante lo shutdown del watchdog: {e}", exc_info=True)
    finally:
        _global_observer = None
        logger.info("✅ [WORKER] [STOP_WATCHDOG] Cleanup completato")


def stop_cleanup_thread_safely():
    """
    Ferma il thread di cleanup STUCK in modo sicuro.
    Imposta il flag di shutdown e attende la terminazione del thread.
    """
    global _cleanup_thread, _cleanup_shutdown_flag
    
    logger.info("🧹 [WORKER] [STOP_CLEANUP] Inizio fermata cleanup thread...")
    
    if _cleanup_thread is None:
        logger.debug("⚠️ [WORKER] [STOP_CLEANUP] Cleanup thread non inizializzato, skip")
        return
    
    try:
        if _cleanup_thread.is_alive():
            logger.info("🧹 [WORKER] [STOP_CLEANUP] Thread attivo, impostazione flag shutdown...")
            # Imposta flag di shutdown per interrompere il loop
            _cleanup_shutdown_flag.set()
            logger.info("🧹 [WORKER] [STOP_CLEANUP] Attesa terminazione thread (timeout 2s)...")
            _cleanup_thread.join(timeout=2.0)
            
            if _cleanup_thread.is_alive():
                logger.warning("⚠️ [WORKER] [STOP_CLEANUP] Cleanup thread non terminato entro timeout di 2 secondi")
            else:
                logger.info("✅ [WORKER] [STOP_CLEANUP] Cleanup thread fermato correttamente")
        else:
            logger.debug("ℹ️ [WORKER] [STOP_CLEANUP] Cleanup thread già fermato")
    except Exception as e:
        logger.error(f"❌ [WORKER] [STOP_CLEANUP] Errore durante lo shutdown del cleanup thread: {e}", exc_info=True)
    finally:
        _cleanup_thread = None
        logger.info("✅ [WORKER] [STOP_CLEANUP] Cleanup completato")


def stop_queued_processing_thread_safely():
    """
    Ferma il thread di processing QUEUED in modo sicuro.
    Imposta il flag di shutdown e attende la terminazione del thread.
    """
    global _queued_processing_thread, _queued_processing_shutdown_flag
    
    logger.info("📋 [WORKER] [STOP_QUEUED] Inizio fermata queued processing thread...")
    
    if _queued_processing_thread is None:
        logger.debug("⚠️ [WORKER] [STOP_QUEUED] Queued processing thread non inizializzato, skip")
        return
    
    try:
        if _queued_processing_thread.is_alive():
            logger.info("📋 [WORKER] [STOP_QUEUED] Thread attivo, impostazione flag shutdown...")
            # Imposta flag di shutdown per interrompere il loop
            _queued_processing_shutdown_flag.set()
            logger.info("📋 [WORKER] [STOP_QUEUED] Attesa terminazione thread (timeout 2s)...")
            _queued_processing_thread.join(timeout=2.0)
            
            if _queued_processing_thread.is_alive():
                logger.warning("⚠️ [WORKER] [STOP_QUEUED] Queued processing thread non terminato entro timeout di 2 secondi")
            else:
                logger.info("✅ [WORKER] [STOP_QUEUED] Queued processing thread fermato correttamente")
        else:
            logger.debug("ℹ️ [WORKER] [STOP_QUEUED] Queued processing thread già fermato")
    except Exception as e:
        logger.error(f"❌ [WORKER] [STOP_QUEUED] Errore durante lo shutdown del queued processing thread: {e}", exc_info=True)
    finally:
        _queued_processing_thread = None
        logger.info("✅ [WORKER] [STOP_QUEUED] Cleanup completato")


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
    logger.info("🔍 [WORKER] [CLEANUP_LOOP] Cleanup loop STUCK avviato (thread daemon)")
    
    while not _cleanup_shutdown_flag.is_set():
        try:
            # Usa wait invece di sleep per permettere interruzione immediata (NON-BLOCCANTE)
            if _cleanup_shutdown_flag.wait(timeout=cleanup_interval):
                # Flag di shutdown impostato, esci dal loop
                logger.info("🧹 [WORKER] [CLEANUP_LOOP] Shutdown richiesto, terminazione...")
                break
            
            # Esegui cleanup solo se shutdown non richiesto
            if not _cleanup_shutdown_flag.is_set():
                logger.debug("🔍 [WORKER] [CLEANUP_LOOP] Esecuzione controllo STUCK...")
                stuck_count = check_and_mark_stuck_documents()
                if stuck_count > 0:
                    logger.info(f"✅ [WORKER] [CLEANUP_LOOP] Cleanup STUCK: {stuck_count} documento(i) marcato(i) come STUCK")
                else:
                    logger.debug("✅ [WORKER] [CLEANUP_LOOP] Nessun documento STUCK trovato")
        except Exception as e:
            logger.error(f"❌ [WORKER] [CLEANUP_LOOP] Errore nel cleanup STUCK: {e}", exc_info=True)
    
    logger.info("✅ [WORKER] [CLEANUP_LOOP] Cleanup loop STUCK terminato")


def process_queued_document(doc_info: Dict[str, Any]) -> None:
    """
    Processa un documento QUEUED (caricato manualmente via /upload).
    
    IMPORTANTE: Questa funzione viene SEMPRE eseguita in thread daemon separato
    per NON bloccare mai il loop principale. Operazioni pesanti sono accettabili.
    
    Usa semaforo per limitare concorrenza e evitare saturazione CPU/RAM.
    
    Args:
        doc_info: Dizionario con informazioni del documento QUEUED (hash, file_path, file_name)
    """
    doc_hash = doc_info.get("hash")
    file_path = doc_info.get("file_path", "")
    file_name = doc_info.get("file_name", "N/A")
    
    if not doc_hash or not file_path:
        logger.warning(f"⚠️ [WORKER] [PROCESS_QUEUED] Informazioni documento incomplete: hash={doc_hash}, path={file_path}")
        return
    
    # Flag per tracciare se il semaforo è stato acquisito (evita double-release)
    acquired = False
    
    # Acquisisci semaforo per limitare concorrenza (max _MAX_CONCURRENT_PDF_PROCESSING simultanei)
    if not _pdf_processing_semaphore.acquire(timeout=300):  # Timeout 5 minuti
        logger.error(f"❌ [WORKER] [PROCESS_QUEUED] Timeout acquisizione semaforo per {file_name} - troppi PDF in processing")
        return
    
    # Semaforo acquisito con successo
    acquired = True
    
    try:
        logger.info(f"📄 [WORKER] [PROCESS_QUEUED] Processing started: hash={doc_hash[:16]}... file={file_name}")
        
        from app.processed_documents import (
            mark_document_error,
            DocumentStatus,
            is_document_finalized,
            transition_document_state
        )
        
        # Verifica che il file esista
        file_path_obj = Path(file_path)
        if not file_path_obj.exists():
            logger.warning(f"⚠️ [WORKER] [PROCESS_QUEUED] File non trovato: {file_path}")
            transition_document_state(
                doc_hash=doc_hash,
                from_state=DocumentStatus.QUEUED,
                to_state=DocumentStatus.ERROR_FINAL,
                reason="File non trovato dopo upload",
                metadata={"error_message": "File non trovato dopo upload"}
            )
            return
        
        # Verifica se il documento è già FINALIZED (doppio controllo)
        if is_document_finalized(doc_hash):
            logger.info(f"⏭️ [WORKER] [PROCESS_QUEUED] Documento già FINALIZED (hash={doc_hash[:16]}...), ignoro - {file_name}")
            return
        
        # Transizione QUEUED → PROCESSING
        transition_document_state(
            doc_hash=doc_hash,
            from_state=DocumentStatus.QUEUED,
            to_state=DocumentStatus.PROCESSING,
            reason="Worker preleva documento QUEUED - avvio processing",
            metadata={
                "file_path": file_path,
                "file_name": file_name
            }
        )
        
        logger.info(f"📄 [WORKER] [PROCESS_QUEUED] Transizione QUEUED → PROCESSING: hash={doc_hash[:16]}... file={file_name}")
        
        import base64
        from app.watchdog_queue import add_to_queue
        
        # Leggi il file PDF
        from app.paths import safe_open
        file_path_obj = file_path_obj.resolve()
        with safe_open(file_path_obj, 'rb') as f:
            pdf_bytes = f.read()
        
        if len(pdf_bytes) == 0:
            logger.warning(f"⚠️ [WORKER] [PROCESS_QUEUED] File PDF vuoto: {file_path}")
            mark_document_error(doc_hash, "File PDF vuoto")
            return
        
        # Estrai i dati (OPERAZIONE PESANTE)
        logger.info(f"🔍 [WORKER] [PROCESS_QUEUED] Avvio estrazione dati da PDF: {file_name}")
        from app.extract import extract_from_pdf, generate_preview_png
        data = extract_from_pdf(file_path)
        extraction_mode = data.pop("_extraction_mode", None)
        ai_fallback_used = data.pop("_ai_fallback_used", False)
        ai_fallback_fields = data.pop("_ai_fallback_fields", [])
        if ai_fallback_used:
            logger.warning(f"⚠️ [WORKER] [PROCESS_QUEUED] AI fallback utilizzato: campi={ai_fallback_fields}")
        logger.info(f"✅ [WORKER] [PROCESS_QUEUED] Estrazione dati completata: {file_name} (mode={extraction_mode}, ai_fallback_used={ai_fallback_used})")
        
        # Verifica se questo numero documento è già in Excel (controllo finale)
        try:
            from app.excel import read_excel_as_dict
            existing_data = read_excel_as_dict()
            for row in existing_data.get("rows", []):
                if (row.get("numero_documento") == data.get("numero_documento") and 
                    row.get("mittente", "").strip() == data.get("mittente", "").strip()):
                    logger.info(f"⏭️ [WORKER] [PROCESS_QUEUED] DDT già presente in Excel (numero: {data.get('numero_documento')}), marco come FINALIZED - {file_name}")
                    from app.processed_documents import mark_document_finalized
                    mark_document_finalized(doc_hash)
                    return
        except Exception as e:
            logger.debug(f"[WORKER] [PROCESS_QUEUED] Errore controllo Excel: {e}")
            # Continua comunque
        
        # Converti PDF in base64
        pdf_base64 = base64.b64encode(pdf_bytes).decode()
        
        # Genera PNG di anteprima
        try:
            preview_path = generate_preview_png(file_path, doc_hash)
            if preview_path:
                logger.info(f"✅ [WORKER] [PROCESS_QUEUED] PNG anteprima generata: {preview_path}")
            else:
                logger.warning(f"⚠️ [WORKER] [PROCESS_QUEUED] Impossibile generare PNG anteprima per {doc_hash[:16]}...")
        except Exception as e:
            logger.warning(f"⚠️ [WORKER] [PROCESS_QUEUED] Errore generazione PNG anteprima: {e}")
        
        # Aggiungi alla coda per l'anteprima (con extraction_mode e ai_fallback_used)
        logger.info(f"📋 [WORKER] [PROCESS_QUEUED] Aggiunta alla coda watchdog: {file_name}")
        queue_id = add_to_queue(file_path, data, pdf_base64, doc_hash, extraction_mode, ai_fallback_used=ai_fallback_used, ai_fallback_fields=ai_fallback_fields)
        logger.info(f"✅ [WORKER] [PROCESS_QUEUED] DDT aggiunto alla coda: queue_id={queue_id} hash={doc_hash[:16]}... numero={data.get('numero_documento', 'N/A')}")
        
        # Marca come READY_FOR_REVIEW quando tutto è pronto
        from app.processed_documents import mark_document_ready
        mark_document_ready(doc_hash, queue_id, extraction_mode)
        logger.info(f"✅ [WORKER] [PROCESS_QUEUED] Documento READY_FOR_REVIEW: hash={doc_hash[:16]}... numero={data.get('numero_documento', 'N/A')} extraction_mode={extraction_mode or 'N/A'}")
        
    except ValueError as e:
        logger.error(f"❌ [WORKER] [PROCESS_QUEUED] Errore validazione DDT: {e}")
        mark_document_error(doc_hash, f"Errore validazione: {str(e)}")
    except FileNotFoundError:
        logger.warning(f"⚠️ [WORKER] [PROCESS_QUEUED] File non trovato: {file_path}")
        mark_document_error(doc_hash, "File non trovato")
    except Exception as e:
        logger.error(f"❌ [WORKER] [PROCESS_QUEUED] Errore nel parsing DDT: {e}", exc_info=True)
        mark_document_error(doc_hash, f"Errore parsing: {str(e)}")
    finally:
        logger.debug(f"🏁 [WORKER] [PROCESS_QUEUED] Processing completato: hash={doc_hash[:16]}... file={file_name}")
        # Rilascia semaforo solo se acquisito (evita double-release)
        if acquired:
            _pdf_processing_semaphore.release()
            logger.debug(f"🔓 [WORKER] [PROCESS_QUEUED] Semaforo rilasciato per {file_name}")
        else:
            logger.debug(f"⚠️ [WORKER] [PROCESS_QUEUED] Semaforo non rilasciato (non acquisito) per {file_name}")


def queued_processing_loop():
    """
    Loop periodico per processare documenti QUEUED (caricati manualmente via /upload).
    
    IMPORTANTE: Eseguito in thread daemon separato, NON blocca mai il main thread.
    Usa Event.wait() invece di time.sleep() per permettere interruzione immediata.
    """
    import time
    from app.processed_documents import get_queued_documents
    # Controlla ogni 10 secondi (più frequente rispetto a cleanup STUCK)
    check_interval = 10  # 10 secondi
    
    logger.info("📋 [WORKER] [QUEUED_LOOP] Loop processing QUEUED avviato (thread daemon)")
    
    while not _queued_processing_shutdown_flag.is_set():
        try:
            # Usa wait invece di sleep per permettere interruzione immediata (NON-BLOCCANTE)
            if _queued_processing_shutdown_flag.wait(timeout=check_interval):
                # Flag di shutdown impostato, esci dal loop
                logger.info("📋 [WORKER] [QUEUED_LOOP] Shutdown richiesto, terminazione...")
                break
            
            # Esegui processing solo se shutdown non richiesto
            if not _queued_processing_shutdown_flag.is_set():
                logger.debug("📋 [WORKER] [QUEUED_LOOP] Controllo documenti QUEUED...")
                queued_docs = get_queued_documents()
                
                if queued_docs:
                    logger.info(f"📋 [WORKER] [QUEUED_LOOP] Trovati {len(queued_docs)} documento(i) QUEUED, avvio processing...")
                    # Processa ogni documento QUEUED in un thread separato (non bloccare il loop)
                    for doc_info in queued_docs:
                        # Avvia processing in thread daemon separato
                        thread = threading.Thread(
                            target=process_queued_document,
                            args=(doc_info,),
                            daemon=True
                        )
                        thread.start()
                        logger.debug(f"📋 [WORKER] [QUEUED_LOOP] Thread processing avviato per: {doc_info.get('file_name', 'N/A')}")
                else:
                    logger.debug("📋 [WORKER] [QUEUED_LOOP] Nessun documento QUEUED trovato")
        except Exception as e:
            logger.error(f"❌ [WORKER] [QUEUED_LOOP] Errore nel processing QUEUED: {e}", exc_info=True)
    
    logger.info("✅ [WORKER] [QUEUED_LOOP] Loop processing QUEUED terminato")


def init_background_tasks():
    """
    Inizializza task in background (migrazione, layout models, controllo STUCK, cleanup coda).
    """
    logger.info("🚀 [WORKER] [BACKGROUND_TASKS] Avvio task iniziali in background...")
    
    try:
        # Migra documenti READY (deprecato) a READY_FOR_REVIEW per backward compatibility
        logger.info("🔄 [WORKER] [BACKGROUND_TASKS] Avvio migrazione stati...")
        from app.processed_documents import migrate_ready_to_ready_for_review
        migrated_count = migrate_ready_to_ready_for_review()
        if migrated_count > 0:
            logger.info(f"✅ [WORKER] [BACKGROUND_TASKS] Migrazione stati completata: {migrated_count} documento(i) migrato(i)")
        else:
            logger.info("✅ [WORKER] [BACKGROUND_TASKS] Migrazione stati: nessun documento da migrare")
    except Exception as e:
        logger.error(f"❌ [WORKER] [BACKGROUND_TASKS] Errore migrazione stati: {e}", exc_info=True)
    
    try:
        # Layout models - LAZY LOADING (non caricare all'avvio, solo quando necessario)
        logger.debug("📐 [WORKER] [BACKGROUND_TASKS] Layout models: lazy loading (caricati on-demand)")
    except Exception as e:
        logger.error(f"❌ [WORKER] [BACKGROUND_TASKS] Errore setup layout models: {e}", exc_info=True)
    
    try:
        # Esegui un controllo iniziale all'avvio (in background)
        logger.info("🔍 [WORKER] [BACKGROUND_TASKS] Avvio controllo iniziale STUCK...")
        from app.processed_documents import check_and_mark_stuck_documents
        initial_stuck = check_and_mark_stuck_documents()
        if initial_stuck > 0:
            logger.info(f"✅ [WORKER] [BACKGROUND_TASKS] Controllo iniziale STUCK: {initial_stuck} documento(i) già bloccato(i)")
        else:
            logger.info("✅ [WORKER] [BACKGROUND_TASKS] Controllo iniziale STUCK: nessun documento bloccato")
    except Exception as e:
        logger.error(f"❌ [WORKER] [BACKGROUND_TASKS] Errore controllo iniziale STUCK: {e}", exc_info=True)
    
    try:
        # Watchdog queue - LAZY LOADING (non caricare all'avvio, solo quando necessario)
        logger.debug("📋 [WORKER] [BACKGROUND_TASKS] Watchdog queue: lazy loading (caricata on-demand)")
    except Exception as e:
        logger.error(f"❌ [WORKER] [BACKGROUND_TASKS] Errore setup watchdog queue: {e}", exc_info=True)
    
    logger.info("✅ [WORKER] [BACKGROUND_TASKS] Tutti i task iniziali completati")


def signal_handler(signum, frame):
    """
    Gestisce SIGTERM/SIGINT per shutdown graceful.
    """
    logger.critical("⛔ [WORKER] [SIGNAL] Segnale di shutdown ricevuto, avvio shutdown graceful...")
    _shutdown_event.set()


def main():
    """
    Main loop del worker process.
    """
    logger.info("🚀 [WORKER] Avvio worker process...")
    
    # Verifica ruolo
    if not IS_WORKER_ROLE:
        logger.error("❌ [WORKER] DDT_ROLE non è 'worker'. Impostare DDT_ROLE=worker prima di avviare worker.py")
        sys.exit(1)
    
    # Registra signal handler per shutdown graceful
    signal.signal(signal.SIGTERM, signal_handler)
    signal.signal(signal.SIGINT, signal_handler)
    
    # Assicurati che la cartella inbox esista
    inbox_path = get_inbox_dir()
    logger.info(f"📁 [WORKER] Cartella inbox verificata: {inbox_path}")
    
    # Inizializza task in background (migrazione, layout models, controllo STUCK, cleanup coda)
    init_background_tasks()
    
    # Avvia watchdog filesystem
    logger.info("👀 [WORKER] Configurazione watchdog filesystem...")
    global _global_observer
    observer = Observer()
    _global_observer = observer
    
    try:
        handler = DDTHandler()
        observer.schedule(handler, inbox_path, recursive=False)
        # REGOLA FERREA: daemon=True per permettere shutdown veloce
        watcher_thread = threading.Thread(target=start_watcher_background, args=(observer,), daemon=True)
        watcher_thread.start()
        logger.info(f"✅ [WORKER] Watchdog configurato per monitorare: {inbox_path}")
    except Exception as e:
        logger.error(f"❌ [WORKER] Errore nella configurazione del watchdog: {e}", exc_info=True)
        _global_observer = None
    
    # Avvia cleanup periodico per documenti STUCK
    global _cleanup_thread, _cleanup_shutdown_flag
    _cleanup_shutdown_flag.clear()  # Reset flag all'avvio
    
    logger.info("🔍 [WORKER] Avvio cleanup thread STUCK...")
    _cleanup_thread = threading.Thread(target=stuck_cleanup_loop, daemon=True)
    _cleanup_thread.start()
    logger.info("✅ [WORKER] Cleanup periodico STUCK avviato (controllo ogni 5 minuti, thread daemon)")
    
    # Avvia loop periodico per processare documenti QUEUED
    global _queued_processing_thread, _queued_processing_shutdown_flag
    _queued_processing_shutdown_flag.clear()  # Reset flag all'avvio
    
    logger.info("📋 [WORKER] Avvio queued processing thread...")
    _queued_processing_thread = threading.Thread(target=queued_processing_loop, daemon=True)
    _queued_processing_thread.start()
    logger.info("✅ [WORKER] Loop processing QUEUED avviato (controllo ogni 10 secondi, thread daemon)")
    
    logger.info("✅ [WORKER] Worker process avviato correttamente")
    
    # Main loop: attende shutdown signal
    try:
        logger.info("⏳ [WORKER] Worker in esecuzione, in attesa di segnale di shutdown...")
        _shutdown_event.wait()  # Attende indefinitamente fino a shutdown
        logger.info("⛔ [WORKER] Shutdown richiesto, avvio cleanup...")
    except KeyboardInterrupt:
        logger.info("⛔ [WORKER] Interruzione da tastiera, avvio cleanup...")
    finally:
        # Shutdown graceful
        logger.critical("⛔ [WORKER] [SHUTDOWN] Shutdown richiesto, arresto thread/observer...")
        
        # Ferma queued processing thread PRIMA (più importante)
        try:
            logger.info("📋 [WORKER] [SHUTDOWN] Fermata queued processing thread...")
            stop_queued_processing_thread_safely()
            logger.info("✅ [WORKER] [SHUTDOWN] Queued processing thread fermato")
        except Exception as e:
            logger.error(f"❌ [WORKER] [SHUTDOWN] Errore durante shutdown queued processing thread: {e}", exc_info=True)
        
        # Ferma cleanup thread PRIMA del watchdog (ordine inverso rispetto startup)
        try:
            logger.info("🧹 [WORKER] [SHUTDOWN] Fermata cleanup thread...")
            stop_cleanup_thread_safely()
            logger.info("✅ [WORKER] [SHUTDOWN] Cleanup thread fermato")
        except Exception as e:
            logger.error(f"❌ [WORKER] [SHUTDOWN] Errore durante shutdown cleanup thread: {e}", exc_info=True)
        
        # Ferma watchdog observer
        try:
            logger.info("🛑 [WORKER] [SHUTDOWN] Fermata watchdog observer...")
            stop_watchdog_safely()
            logger.info("✅ [WORKER] [SHUTDOWN] Watchdog observer fermato")
        except Exception as e:
            logger.error(f"❌ [WORKER] [SHUTDOWN] Errore durante shutdown watchdog: {e}", exc_info=True)
        
        logger.critical("✅ [WORKER] [SHUTDOWN] Shutdown completato (tutti i thread/task fermati)")


if __name__ == "__main__":
    main()
