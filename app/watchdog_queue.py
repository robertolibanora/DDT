"""
Sistema di coda per i PDF rilevati dal watchdog
Permette al frontend di mostrare l'anteprima prima di salvare
"""
import json
import logging
import base64
from pathlib import Path
from typing import Dict, Any, Optional, List
from datetime import datetime, timedelta
import threading

logger = logging.getLogger(__name__)

# Lock per operazioni thread-safe
_queue_lock = threading.Lock()

# Coda in memoria (in produzione potresti usare Redis o database)
_watchdog_queue: List[Dict[str, Any]] = []

QUEUE_FILE = Path("app/watchdog_queue.json")

# Configurazione pulizia automatica
MAX_QUEUE_SIZE = 1000  # Massimo numero di elementi in coda
CLEANUP_DAYS = 7  # Rimuovi elementi processati più vecchi di 7 giorni


def _load_queue() -> List[Dict[str, Any]]:
    """Carica la coda da file se esiste"""
    global _watchdog_queue
    
    if QUEUE_FILE.exists():
        try:
            with open(QUEUE_FILE, 'r', encoding='utf-8') as f:
                _watchdog_queue = json.load(f)
            logger.info(f"Caricata coda watchdog con {len(_watchdog_queue)} elementi")
        except Exception as e:
            logger.warning(f"Errore caricamento coda: {e}")
            _watchdog_queue = []
    else:
        _watchdog_queue = []
    
    return _watchdog_queue


def _save_queue():
    """Salva la coda su file"""
    try:
        with open(QUEUE_FILE, 'w', encoding='utf-8') as f:
            json.dump(_watchdog_queue, f, indent=2, ensure_ascii=False)
    except Exception as e:
        logger.warning(f"Errore salvataggio coda: {e}")


def add_to_queue(file_path: str, extracted_data: Dict[str, Any], pdf_base64: str, file_hash: str) -> str:
    """
    Aggiunge un PDF alla coda per l'anteprima
    
    Args:
        file_path: Percorso del file PDF
        extracted_data: Dati estratti dall'AI
        pdf_base64: PDF convertito in base64
        file_hash: Hash del file
        
    Returns:
        ID della voce in coda
    """
    global _watchdog_queue
    
    with _queue_lock:
        queue_id = f"{file_hash}_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
        
        queue_item = {
            "id": queue_id,
            "file_path": file_path,
            "file_name": Path(file_path).name,
            "file_hash": file_hash,
            "extracted_data": extracted_data,
            "pdf_base64": pdf_base64,
            "timestamp": datetime.now().isoformat(),
            "processed": False
        }
        
        _watchdog_queue.append(queue_item)
        _save_queue()
        
        logger.info(f"PDF aggiunto alla coda watchdog: {queue_id}")
        return queue_id


def get_pending_items() -> List[Dict[str, Any]]:
    """
    Ottiene tutti gli elementi in coda non ancora processati
    
    Returns:
        Lista di elementi in coda
    """
    with _queue_lock:
        _load_queue()
        return [item for item in _watchdog_queue if not item.get("processed", False)]


def get_all_items() -> List[Dict[str, Any]]:
    """
    Ottiene tutti gli elementi in coda (sia processati che non)
    
    Returns:
        Lista di tutti gli elementi in coda
    """
    with _queue_lock:
        _load_queue()
        return _watchdog_queue.copy()


def is_file_hash_in_queue(file_hash: str) -> bool:
    """
    Verifica se un file con questo hash è già nella coda (processato o meno)
    
    Args:
        file_hash: Hash del file da verificare
        
    Returns:
        True se il file è già nella coda, False altrimenti
    """
    with _queue_lock:
        _load_queue()
        for item in _watchdog_queue:
            if item.get("file_hash") == file_hash:
                return True
        return False


def mark_as_processed(queue_id: str):
    """
    Marca un elemento come processato
    
    Args:
        queue_id: ID dell'elemento da marcare
    """
    global _watchdog_queue
    
    with _queue_lock:
        _load_queue()
        for item in _watchdog_queue:
            if item.get("id") == queue_id:
                item["processed"] = True
                break
        _save_queue()


def remove_item(queue_id: str):
    """
    Rimuove un elemento dalla coda
    
    Args:
        queue_id: ID dell'elemento da rimuovere
    """
    global _watchdog_queue
    
    with _queue_lock:
        _load_queue()
        _watchdog_queue = [item for item in _watchdog_queue if item.get("id") != queue_id]
        _save_queue()


def get_item_by_id(queue_id: str) -> Optional[Dict[str, Any]]:
    """
    Ottiene un elemento specifico dalla coda
    
    Args:
        queue_id: ID dell'elemento
        
    Returns:
        Elemento della coda o None
    """
    with _queue_lock:
        _load_queue()
        for item in _watchdog_queue:
            if item.get("id") == queue_id:
                return item
        return None


def cleanup_old_items() -> int:
    """
    Rimuove elementi vecchi dalla coda per evitare crescita indefinita
    
    - Rimuove elementi processati più vecchi di CLEANUP_DAYS giorni
    - Se la coda supera MAX_QUEUE_SIZE, rimuove i più vecchi (processati o meno)
    
    Returns:
        Numero di elementi rimossi
    """
    global _watchdog_queue
    
    with _queue_lock:
        _load_queue()
        initial_count = len(_watchdog_queue)
        
        if initial_count == 0:
            return 0
        
        cutoff_date = datetime.now() - timedelta(days=CLEANUP_DAYS)
        
        # Filtra elementi da mantenere
        kept_items = []
        for item in _watchdog_queue:
            timestamp_str = item.get("timestamp", "")
            if not timestamp_str:
                # Se non ha timestamp, mantienilo (vecchio formato)
                kept_items.append(item)
                continue
            
            try:
                item_date = datetime.fromisoformat(timestamp_str)
                
                # Mantieni se:
                # 1. Non è processato, OPPURE
                # 2. È processato ma è più recente di CLEANUP_DAYS giorni
                is_processed = item.get("processed", False)
                if not is_processed or item_date > cutoff_date:
                    kept_items.append(item)
            except (ValueError, TypeError):
                # Se il timestamp non è valido, mantieni l'elemento
                kept_items.append(item)
        
        # Se ancora troppo grande, rimuovi i più vecchi (indipendentemente da processed)
        if len(kept_items) > MAX_QUEUE_SIZE:
            # Ordina per timestamp (più recenti prima)
            kept_items.sort(
                key=lambda x: datetime.fromisoformat(x.get("timestamp", "2000-01-01")) 
                if x.get("timestamp") else datetime.min,
                reverse=True
            )
            # Mantieni solo i più recenti
            kept_items = kept_items[:MAX_QUEUE_SIZE]
        
        removed_count = initial_count - len(kept_items)
        if removed_count > 0:
            _watchdog_queue = kept_items
            _save_queue()
            logger.info(f"Pulizia coda watchdog: rimossi {removed_count} elementi vecchi")
        
        return removed_count


# Carica la coda all'avvio e pulisci elementi vecchi
_load_queue()
cleanup_old_items()

