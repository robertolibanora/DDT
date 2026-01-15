"""
Sistema di tracking persistente per documenti processati
Garantisce idempotenza e previene loop di processing
"""
import json
import logging
import hashlib
import os
import threading
from pathlib import Path
from typing import Dict, Any, Optional
from datetime import datetime, timedelta
from enum import Enum

logger = logging.getLogger(__name__)

# Lock per operazioni thread-safe
_documents_lock = threading.Lock()

# Stati possibili per un documento
class DocumentStatus(str, Enum):
    NEW = "NEW"
    QUEUED = "QUEUED"  # Documento caricato manualmente, in attesa di processing da parte del worker
    PROCESSING = "PROCESSING"  # Stato tecnico: elaborazione in corso (invisibile all'utente)
    STUCK = "STUCK"  # Stato intermedio: PROCESSING bloccato oltre il timeout (richiede attenzione manuale)
    READY = "READY"  # DEPRECATO: mantenuto per backward compatibility, migrato automaticamente a READY_FOR_REVIEW
    READY_FOR_REVIEW = "READY_FOR_REVIEW"  # Stato funzionale: documento pronto per revisione utente
    FINALIZED = "FINALIZED"
    ERROR_FINAL = "ERROR_FINAL"


from app.paths import get_processed_documents_file
PROCESSED_DOCUMENTS_FILE = get_processed_documents_file()

# Struttura dati:
# {
#   "documents": {
#     "hash_sha256": {
#       "hash": "hash_sha256",
#       "file_path": "/path/to/file.pdf",
#       "file_name": "file.pdf",
#       "status": "FINALIZED",
#       "first_seen": "2024-01-01T12:00:00",
#       "last_updated": "2024-01-01T12:00:00",
#       "queue_id": "optional_queue_id",
#       "error_message": "optional_error",
#       "data_inserimento": "14-01-2026"  # Data scelta dall'utente (gg-mm-yyyy)
#     }
#   }
# }


def _load_documents() -> Dict[str, Any]:
    """Carica i documenti processati da file"""
    if not PROCESSED_DOCUMENTS_FILE.exists():
        return {"documents": {}}
    
    try:
        with open(PROCESSED_DOCUMENTS_FILE, 'r', encoding='utf-8') as f:
            data = json.load(f)
            # Assicura che la struttura sia corretta
            if "documents" not in data:
                data = {"documents": {}}
            return data
    except json.JSONDecodeError as e:
        logger.warning(f"Errore parsing processed_documents.json: {e}, ricreo file")
        return {"documents": {}}
    except Exception as e:
        logger.error(f"Errore caricamento processed_documents: {e}", exc_info=True)
        return {"documents": {}}


def _save_documents(data: Dict[str, Any]) -> None:
    """Salva i documenti processati su file"""
    try:
        from app.paths import ensure_dir, safe_open
        ensure_dir(PROCESSED_DOCUMENTS_FILE.parent)
        with safe_open(PROCESSED_DOCUMENTS_FILE, 'w', encoding='utf-8') as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
    except Exception as e:
        logger.error(f"Errore salvataggio processed_documents: {e}", exc_info=True)
        raise


def calculate_file_hash(file_path: str) -> str:
    """
    Calcola l'hash SHA256 del contenuto del file PDF
    
    Args:
        file_path: Percorso del file PDF
        
    Returns:
        Hash SHA256 in formato esadecimale
    """
    try:
        from app.paths import safe_open
        # file_path può essere già assoluto o relativo
        file_path_obj = Path(file_path)
        if not file_path_obj.is_absolute():
            from app.paths import get_base_dir
            file_path_obj = get_base_dir() / file_path_obj
        file_path_obj = file_path_obj.resolve()
        
        with safe_open(file_path_obj, 'rb') as f:
            file_bytes = f.read()
            file_hash = hashlib.sha256(file_bytes).hexdigest()
        return file_hash
    except Exception as e:
        logger.warning(f"Errore calcolo hash SHA256 file {file_path}: {e}")
        # Fallback: usa il nome del file (non ideale ma meglio di niente)
        return hashlib.sha256(str(file_path).encode()).hexdigest()


# ============================================================================
# FUNZIONE CENTRALIZZATA DI TRANSIZIONE STATO - PRODUCTION GRADE
# ============================================================================

