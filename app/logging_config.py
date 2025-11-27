"""
Configurazione del logging per l'applicazione
"""
import logging
import sys
from pathlib import Path
from typing import Optional

def setup_logging(level: int = logging.INFO, log_file: Optional[Path] = None) -> None:
    """
    Configura il sistema di logging
    
    Args:
        level: Livello di logging (default: INFO)
        log_file: Path opzionale per log su file
    """
    # Formato dei log
    log_format = '%(asctime)s - %(name)s - %(levelname)s - %(message)s'
    date_format = '%Y-%m-%d %H:%M:%S'
    
    # Configura handlers
    handlers = [logging.StreamHandler(sys.stdout)]
    
    if log_file:
        log_file.parent.mkdir(parents=True, exist_ok=True)
        handlers.append(logging.FileHandler(log_file))
    
    # Configura logging
    logging.basicConfig(
        level=level,
        format=log_format,
        datefmt=date_format,
        handlers=handlers,
        force=True
    )
    
    # Riduci verbosità di alcune librerie
    logging.getLogger("watchdog").setLevel(logging.WARNING)
    logging.getLogger("openai").setLevel(logging.WARNING)
    logging.getLogger("httpx").setLevel(logging.WARNING)
    
    logging.info("Logging configurato")

