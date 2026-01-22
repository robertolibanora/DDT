"""
Sistema di configurazione globale persistente per DDT Reader
Gestisce parametri operativi globali come la data della cartella di output

IMPORTANTE: Usa file locking cross-process per coordinamento WEB/WORKER
"""
import json
import logging
import os
import threading
from pathlib import Path
from typing import Optional, Dict, Any
from datetime import datetime

logger = logging.getLogger(__name__)

# Lock thread-local per cache (complementare al file lock cross-process)
_config_lock = threading.Lock()

# Cache della configurazione (thread-safe)
_config_cache: Optional[Dict[str, Any]] = None

from app.paths import get_app_dir, ensure_dir, safe_open
from app.file_lock import file_lock

# PATH UNICO E ASSOLUTO per configurazione globale
CONFIG_FILE = get_app_dir() / "global_config.json"

# Valore default per la data di output (oggi in formato gg-mm-yyyy)
def _get_default_output_date() -> str:
    """Restituisce la data odierna in formato gg-mm-yyyy"""
    today = datetime.now()
    return f"{today.day:02d}-{today.month:02d}-{today.year}"


def _load_config() -> Dict[str, Any]:
    """
    Carica la configurazione globale dal file JSON (READ-ONLY ASSOLUTO).
    
    REGOLA FERREA: NESSUNA SCRITTURA SU DISCO.
    - Se file non esiste → ritorna default IN MEMORIA
    - Se JSON invalido → log errore + ritorna default IN MEMORIA
    - MAI chiama _save_config()
    - MAI crea file
    
    Usa file locking condiviso per lettura cross-process.
    
    Returns:
        Dizionario con la configurazione globale
    """
    global _config_cache
    
    # Double-check locking pattern per thread-safety (cache)
    if _config_cache is not None:
        return _config_cache
    
    with _config_lock:
        # Verifica di nuovo dentro il lock (double-check)
        if _config_cache is not None:
            return _config_cache
        
        # File locking condiviso per lettura cross-process
        try:
            with file_lock(CONFIG_FILE, exclusive=False, timeout=3.0):
                if not CONFIG_FILE.exists():
                    # File non esiste → ritorna default IN MEMORIA (NESSUNA SCRITTURA)
                    logger.debug(
                        f"File configurazione globale non trovato: {CONFIG_FILE}, "
                        f"uso valori default in memoria (PID={os.getpid()})"
                    )
                    _config_cache = {
                        "active_output_date": _get_default_output_date(),
                        "last_updated": datetime.now().isoformat()
                    }
                    return _config_cache
                
                # Leggi file sotto lock condiviso
                with safe_open(CONFIG_FILE, 'r', encoding='utf-8') as f:
                    _config_cache = json.load(f)
                
                # Assicura che la struttura sia corretta (solo in memoria)
                if "active_output_date" not in _config_cache:
                    logger.warning(
                        f"Campo 'active_output_date' mancante in config, "
                        f"uso default in memoria (PID={os.getpid()})"
                    )
                    _config_cache["active_output_date"] = _get_default_output_date()
                
                logger.debug(
                    f"Configurazione globale caricata: "
                    f"active_output_date={_config_cache.get('active_output_date')} "
                    f"(PID={os.getpid()})"
                )
                return _config_cache
                
        except json.JSONDecodeError as e:
            # JSON invalido → ritorna default IN MEMORIA (NESSUNA SCRITTURA)
            logger.error(
                f"Errore parsing JSON configurazione globale: {e} "
                f"(PID={os.getpid()})"
            )
            _config_cache = {
                "active_output_date": _get_default_output_date(),
                "last_updated": datetime.now().isoformat()
            }
            return _config_cache
        except Exception as e:
            # Errore generico → ritorna default IN MEMORIA (NESSUNA SCRITTURA)
            logger.error(
                f"Errore caricamento configurazione globale: {e} "
                f"(PID={os.getpid()})",
                exc_info=True
            )
            _config_cache = {
                "active_output_date": _get_default_output_date(),
                "last_updated": datetime.now().isoformat()
            }
            return _config_cache


def _save_config(config: Dict[str, Any]) -> None:
    """
    Salva la configurazione globale nel file JSON (thread-safe, atomico, cross-process).
    
    Usa file locking ESCLUSIVO per garantire coordinamento tra WEB e WORKER.
    
    Args:
        config: Dizionario con la configurazione globale
        
    Raises:
        TimeoutError: Se il lock non può essere acquisito
        OSError: Se c'è un errore I/O durante la scrittura
    """
    global _config_cache
    
    pid = os.getpid()
    timestamp = datetime.now().isoformat()
    
    # File locking esclusivo per scrittura cross-process
    try:
        with file_lock(CONFIG_FILE, exclusive=True, timeout=3.0):
            with _config_lock:
                try:
                    # Assicura che la directory esista
                    ensure_dir(CONFIG_FILE.parent)
                    
                    # Aggiungi timestamp di aggiornamento
                    config["last_updated"] = timestamp
                    
                    # Scrittura atomica: scrivi in file temporaneo, poi rename
                    temp_file = CONFIG_FILE.with_suffix('.json.tmp')
                    
                    with safe_open(temp_file, 'w', encoding='utf-8') as f:
                        json.dump(config, f, indent=2, ensure_ascii=False)
                        f.flush()
                        os.fsync(f.fileno())  # Forza scrittura su disco
                    
                    # Rename atomico (cross-platform)
                    temp_file.replace(CONFIG_FILE)
                    
                    # Aggiorna la cache
                    _config_cache = config.copy()
                    
                    logger.info(
                        f"✅ Configurazione globale salvata: "
                        f"active_output_date={config.get('active_output_date')} "
                        f"(PID={pid}, timestamp={timestamp}, path={CONFIG_FILE})"
                    )
                except (OSError, IOError, PermissionError) as e:
                    logger.error(
                        f"Errore I/O salvataggio configurazione globale: {e} "
                        f"(PID={pid}, path={CONFIG_FILE})",
                        exc_info=True
                    )
                    raise
                except Exception as e:
                    logger.error(
                        f"Errore salvataggio configurazione globale: {e} "
                        f"(PID={pid}, path={CONFIG_FILE})",
                        exc_info=True
                    )
                    raise
    except TimeoutError as e:
        logger.error(
            f"Timeout acquisizione lock per salvataggio config "
            f"(PID={pid}, path={CONFIG_FILE})"
        )
        raise