# ============================================================================
# MATRICE DI TRANSIZIONI VALIDE - REGOLE FERREE
# ============================================================================
# Ogni transizione deve essere esplicitamente consentita qui.
# Transizioni non presenti sono VIETATE e genereranno ValueError.
#
# REGOLE:
# - FINALIZED e ERROR_FINAL sono terminali: nessuna transizione permessa
# - READY_FOR_REVIEW → PROCESSING è VIETATO (no loop)
# - STUCK può essere riprocessato solo manualmente (STUCK → PROCESSING)
# ============================================================================
_VALID_TRANSITIONS = {
    None: [DocumentStatus.NEW, DocumentStatus.QUEUED, DocumentStatus.PROCESSING, DocumentStatus.READY_FOR_REVIEW, 
           DocumentStatus.FINALIZED, DocumentStatus.ERROR_FINAL],  # Creazione nuovo documento
    DocumentStatus.NEW: [DocumentStatus.PROCESSING, DocumentStatus.ERROR_FINAL],
    DocumentStatus.QUEUED: [DocumentStatus.PROCESSING, DocumentStatus.ERROR_FINAL],  # Worker preleva QUEUED e passa a PROCESSING
    DocumentStatus.PROCESSING: [
        DocumentStatus.READY_FOR_REVIEW,  # Successo: dati estratti, pronto per revisione
        DocumentStatus.STUCK,                # Timeout: bloccato oltre soglia
        DocumentStatus.ERROR_FINAL,        # Errore strutturale irreversibile
        DocumentStatus.FINALIZED           # Finalizzazione diretta (raro, ma possibile)
    ],
    DocumentStatus.STUCK: [
        DocumentStatus.PROCESSING,  # Riprocessamento manuale (azione utente)
        DocumentStatus.ERROR_FINAL  # Conversione manuale a errore definitivo (azione utente)
        # NOTA: STUCK → NEW è stato rimosso (non più necessario)
    ],
    DocumentStatus.READY_FOR_REVIEW: [
        DocumentStatus.FINALIZED,   # Conferma utente
        DocumentStatus.ERROR_FINAL  # Errore durante revisione
        # NOTA: READY_FOR_REVIEW → PROCESSING è VIETATO (no loop)
    ],
    DocumentStatus.READY: [DocumentStatus.READY_FOR_REVIEW],  # Backward compatibility
    # Stati terminali - nessuna transizione permessa
    DocumentStatus.FINALIZED: [],      # Stato terminale: documento completato
    DocumentStatus.ERROR_FINAL: [],   # Stato terminale: errore definitivo
}


