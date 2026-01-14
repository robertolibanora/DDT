"""
Sistema di tracking persistente per documenti processati
Garantisce idempotenza e previene loop di processing
"""
import json
import logging
import hashlib
import threading
from pathlib import Path
from typing import Dict, Any, Optional
from datetime import datetime
from enum import Enum

logger = logging.getLogger(__name__)

# Lock per operazioni thread-safe
_documents_lock = threading.Lock()

# Stati possibili per un documento
class DocumentStatus(str, Enum):
    NEW = "NEW"
    PROCESSING = "PROCESSING"
    FINALIZED = "FINALIZED"
    ERROR_FINAL = "ERROR_FINAL"


PROCESSED_DOCUMENTS_FILE = Path("app/processed_documents.json")

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
#       "error_message": "optional_error"
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
        PROCESSED_DOCUMENTS_FILE.parent.mkdir(parents=True, exist_ok=True)
        with open(PROCESSED_DOCUMENTS_FILE, 'w', encoding='utf-8') as f:
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
        with open(file_path, 'rb') as f:
            file_bytes = f.read()
            file_hash = hashlib.sha256(file_bytes).hexdigest()
        return file_hash
    except Exception as e:
        logger.warning(f"Errore calcolo hash SHA256 file {file_path}: {e}")
        # Fallback: usa il nome del file (non ideale ma meglio di niente)
        return hashlib.sha256(file_path.encode()).hexdigest()


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
                     queue_id: Optional[str] = None) -> None:
    """
    Registra o aggiorna un documento nel sistema di tracking
    
    Args:
        file_path: Percorso del file PDF
        doc_hash: Hash SHA256 del documento
        status: Stato del documento
        queue_id: ID opzionale della coda watchdog
    """
    with _documents_lock:
        data = _load_documents()
        documents = data.setdefault("documents", {})
        
        now = datetime.now().isoformat()
        
        if doc_hash in documents:
            # Aggiorna documento esistente
            doc = documents[doc_hash]
            doc["last_updated"] = now
            doc["status"] = status.value
            if queue_id:
                doc["queue_id"] = queue_id
            if file_path:
                doc["file_path"] = file_path
                doc["file_name"] = Path(file_path).name
        else:
            # Crea nuovo documento
            documents[doc_hash] = {
                "hash": doc_hash,
                "file_path": file_path,
                "file_name": Path(file_path).name,
                "status": status.value,
                "first_seen": now,
                "last_updated": now,
                "queue_id": queue_id
            }
        
        _save_documents(data)
        
        logger.info(f"📝 Documento registrato: hash={doc_hash[:16]}... status={status.value} file={Path(file_path).name}")


def mark_document_finalized(doc_hash: str, queue_id: Optional[str] = None) -> None:
    """
    Marca un documento come finalizzato
    
    Args:
        doc_hash: Hash SHA256 del documento
        queue_id: ID opzionale della coda watchdog
    """
    with _documents_lock:
        data = _load_documents()
        documents = data.setdefault("documents", {})
        
        if doc_hash in documents:
            doc = documents[doc_hash]
            doc["status"] = DocumentStatus.FINALIZED.value
            doc["last_updated"] = datetime.now().isoformat()
            if queue_id:
                doc["queue_id"] = queue_id
            _save_documents(data)
            logger.info(f"✅ Documento FINALIZED: hash={doc_hash[:16]}... file={doc.get('file_name', 'N/A')}")
        else:
            # Se non esiste, crealo come FINALIZED
            register_document("", doc_hash, DocumentStatus.FINALIZED, queue_id)


def mark_document_error(doc_hash: str, error_message: str, queue_id: Optional[str] = None) -> None:
    """
    Marca un documento come errore finale (non riprocessabile)
    
    Args:
        doc_hash: Hash SHA256 del documento
        error_message: Messaggio di errore
        queue_id: ID opzionale della coda watchdog
    """
    with _documents_lock:
        data = _load_documents()
        documents = data.setdefault("documents", {})
        
        if doc_hash in documents:
            doc = documents[doc_hash]
            doc["status"] = DocumentStatus.ERROR_FINAL.value
            doc["last_updated"] = datetime.now().isoformat()
            doc["error_message"] = error_message
            if queue_id:
                doc["queue_id"] = queue_id
            _save_documents(data)
            logger.warning(f"❌ Documento ERROR_FINAL: hash={doc_hash[:16]}... error={error_message}")
        else:
            # Se non esiste, crealo come ERROR_FINAL
            register_document("", doc_hash, DocumentStatus.ERROR_FINAL, queue_id)
            with _documents_lock:
                data = _load_documents()
                documents = data.setdefault("documents", {})
                if doc_hash in documents:
                    documents[doc_hash]["error_message"] = error_message
                    _save_documents(data)


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
        
        # NEW o altri stati possono essere riprocessati
        return True, "reprocess_allowed"