def get_active_output_date() -> str:
    """
    Ottiene la data attiva corrente per la cartella di output
    
    Returns:
        Data in formato gg-mm-yyyy (es: "15-01-2026")
    """
    config = _load_config()
    return config.get("active_output_date", _get_default_output_date())


def set_active_output_date(date_str: str) -> None:
    """
    Imposta la data attiva per la cartella di output.
    
    Usa file locking esclusivo per garantire scrittura atomica cross-process.
    
    Args:
        date_str: Data in formato gg-mm-yyyy (es: "15-01-2026")
        
    Raises:
        ValueError: Se il formato della data non è valido
        TimeoutError: Se il lock non può essere acquisito
        OSError: Se c'è un errore I/O durante la scrittura
    """
    pid = os.getpid()
    
    # Valida formato data (gg-mm-yyyy)
    try:
        parts = date_str.split("-")
        if len(parts) != 3:
            raise ValueError("Formato data non valido")
        giorno, mese, anno = parts
        int(giorno), int(mese), int(anno)  # Verifica che siano numeri
        if len(anno) != 4:
            raise ValueError("Anno deve essere a 4 cifre")
        if int(giorno) < 1 or int(giorno) > 31:
            raise ValueError("Giorno non valido")
        if int(mese) < 1 or int(mese) > 12:
            raise ValueError("Mese non valido")
    except (ValueError, AttributeError) as e:
        raise ValueError(f"Formato data non valido (atteso gg-mm-yyyy): {date_str}") from e
    
    # Carica config corrente (read-only, sotto lock condiviso)
    config = _load_config()
    old_date = config.get("active_output_date")
    
    # Aggiorna e salva (sotto lock esclusivo)
    config["active_output_date"] = date_str
    _save_config(config)
    
    logger.info(
        f"📅 Output-date salvato: {old_date} → {date_str} "
        f"(PID={pid}, path={CONFIG_FILE})"
    )


def reload_config() -> None:
    """Ricarica la configurazione dal file (forza refresh cache, thread-safe)"""
    global _config_cache
    
    with _config_lock:
        _config_cache = None
        _load_config()
        logger.info("Configurazione globale ricaricata")


def ensure_config_file() -> None:
    """
    Inizializza il file di configurazione globale all'avvio del server.
    Crea il file con valori default SOLO SE NON ESISTE.
    
    REGOLA FERREA:
    - Chiamata SOLO all'avvio applicazione (lifespan startup)
    - MAI chiamata da endpoint GET
    - Usa lock esclusivo per creazione atomica cross-process
    
    IMPORTANTE: Chiamare questa funzione UNA SOLA VOLTA all'avvio.
    """
    global _config_cache
    
    pid = os.getpid()
    
    # File locking esclusivo per creazione atomica
    try:
        with file_lock(CONFIG_FILE, exclusive=True, timeout=3.0):
            with _config_lock:
                if CONFIG_FILE.exists():
                    # File già esiste, carica normalmente (read-only)
                    _load_config()
                    logger.debug(
                        f"File configurazione globale esistente: {CONFIG_FILE} "
                        f"(PID={pid})"
                    )
                    return
                
                # File non esiste, crealo con valori default
                logger.info(
                    f"📝 Global config inizializzata: creo {CONFIG_FILE} "
                    f"(PID={pid})"
                )
                
                # Assicura che la directory esista
                ensure_dir(CONFIG_FILE.parent)
                
                default_config = {
                    "active_output_date": _get_default_output_date(),
                    "last_updated": datetime.now().isoformat()
                }
                
                # Salva il file iniziale (sotto lock esclusivo già acquisito)
                # Nota: _save_config() acquisirà il suo lock, ma siamo già dentro
                # un lock esclusivo, quindi va bene (lock annidati OK per stesso processo)
                temp_file = CONFIG_FILE.with_suffix('.json.tmp')
                
                with safe_open(temp_file, 'w', encoding='utf-8') as f:
                    json.dump(default_config, f, indent=2, ensure_ascii=False)
                    f.flush()
                    os.fsync(f.fileno())
                
                temp_file.replace(CONFIG_FILE)
                
                # Aggiorna cache
                _config_cache = default_config.copy()
                
                logger.info(
                    f"✅ Global config inizializzata: "
                    f"active_output_date={default_config['active_output_date']} "
                    f"(PID={pid}, path={CONFIG_FILE})"
                )
    except TimeoutError as e:
        logger.error(
            f"Timeout acquisizione lock per inizializzazione config "
            f"(PID={pid}, path={CONFIG_FILE})"
        )
        raise
    except Exception as e:
        logger.error(
            f"Errore inizializzazione configurazione globale: {e} "
            f"(PID={pid}, path={CONFIG_FILE})",
            exc_info=True
        )
        raise