def transition_document_state(
    doc_hash: str,
    from_state: DocumentStatus | None,
    to_state: DocumentStatus,
    reason: str,
    metadata: dict | None = None
) -> None:
    """
    Funzione UNICA e OBBLIGATORIA per tutte le transizioni di stato dei documenti.
    Garantisce coerenza, validazione e tracciabilità completa.
    
    REGOLE FERREE:
    - STUCK: documento bloccato oltre timeout, richiede azione manuale
      - Può essere convertito a ERROR_FINAL se errore irreversibile
      - Può essere resettato a NEW/PROCESSING per riprocessamento
    - ERROR_FINAL: errore definitivo, documento non processabile
      - Stato terminale, nessuna transizione permessa
      - Usato per errori strutturali del documento (PDF corrotto, formato non valido, ecc.)
    - FINALIZED: documento completato con successo
      - Stato terminale, nessuna transizione permessa
    
    Args:
        doc_hash: Hash SHA256 del documento
        from_state: Stato di partenza (None se documento non esiste ancora)
        to_state: Stato di destinazione
        reason: Motivo della transizione (obbligatorio per audit)
        metadata: Dizionario opzionale con metadati aggiuntivi:
            - queue_id: ID della coda watchdog
            - error_message: Messaggio di errore (per ERROR_FINAL)
            - data_inserimento: Data inserimento (per FINALIZED)
            - stuck_reason: Motivo specifico per STUCK
            - file_path: Percorso del file
            - file_name: Nome del file
    
    Raises:
        ValueError: Se la transizione non è valida o se mancano parametri obbligatori
        RuntimeError: Se lo stato attuale del documento non corrisponde a from_state
    """
    if not doc_hash:
        raise ValueError("doc_hash non può essere vuoto")
    
    if not reason or not reason.strip():
        raise ValueError("reason è obbligatorio per audit trail")
    
    # Normalizza from_state per la validazione
    if from_state is not None and not isinstance(from_state, DocumentStatus):
        if isinstance(from_state, str):
            try:
                from_state = DocumentStatus(from_state)
            except ValueError:
                raise ValueError(f"from_state non valido: {from_state}")
        else:
            raise ValueError(f"from_state deve essere DocumentStatus o None, trovato: {type(from_state)}")
    
    # Normalizza to_state
    if not isinstance(to_state, DocumentStatus):
        if isinstance(to_state, str):
            try:
                to_state = DocumentStatus(to_state)
            except ValueError:
                raise ValueError(f"to_state non valido: {to_state}")
        else:
            raise ValueError(f"to_state deve essere DocumentStatus, trovato: {type(to_state)}")
    
    # Valida transizione
    allowed_states = _VALID_TRANSITIONS.get(from_state, [])
    if to_state not in allowed_states:
        from_str = from_state.value if from_state else "None (nuovo documento)"
        raise ValueError(
            f"Transizione NON VALIDA: {from_str} → {to_state.value}. "
            f"Transizioni permesse da {from_str}: {[s.value for s in allowed_states]}"
        )
    
    # Verifica che gli stati terminali non possano essere modificati
    if from_state in (DocumentStatus.FINALIZED, DocumentStatus.ERROR_FINAL):
        raise ValueError(
            f"Impossibile modificare stato terminale {from_state.value}. "
            f"Documento già finalizzato o in errore definitivo."
        )
    
    # Valida metadati obbligatori per alcuni stati
    if to_state == DocumentStatus.ERROR_FINAL:
        if not metadata or not metadata.get("error_message"):
            raise ValueError("error_message è obbligatorio per ERROR_FINAL")
    
    with _documents_lock:
        data = _load_documents()
        documents = data.setdefault("documents", {})
        
        # Verifica stato attuale se documento esiste
        if doc_hash in documents:
            current_status_str = documents[doc_hash].get("status", "")
            try:
                current_status = DocumentStatus(current_status_str) if current_status_str else None
            except ValueError:
                # Stato non riconosciuto, tratta come None
                current_status = None
            
            # Se from_state è specificato, verifica corrispondenza
            if from_state is not None:
                if current_status != from_state:
                    raise RuntimeError(
                        f"Stato documento non corrispondente: atteso {from_state.value}, "
                        f"trovato {current_status.value if current_status else 'None'}. "
                        f"Hash: {doc_hash[:16]}..."
                    )
        else:
            # Documento non esiste
            if from_state is not None:
                raise RuntimeError(
                    f"Documento non trovato ma from_state specificato: {from_state.value}. "
                    f"Hash: {doc_hash[:16]}..."
                )
            # Se documento non esiste e to_state richiede file_path, verificalo
            if metadata and metadata.get("file_path"):
                file_path = metadata["file_path"]
                file_name = metadata.get("file_name") or Path(file_path).name
            else:
                file_path = ""
                file_name = ""
        
        # Prepara metadati documento
        now = datetime.now().isoformat()
        
        if doc_hash in documents:
            doc = documents[doc_hash]
            old_status = doc.get("status", "")
        else:
            doc = {
                "hash": doc_hash,
                "first_seen": now,
            }
            old_status = None
        
        # Aggiorna stato e metadati
        doc["status"] = to_state.value
        doc["last_updated"] = now
        
        # REGOLA FERREA: PROCESSING deve avere started_at
        if to_state == DocumentStatus.PROCESSING:
            # Se non esiste già started_at, imposta now
            if "started_at" not in doc or not doc.get("started_at"):
                doc["started_at"] = now
                logger.debug(f"📌 PROCESSING started_at impostato: {doc_hash[:16]}... at {now}")
        
        # Aggiorna metadati specifici per stato
        if metadata:
            if "queue_id" in metadata:
                doc["queue_id"] = metadata["queue_id"]
            
            if "file_path" in metadata and metadata["file_path"]:
                doc["file_path"] = metadata["file_path"]
                doc["file_name"] = metadata.get("file_name") or Path(metadata["file_path"]).name
            
            if "data_inserimento" in metadata:
                doc["data_inserimento"] = metadata["data_inserimento"]
            
            # Salva extraction_mode nei metadata del documento (persistente)
            if "extraction_mode" in metadata:
                doc["extraction_mode"] = metadata["extraction_mode"]
            
            if to_state == DocumentStatus.ERROR_FINAL:
                doc["error_message"] = metadata.get("error_message", reason)
            
            if to_state == DocumentStatus.STUCK:
                doc["stuck_since"] = now
                doc["stuck_reason"] = metadata.get("stuck_reason", reason)
        
        # Pulisci metadati non più rilevanti
        if to_state != DocumentStatus.STUCK:
            doc.pop("stuck_since", None)
            doc.pop("stuck_reason", None)
        
        if to_state != DocumentStatus.ERROR_FINAL:
            doc.pop("error_message", None)
        
        # Pulisci started_at quando esce da PROCESSING (non più necessario)
        if to_state != DocumentStatus.PROCESSING:
            doc.pop("started_at", None)
        
        # Salva
        documents[doc_hash] = doc
        _save_documents(data)
        
        # Log strutturato per audit trail completo
        old_str = old_status if old_status else "None (nuovo)"
        extraction_mode_log = ""
        if metadata and metadata.get("extraction_mode"):
            extraction_mode_log = f" | extraction_mode={metadata['extraction_mode']}"
        
        logger.info(
            f"🔄 TRANSIZIONE_STATO | "
            f"doc_hash={doc_hash[:16]}... | "
            f"from_state={old_str} | "
            f"to_state={to_state.value} | "
            f"reason={reason} | "
            f"timestamp={now}{extraction_mode_log}"
        )
        
        # Log warning per transizioni critiche
        if to_state == DocumentStatus.STUCK:
            logger.warning(
                f"⚠️ DOCUMENTO_STUCK | "
                f"doc_hash={doc_hash[:16]}... | "
                f"file_name={doc.get('file_name', 'N/A')} | "
                f"reason={reason}"
            )
        elif to_state == DocumentStatus.ERROR_FINAL:
            logger.error(
                f"❌ DOCUMENTO_ERROR_FINAL | "
                f"doc_hash={doc_hash[:16]}... | "
                f"file_name={doc.get('file_name', 'N/A')} | "
                f"error_message={metadata.get('error_message', reason) if metadata else reason}"
            )


def is_document_finalized(doc_hash: str) -> bool:
    """
    Verifica se un documento è già stato finalizzato (FINALIZED o ERROR_FINAL)
    
    Args:
        doc_hash: Hash SHA256 del documento
        
    Returns:
        True se il documento è finalizzato, False altrimenti
    """
    with _documents_lock:
        data = _load_documents()
        doc = data.get("documents", {}).get(doc_hash)
        if not doc:
            return False
        
        status = doc.get("status", "")
        return status in (DocumentStatus.FINALIZED.value, DocumentStatus.ERROR_FINAL.value)


