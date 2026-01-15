"""
Sistema di configurazione globale persistente per DDT Reader
Gestisce parametri operativi globali come la data della cartella di output
"""
import json
import logging
import threading
from pathlib import Path
from typing import Optional, Dict, Any
from datetime import datetime

logger = logging.getLogger(__name__)

# Lock per operazioni thread-safe
_config_lock = threading.Lock()

# Cache della configurazione (thread-safe)
_config_cache: Optional[Dict[str, Any]] = None

from app.paths import get_app_dir, ensure_dir, safe_open

CONFIG_FILE = get_app_dir() / "global_config.json"

# Valore default per la data di output (oggi in formato gg-mm-yyyy)
def _get_default_output_date() -> str:
    """Restituisce la data odierna in formato gg-mm-yyyy"""
    today = datetime.now()
    return f"{today.day:02d}-{today.month:02d}-{today.year}"


def _load_config() -> Dict[str, Any]:
    """
    Carica la configurazione globale dal file JSON (thread-safe)
    
    Returns:
        Dizionario con la configurazione globale
    """
    global _config_cache
    
    # Double-check locking pattern per thread-safety
    if _config_cache is not None:
        return _config_cache
    
    with _config_lock:
        # Verifica di nuovo dentro il lock (double-check)
        if _config_cache is not None:
            return _config_cache
        
        if not CONFIG_FILE.exists():
            logger.info(f"File configurazione globale non trovato, creo {CONFIG_FILE} con valori default")
            default_config = {
                "active_output_date": _get_default_output_date(),
                "last_updated": datetime.now().isoformat()
            }
            _config_cache = default_config
            _save_config(_config_cache)
            return _config_cache
        
        try:
            with safe_open(CONFIG_FILE, 'r', encoding='utf-8') as f:
                _config_cache = json.load(f)
            
            # Assicura che la struttura sia corretta
            if "active_output_date" not in _config_cache:
                _config_cache["active_output_date"] = _get_default_output_date()
                _save_config(_config_cache)
            
            logger.debug(f"Configurazione globale caricata: active_output_date={_config_cache.get('active_output_date')}")
            return _config_cache
        except json.JSONDecodeError as e:
            logger.error(f"Errore parsing JSON configurazione globale: {e}")
            _config_cache = {
                "active_output_date": _get_default_output_date(),
                "last_updated": datetime.now().isoformat()
            }
            _save_config(_config_cache)
            return _config_cache
        except Exception as e:
            logger.error(f"Errore caricamento configurazione globale: {e}", exc_info=True)
            _config_cache = {
                "active_output_date": _get_default_output_date(),
                "last_updated": datetime.now().isoformat()
            }
            _save_config(_config_cache)
            return _config_cache


def _save_config(config: Dict[str, Any]) -> None:
    """
    Salva la configurazione globale nel file JSON (thread-safe)
    
    Args:
        config: Dizionario con la configurazione globale
    """
    global _config_cache
    
    with _config_lock:
        try:
            # Assicura che la directory esista
            ensure_dir(CONFIG_FILE.parent)
            
            # Aggiungi timestamp di aggiornamento
            config["last_updated"] = datetime.now().isoformat()
            
            with safe_open(CONFIG_FILE, 'w', encoding='utf-8') as f:
                json.dump(config, f, indent=2, ensure_ascii=False)
            
            # Aggiorna la cache
            _config_cache = config.copy()
            logger.info(f"✅ Configurazione globale salvata: active_output_date={config.get('active_output_date')}")
        except Exception as e:
            logger.error(f"Errore salvataggio configurazione globale: {e}", exc_info=True)
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
    Imposta la data attiva per la cartella di output
    
    Args:
        date_str: Data in formato gg-mm-yyyy (es: "15-01-2026")
        
    Raises:
        ValueError: Se il formato della data non è valido
    """
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
    
    config = _load_config()
    old_date = config.get("active_output_date")
    config["active_output_date"] = date_str
    _save_config(config)
    
    logger.info(f"📅 Data output aggiornata: {old_date} → {date_str}")


def reload_config() -> None:
    """Ricarica la configurazione dal file (forza refresh cache, thread-safe)"""
    global _config_cache
    
    with _config_lock:
        _config_cache = None
        _load_config()
        logger.info("Configurazione globale ricaricata")
