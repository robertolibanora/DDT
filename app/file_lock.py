"""
File locking cross-process usando fcntl.flock (Linux)
Garantisce coordinamento tra processi WEB e WORKER
"""
import fcntl
import logging
import os
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

# Timeout default per acquisizione lock (secondi)
LOCK_TIMEOUT = 3.0

# Lock file per ogni file JSON condiviso
_lock_files: dict[Path, int] = {}


def _get_lock_file_path(file_path: Path) -> Path:
    """Restituisce il path del file di lock associato a un file JSON"""
    return file_path.parent / f".{file_path.name}.lock"


@contextmanager
def file_lock(file_path: Path, exclusive: bool = True, timeout: float = LOCK_TIMEOUT):
    """
    Context manager per file locking cross-process.
    
    Args:
        file_path: Path del file da proteggere
        exclusive: True per lock esclusivo (scrittura), False per lock condiviso (lettura)
        timeout: Timeout in secondi per acquisizione lock (default 3s)
    
    Yields:
        File handle del lock file
        
    Raises:
        TimeoutError: Se il lock non può essere acquisito entro timeout
        OSError: Se c'è un errore I/O con il lock file
    """
    lock_path = _get_lock_file_path(file_path)
    lock_fd = None
    
    try:
        # Crea directory se non esiste
        lock_path.parent.mkdir(parents=True, exist_ok=True)
        
        # Apri lock file (crea se non esiste)
        lock_fd = os.open(str(lock_path), os.O_CREAT | os.O_RDWR)
        
        # Tipo di lock: LOCK_EX (esclusivo) o LOCK_SH (condiviso)
        lock_type = fcntl.LOCK_EX if exclusive else fcntl.LOCK_SH
        
        # Prova ad acquisire lock con timeout
        start_time = time.time()
        acquired = False
        
        while not acquired:
            try:
                # Prova lock non-bloccante
                fcntl.flock(lock_fd, lock_type | fcntl.LOCK_NB)
                acquired = True
            except (IOError, OSError) as e:
                # Lock già acquisito, aspetta e riprova
                if time.time() - start_time >= timeout:
                    error_msg = (
                        f"Timeout acquisizione lock per {file_path} "
                        f"(exclusive={exclusive}, timeout={timeout}s). "
                        f"PID={os.getpid()}"
                    )
                    logger.error(error_msg)
                    raise TimeoutError(error_msg) from e
                
                # Aspetta 50ms prima di riprovare
                time.sleep(0.05)
        
        # Lock acquisito con successo
        lock_mode = "EXCLUSIVE" if exclusive else "SHARED"
        logger.debug(
            f"🔒 Lock {lock_mode} acquisito: {file_path.name} (PID={os.getpid()})"
        )
        
        # Yield per eseguire operazione protetta
        yield lock_fd
        
    except Exception as e:
        logger.error(
            f"❌ Errore acquisizione lock per {file_path}: {e} (PID={os.getpid()})",
            exc_info=True
        )
        raise
    finally:
        # Rilascia lock
        if lock_fd is not None:
            try:
                fcntl.flock(lock_fd, fcntl.LOCK_UN)
                logger.debug(f"🔓 Lock rilasciato: {file_path.name} (PID={os.getpid()})")
            except Exception as e:
                logger.warning(f"Errore rilascio lock per {file_path}: {e}")
            finally:
                os.close(lock_fd)