def mark_document_ready(doc_hash: str, queue_id: Optional[str] = None, extraction_mode: Optional[str] = None) -> None:
    """
    Marca un documento come READY_FOR_REVIEW (pronto per revisione utente)
    Viene chiamato quando:
    - Dati sono stati estratti con successo
    - PNG anteprima è stata generata
    - Documento è stato aggiunto alla coda watchdog
    
    Args:
        doc_hash: Hash SHA256 del documento
        queue_id: ID opzionale della coda watchdog
        extraction_mode: Modalità di estrazione usata (LAYOUT_MODEL, HYBRID_LAYOUT_AI, AI_FALLBACK)
    """
    # Ottieni stato corrente
    current_status = get_document_status(doc_hash)
    current_state = None
    if current_status:
        try:
            current_state = DocumentStatus(current_status)
        except ValueError:
            current_state = None
    
    # Se già finalizzato, non fare nulla
    if current_state in (DocumentStatus.FINALIZED, DocumentStatus.ERROR_FINAL):
        logger.debug(f"Documento già finalizzato, ignoro: hash={doc_hash[:16]}...")
        return
    
    # Prepara metadata con extraction_mode se disponibile
    metadata = {}
    if queue_id:
        metadata["queue_id"] = queue_id
    if extraction_mode:
        metadata["extraction_mode"] = extraction_mode
    
    # Transizione usando funzione centralizzata
    try:
        transition_document_state(
            doc_hash=doc_hash,
            from_state=current_state,
            to_state=DocumentStatus.READY_FOR_REVIEW,
            reason=f"Documento pronto per revisione utente (dati estratti + PNG + coda, extraction_mode={extraction_mode or 'N/A'})",
            metadata=metadata if metadata else None
        )
    except RuntimeError as e:
        # Se lo stato non corrisponde, prova senza from_state (per compatibilità)
        logger.warning(f"Stato non corrispondente, provo senza validazione: {e}")
        transition_document_state(
            doc_hash=doc_hash,
            from_state=None,
            to_state=DocumentStatus.READY_FOR_REVIEW,
            reason=f"Documento pronto per revisione utente (compatibilità, extraction_mode={extraction_mode or 'N/A'})",
            metadata=metadata if metadata else None
        )


def is_document_processing(doc_hash: str) -> bool:
    """
    Verifica se un documento è attualmente in processing
    
    Args:
        doc_hash: Hash SHA256 del documento
        
    Returns:
        True se il documento è in processing, False altrimenti
    """
    with _documents_lock:
        data = _load_documents()
        doc = data.get("documents", {}).get(doc_hash)
        if not doc:
            return False
        
        return doc.get("status", "") == DocumentStatus.PROCESSING.value


def register_document(file_path: str, doc_hash: str, status: DocumentStatus = DocumentStatus.NEW, 
                     queue_id: Optional[str] = None, data_inserimento: Optional[str] = None) -> None:
    """
    Registra o aggiorna un documento nel sistema di tracking
    DEPRECATO: Usa transition_document_state() direttamente per nuove implementazioni.
    Mantenuto per backward compatibility.
    
    Args:
        file_path: Percorso del file PDF
        doc_hash: Hash SHA256 del documento
        status: Stato del documento
        queue_id: ID opzionale della coda watchdog
        data_inserimento: Data di inserimento scelta dall'utente (gg-mm-yyyy)
    """
    # Ottieni stato corrente
    current_status = get_document_status(doc_hash)
    current_state = None
    if current_status:
        try:
            current_state = DocumentStatus(current_status)
        except ValueError:
            current_state = None
    
    # Se documento esiste e stato è terminale, non modificare
    if current_state in (DocumentStatus.FINALIZED, DocumentStatus.ERROR_FINAL):
        logger.debug(f"Documento già finalizzato, ignoro registrazione: hash={doc_hash[:16]}...")
        return
    
    # Usa funzione centralizzata per transizione
    metadata = {}
    if queue_id:
        metadata["queue_id"] = queue_id
    if file_path:
        metadata["file_path"] = file_path
        metadata["file_name"] = Path(file_path).name
    if data_inserimento and status != DocumentStatus.FINALIZED:
        metadata["data_inserimento"] = data_inserimento
    
    try:
        transition_document_state(
            doc_hash=doc_hash,
            from_state=current_state,
            to_state=status,
            reason=f"Registrazione documento (compatibilità)",
            metadata=metadata if metadata else None
        )
    except RuntimeError:
        # Se stato non corrisponde, crea nuovo documento
        transition_document_state(
            doc_hash=doc_hash,
            from_state=None,
            to_state=status,
            reason=f"Creazione nuovo documento",
            metadata=metadata if metadata else None
        )


def mark_document_finalized(doc_hash: str, queue_id: Optional[str] = None, data_inserimento: Optional[str] = None) -> None:
    """
    Marca un documento come finalizzato
    
    Args:
        doc_hash: Hash SHA256 del documento
        queue_id: ID opzionale della coda watchdog
        data_inserimento: Data di inserimento scelta dall'utente (gg-mm-yyyy)
    """
    # Ottieni stato corrente
    current_status = get_document_status(doc_hash)
    current_state = None
    if current_status:
        try:
            current_state = DocumentStatus(current_status)
        except ValueError:
            current_state = None
    
    # Se già finalizzato, non fare nulla
    if current_state == DocumentStatus.FINALIZED:
        logger.debug(f"Documento già FINALIZED: hash={doc_hash[:16]}...")
        return
    
    # Prepara metadati
    metadata = {}
    if queue_id:
        metadata["queue_id"] = queue_id
    if data_inserimento:
        metadata["data_inserimento"] = data_inserimento
    
    # Transizione usando funzione centralizzata
    transition_document_state(
        doc_hash=doc_hash,
        from_state=current_state,
        to_state=DocumentStatus.FINALIZED,
        reason="Documento finalizzato dall'utente",
        metadata=metadata if metadata else None
    )


def mark_document_error(doc_hash: str, error_message: str, queue_id: Optional[str] = None) -> None:
    """
    Marca un documento come errore finale (non riprocessabile)
    
    REGOLA FERREA: ERROR_FINAL è per errori strutturali irreversibili:
    - PDF corrotto o non valido
    - Formato non supportato
    - Errori di parsing che impediscono l'estrazione
    
    NON usare per errori temporanei o recuperabili (usa STUCK invece).
    
    Args:
        doc_hash: Hash SHA256 del documento
        error_message: Messaggio di errore (obbligatorio)
        queue_id: ID opzionale della coda watchdog
    """
    if not error_message or not error_message.strip():
        raise ValueError("error_message è obbligatorio per ERROR_FINAL")
    
    # Ottieni stato corrente
    current_status = get_document_status(doc_hash)
    current_state = None
    if current_status:
        try:
            current_state = DocumentStatus(current_status)
        except ValueError:
            current_state = None
    
    # Se già in ERROR_FINAL, aggiorna solo il messaggio
    if current_state == DocumentStatus.ERROR_FINAL:
        with _documents_lock:
            data = _load_documents()
            documents = data.setdefault("documents", {})
            if doc_hash in documents:
                documents[doc_hash]["error_message"] = error_message
                documents[doc_hash]["last_updated"] = datetime.now().isoformat()
                _save_documents(data)
        logger.debug(f"Messaggio errore aggiornato per documento ERROR_FINAL: hash={doc_hash[:16]}...")
        return
    
    # Prepara metadati
    metadata = {
        "error_message": error_message
    }
    if queue_id:
        metadata["queue_id"] = queue_id
    
    # Transizione usando funzione centralizzata
    transition_document_state(
        doc_hash=doc_hash,
        from_state=current_state,
        to_state=DocumentStatus.ERROR_FINAL,
        reason=f"Errore finale: {error_message}",
        metadata=metadata
    )


def get_document_status(doc_hash: str) -> Optional[str]:
    """
    Ottiene lo stato di un documento
    
    Args:
        doc_hash: Hash SHA256 del documento
        
    Returns:
        Stato del documento o None se non trovato
    """
    with _documents_lock:
        data = _load_documents()
        doc = data.get("documents", {}).get(doc_hash)
        return doc.get("status") if doc else None


def get_document_metadata(doc_hash: str) -> Optional[Dict[str, Any]]:
    """
    Ottiene i metadati di un documento (extraction_mode, queue_id, ecc.)
    
    Args:
        doc_hash: Hash SHA256 del documento
        
    Returns:
        Dizionario con i metadati del documento o None se non trovato
    """
    with _documents_lock:
        data = _load_documents()
        doc = data.get("documents", {}).get(doc_hash)
        if not doc:
            return None
        
        # Estrai solo i metadati rilevanti (non lo stato che ha una funzione dedicata)
        metadata = {}
        if "extraction_mode" in doc:
            metadata["extraction_mode"] = doc["extraction_mode"]
        if "queue_id" in doc:
            metadata["queue_id"] = doc["queue_id"]
        if "file_path" in doc:
            metadata["file_path"] = doc["file_path"]
        if "file_name" in doc:
            metadata["file_name"] = doc["file_name"]
        
        return metadata if metadata else None


def should_process_document(doc_hash: str) -> tuple[bool, str]:
    """
    Determina se un documento dovrebbe essere processato
    
    Args:
        doc_hash: Hash SHA256 del documento
        
    Returns:
        Tupla (should_process: bool, reason: str)
    """
    with _documents_lock:
        data = _load_documents()
        doc = data.get("documents", {}).get(doc_hash)
        
        if not doc:
            return True, "new_document"
        
        status = doc.get("status", "")
        
        if status == DocumentStatus.FINALIZED.value:
            return False, "already_finalized"
        
        if status == DocumentStatus.ERROR_FINAL.value:
            return False, "error_final"
        
        if status == DocumentStatus.PROCESSING.value:
            return False, "already_processing"
        
        if status == DocumentStatus.STUCK.value:
            # STUCK non viene riprocessato automaticamente - richiede azione manuale
            return False, "stuck_requires_manual_action"
        
        if status == DocumentStatus.READY_FOR_REVIEW.value:
            # READY_FOR_REVIEW significa già processato e pronto per anteprima
            return False, "already_ready_for_review"
        
        # Backward compatibility: READY viene trattato come READY_FOR_REVIEW
        if status == DocumentStatus.READY.value:
            return False, "already_ready"
        
        # QUEUED può essere processato dal worker
        if status == DocumentStatus.QUEUED.value:
            return True, "queued_ready_for_processing"
        
        # NEW o altri stati possono essere riprocessati
        return True, "reprocess_allowed"


def get_data_inserimento(doc_hash: str) -> Optional[str]:
    """
    Ottiene la data di inserimento di un documento
    
    Args:
        doc_hash: Hash SHA256 del documento
        
    Returns:
        Data di inserimento (gg-mm-yyyy) o None se non presente
    """
    with _documents_lock:
        data = _load_documents()
        doc = data.get("documents", {}).get(doc_hash)
        return doc.get("data_inserimento") if doc else None


def update_data_inserimento(doc_hash: str, data_inserimento: str) -> bool:
    """
    Aggiorna la data di inserimento di un documento (solo se non è FINALIZED)
    
    Args:
        doc_hash: Hash SHA256 del documento
        data_inserimento: Data di inserimento (gg-mm-yyyy)
        
    Returns:
        True se aggiornato, False se il documento è già FINALIZED
    """
    with _documents_lock:
        data = _load_documents()
        documents = data.setdefault("documents", {})
        
        if doc_hash not in documents:
            # Crea nuovo documento con data_inserimento
            register_document("", doc_hash, DocumentStatus.NEW, None, data_inserimento)
            return True
        
        doc = documents[doc_hash]
        
        # Non permettere modifica se già FINALIZED
        if doc.get("status") == DocumentStatus.FINALIZED.value:
            logger.warning(f"⚠️ Tentativo di modificare data_inserimento per documento FINALIZED: {doc_hash[:16]}...")
            return False
        
        doc["data_inserimento"] = data_inserimento
        doc["last_updated"] = datetime.now().isoformat()
        _save_documents(data)
        logger.info(f"📅 Data inserimento aggiornata: hash={doc_hash[:16]}... data={data_inserimento}")
        return True


# Configurazione timeout per PROCESSING -> STUCK (default: 30 minuti)
PROCESSING_TIMEOUT_MINUTES = int(os.getenv("PROCESSING_TIMEOUT_MINUTES", "30"))


def mark_document_stuck(doc_hash: str, reason: Optional[str] = None) -> None:
    """
    Marca un documento come STUCK (PROCESSING bloccato oltre il timeout).
    
    REGOLA FERREA: STUCK è per documenti bloccati oltre timeout, ma POTENZIALMENTE recuperabili.
    - Timeout superato durante processing
    - Processo interrotto o bloccato
    - Richiede azione manuale per riprocessamento
    
    Differenza con ERROR_FINAL:
    - STUCK: problema temporaneo/sistema, può essere riprocessato
    - ERROR_FINAL: problema strutturale del documento, non recuperabile
    
    Args:
        doc_hash: Hash SHA256 del documento
        reason: Motivo per cui è bloccato (default: "processing_timeout")
    """
    if not reason:
        reason = "processing_timeout"
    
    # Ottieni stato corrente
    current_status = get_document_status(doc_hash)
    current_state = None
    if current_status:
        try:
            current_state = DocumentStatus(current_status)
        except ValueError:
            current_state = None
    
    # Solo PROCESSING può diventare STUCK
    if current_state != DocumentStatus.PROCESSING:
        logger.debug(
            f"⚠️ Tentativo di marcare come STUCK documento non PROCESSING: "
            f"hash={doc_hash[:16]}... status={current_state.value if current_state else 'None'}"
        )
        return
    
    # Prepara metadata con informazioni sul timeout
    with _documents_lock:
        data = _load_documents()
        documents = data.get("documents", {})
        doc = documents.get(doc_hash, {})
        started_at = doc.get("first_seen") or doc.get("last_updated", "")
        timeout_minutes = PROCESSING_TIMEOUT_MINUTES
    
    metadata = {
        "stuck_reason": reason,
        "started_at": started_at,
        "timeout_minutes": timeout_minutes
    }
    
    # Transizione usando funzione centralizzata
    transition_document_state(
        doc_hash=doc_hash,
        from_state=DocumentStatus.PROCESSING,
        to_state=DocumentStatus.STUCK,
        reason=reason,
        metadata=metadata
    )


def check_and_mark_stuck_documents(timeout_minutes: Optional[int] = None) -> int:
    """
    Controlla tutti i documenti in PROCESSING e li marca come STUCK se bloccati oltre il timeout.
    
    PROTEZIONE ZOMBIE:
    - PROCESSING > timeout → STUCK (automatico)
    - PROCESSING > 1 ora → WARNING log (soglia critica)
    - Mai riprocessare automaticamente: solo azione umana può sbloccare
    
    Args:
        timeout_minutes: Timeout in minuti (default: PROCESSING_TIMEOUT_MINUTES)
        
    Returns:
        Numero di documenti marcati come STUCK
    """
    if timeout_minutes is None:
        timeout_minutes = PROCESSING_TIMEOUT_MINUTES
    
    # Soglia critica: 1 ora (per warning)
    CRITICAL_THRESHOLD_MINUTES = 60
    
    with _documents_lock:
        data = _load_documents()
        documents = data.get("documents", {})
        
        stuck_count = 0
        cutoff_time = datetime.now() - timedelta(minutes=timeout_minutes)
        critical_cutoff_time = datetime.now() - timedelta(minutes=CRITICAL_THRESHOLD_MINUTES)
        
        for doc_hash, doc in documents.items():
            status = doc.get("status", "")
            
            # Solo documenti in PROCESSING
            if status != DocumentStatus.PROCESSING.value:
                continue
            
            # REGOLA FERREA: Usa started_at se disponibile, altrimenti first_seen o last_updated
            started_at_str = doc.get("started_at") or doc.get("first_seen") or doc.get("last_updated")
            last_updated_str = doc.get("last_updated")
            
            # Valida started_at (obbligatorio per PROCESSING)
            if not started_at_str:
                # Nessun timestamp valido, marca come STUCK
                mark_document_stuck(doc_hash, "started_at mancante (PROCESSING senza timestamp)")
                stuck_count += 1
                continue
            
            try:
                started_at = datetime.fromisoformat(started_at_str)
            except (ValueError, TypeError):
                # Timestamp non valido, marca come STUCK
                mark_document_stuck(doc_hash, f"started_at non valido: {started_at_str}")
                stuck_count += 1
                continue
            
            # Usa started_at per calcolare timeout (non last_updated)
            last_updated = started_at
            
            # Warning per PROCESSING oltre soglia critica (1 ora)
            if started_at < critical_cutoff_time:
                processing_duration_minutes = (datetime.now() - started_at).total_seconds() / 60
                logger.warning(
                    f"⚠️ PROCESSING_CRITICAL | "
                    f"doc_hash={doc_hash[:16]}... | "
                    f"file_name={doc.get('file_name', 'N/A')} | "
                    f"processing_duration_minutes={processing_duration_minutes:.1f} | "
                    f"started_at={started_at_str}"
                )
            
            # Se è bloccato oltre il timeout, marca come STUCK
            if started_at < cutoff_time:
                processing_duration_minutes = (datetime.now() - started_at).total_seconds() / 60
                mark_document_stuck(
                    doc_hash, 
                    f"Timeout {timeout_minutes} minuti superato (processing durato {processing_duration_minutes:.1f} minuti, started_at={started_at_str})"
                )
                stuck_count += 1
        
        return stuck_count


def get_queued_documents() -> list[Dict[str, Any]]:
    """
    Ottiene tutti i documenti in stato QUEUED (caricati manualmente, in attesa di processing)
    
    Returns:
        Lista di documenti QUEUED con informazioni complete
    """
    with _documents_lock:
        data = _load_documents()
        documents = data.get("documents", {})
        
        queued_docs = []
        for doc_hash, doc in documents.items():
            if doc.get("status") == DocumentStatus.QUEUED.value:
                queued_docs.append({
                    "hash": doc_hash,
                    "file_name": doc.get("file_name", "N/A"),
                    "file_path": doc.get("file_path", ""),
                    "status": DocumentStatus.QUEUED.value,
                    "first_seen": doc.get("first_seen", ""),
                    "last_updated": doc.get("last_updated", ""),
                    "queue_id": doc.get("queue_id"),
                    "error_message": doc.get("error_message")
                })
        
        # Ordina per first_seen (più vecchi prima - FIFO)
        queued_docs.sort(key=lambda x: x.get("first_seen", ""), reverse=False)
        return queued_docs


def get_stuck_documents() -> list[Dict[str, Any]]:
    """
    Ottiene tutti i documenti in stato STUCK
    
    Returns:
        Lista di documenti STUCK con informazioni complete
    """
    with _documents_lock:
        data = _load_documents()
        documents = data.get("documents", {})
        
        stuck_docs = []
        for doc_hash, doc in documents.items():
            if doc.get("status") == DocumentStatus.STUCK.value:
                stuck_docs.append({
                    "hash": doc_hash,
                    "file_name": doc.get("file_name", "N/A"),
                    "file_path": doc.get("file_path", ""),
                    "status": DocumentStatus.STUCK.value,
                    "first_seen": doc.get("first_seen", ""),
                    "last_updated": doc.get("last_updated", ""),
                    "stuck_since": doc.get("stuck_since", doc.get("last_updated", "")),
                    "stuck_reason": doc.get("stuck_reason", "Timeout superato"),
                    "queue_id": doc.get("queue_id"),
                    "error_message": doc.get("error_message")
                })
        
        # Ordina per stuck_since (più vecchi prima)
        stuck_docs.sort(key=lambda x: x.get("stuck_since", ""), reverse=False)
        return stuck_docs


def count_pending_documents() -> int:
    """
    Conta tutti i documenti in stati "in attesa" che richiedono intervento.
    
    Stati considerati "in attesa":
    - QUEUED: documento caricato manualmente, in attesa di processing
    - PROCESSING: documento in elaborazione (può richiedere attenzione se bloccato)
    - READY_FOR_REVIEW: documento pronto per revisione utente
    - STUCK: documento bloccato oltre timeout, richiede azione manuale
    
    Returns:
        Numero totale di documenti in attesa
    """
    with _documents_lock:
        data = _load_documents()
        documents = data.get("documents", {})
        
        pending_count = 0
        pending_states = {
            DocumentStatus.QUEUED.value,
            DocumentStatus.PROCESSING.value,
            DocumentStatus.READY_FOR_REVIEW.value,
            DocumentStatus.STUCK.value
        }
        
        for doc_hash, doc in documents.items():
            status = doc.get("status", "")
            if status in pending_states:
                pending_count += 1
        
        return pending_count


def reset_stuck_to_new(doc_hash: str) -> bool:
    """
    DEPRECATO: Usa transition_document_state() con STUCK → PROCESSING invece.
    Reset manuale di un documento STUCK a NEW per permettere riprocessamento.
    Mantenuto per backward compatibility.
    
    Args:
        doc_hash: Hash SHA256 del documento
        
    Returns:
        True se reset eseguito, False se documento non trovato o non STUCK
    """
    # Ottieni stato corrente
    current_status = get_document_status(doc_hash)
    if not current_status:
        logger.warning(f"⚠️ Tentativo di reset STUCK documento non trovato: hash={doc_hash[:16]}...")
        return False
    
    try:
        current_state = DocumentStatus(current_status)
    except ValueError:
        logger.warning(f"⚠️ Stato documento non valido: {current_status}")
        return False
    
    if current_state != DocumentStatus.STUCK:
        logger.warning(f"⚠️ Tentativo di reset documento non STUCK: hash={doc_hash[:16]}... status={current_state.value}")
        return False
    
    # NOTA: STUCK → NEW non è più nella matrice di transizioni valide
    # Per backward compatibility, convertiamo a STUCK → PROCESSING
    try:
        transition_document_state(
            doc_hash=doc_hash,
            from_state=DocumentStatus.STUCK,
            to_state=DocumentStatus.PROCESSING,
            reason="Reset manuale STUCK → PROCESSING (backward compatibility)",
            metadata=None
        )
        return True
    except (ValueError, RuntimeError) as e:
        logger.error(f"Errore reset STUCK: {e}")
        return False


def convert_stuck_to_error_final(doc_hash: str, error_message: str) -> bool:
    """
    Converte un documento STUCK in ERROR_FINAL quando l'errore è definitivo.
    
    REGOLA FERREA: Usa questa funzione quando:
    - Il documento STUCK ha un errore strutturale irreversibile
    - Dopo tentativi di riprocessamento falliti
    - Quando si determina che il problema non è temporaneo
    
    Args:
        doc_hash: Hash SHA256 del documento
        error_message: Messaggio di errore definitivo
        
    Returns:
        True se conversione eseguita, False se documento non trovato o non STUCK
    """
    if not error_message or not error_message.strip():
        raise ValueError("error_message è obbligatorio per ERROR_FINAL")
    
    # Ottieni stato corrente
    current_status = get_document_status(doc_hash)
    if not current_status:
        logger.warning(f"⚠️ Tentativo di convertire STUCK → ERROR_FINAL documento non trovato: hash={doc_hash[:16]}...")
        return False
    
    try:
        current_state = DocumentStatus(current_status)
    except ValueError:
        logger.warning(f"⚠️ Stato documento non valido: {current_status}")
        return False
    
    if current_state != DocumentStatus.STUCK:
        logger.warning(
            f"⚠️ Tentativo di convertire documento non STUCK: "
            f"hash={doc_hash[:16]}... status={current_state.value}"
        )
        return False
    
    # Transizione usando funzione centralizzata
    try:
        transition_document_state(
            doc_hash=doc_hash,
            from_state=DocumentStatus.STUCK,
            to_state=DocumentStatus.ERROR_FINAL,
            reason=f"Conversione STUCK → ERROR_FINAL: {error_message}",
            metadata={"error_message": error_message}
        )
        return True
    except (ValueError, RuntimeError) as e:
        logger.error(f"Errore conversione STUCK → ERROR_FINAL: {e}")
        return False


def migrate_ready_to_ready_for_review() -> int:
    """
    Migra i documenti con stato READY (deprecato) a READY_FOR_REVIEW
    Funzione di backward compatibility chiamata all'avvio
    
    Returns:
        Numero di documenti migrati
    """
    with _documents_lock:
        data = _load_documents()
        documents = data.get("documents", {})
        
        migrated_count = 0
        for doc_hash, doc in documents.items():
            if doc.get("status") == DocumentStatus.READY.value:
                try:
                    transition_document_state(
                        doc_hash=doc_hash,
                        from_state=DocumentStatus.READY,
                        to_state=DocumentStatus.READY_FOR_REVIEW,
                        reason="Migrazione backward compatibility READY → READY_FOR_REVIEW",
                        metadata=None
                    )
                    migrated_count += 1
                except Exception as e:
                    logger.warning(f"Errore migrazione documento {doc_hash[:16]}...: {e}")
        
        if migrated_count > 0:
            logger.info(f"✅ Migrazione completata: {migrated_count} documento(i) migrato(i) da READY a READY_FOR_REVIEW")
        
        return migrated_count
