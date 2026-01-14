"""
Sistema centralizzato per gestione path assoluti e filesystem-safe
Garantisce che tutti i path siano assoluti e le directory siano scrivibili
Production-grade per deployment systemd
"""
import os
import logging
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

# Directory base del progetto (default: /var/www/DDT per produzione)
# Può essere sovrascritto con variabile d'ambiente DDT_BASE_DIR
_BASE_DIR: Optional[Path] = None


def get_base_dir() -> Path:
    """
    Restituisce la directory base assoluta del progetto
    
    Returns:
        Path assoluto alla directory base (/var/www/DDT in produzione)
        
    Note:
        - Usa DDT_BASE_DIR da variabile d'ambiente se presente
        - Default: /var/www/DDT per produzione
        - Garantisce che il path sia sempre assoluto
    """
    global _BASE_DIR
    
    if _BASE_DIR is not None:
        return _BASE_DIR
    
    # Leggi da variabile d'ambiente o usa default
    base_dir_str = os.getenv("DDT_BASE_DIR", "/var/www/DDT")
    _BASE_DIR = Path(base_dir_str).resolve()
    
    logger.info(f"📁 BASE_DIR inizializzato: {_BASE_DIR}")
    return _BASE_DIR


def ensure_dir(path: Path) -> Path:
    """
    Crea una directory se non esiste e verifica che sia scrivibile
    
    Args:
        path: Path della directory da creare/verificare
        
    Returns:
        Path assoluto della directory (garantito esistente e scrivibile)
        
    Raises:
        OSError: Se la directory non può essere creata o non è scrivibile
        
    Note:
        - Crea tutte le directory parent necessarie (parents=True)
        - Verifica scrivibilità con os.access
        - Logga errori chiari se la directory non è scrivibile
    """
    # Converti in Path assoluto se relativo
    if not path.is_absolute():
        base_dir = get_base_dir()
        path = base_dir / path
    
    # Risolvi eventuali link simbolici e path relativi
    path = path.resolve()
    
    # Crea directory se non esiste
    if not path.exists():
        try:
            path.mkdir(parents=True, exist_ok=True)
            logger.info(f"📁 Directory creata: {path}")
        except OSError as e:
            error_msg = f"Impossibile creare directory {path}: {e}"
            logger.error(f"❌ {error_msg}")
            raise OSError(error_msg) from e
    
    # Verifica che sia una directory
    if not path.is_dir():
        error_msg = f"Path esiste ma non è una directory: {path}"
        logger.error(f"❌ {error_msg}")
        raise OSError(error_msg)
    
    # Verifica scrivibilità
    if not os.access(path, os.W_OK):
        error_msg = (
            f"Directory non scrivibile: {path}\n"
            f"Verifica i permessi del filesystem e che l'utente '{os.getenv('USER', 'unknown')}' "
            f"abbia i permessi di scrittura."
        )
        logger.error(f"❌ {error_msg}")
        raise OSError(error_msg)
    
    return path


def get_path(subdir: str, filename: str = "") -> Path:
    """
    Costruisce un path assoluto basato su subdirectory e filename
    
    Args:
        subdir: Nome della sottodirectory (es: "inbox", "processed", "errors", "tmp")
        filename: Nome del file (opzionale, può essere vuoto per ottenere solo la directory)
        
    Returns:
        Path assoluto completo
        
    Examples:
        >>> get_path("inbox", "file.pdf")
        Path("/var/www/DDT/inbox/file.pdf")
        
        >>> get_path("processed")
        Path("/var/www/DDT/processed")
    """
    base_dir = get_base_dir()
    
    # Rimuovi slash iniziali/finali per consistenza
    subdir = subdir.strip("/")
    
    if filename:
        return base_dir / subdir / filename
    else:
        return base_dir / subdir


# Directory standard del progetto
def get_inbox_dir() -> Path:
    """Restituisce il path assoluto della directory inbox"""
    return ensure_dir(get_path("inbox"))


def get_processed_dir() -> Path:
    """Restituisce il path assoluto della directory processed"""
    return ensure_dir(get_path("processed"))


def get_errors_dir() -> Path:
    """Restituisce il path assoluto della directory errors"""
    return ensure_dir(get_path("errors"))


def get_tmp_dir() -> Path:
    """Restituisce il path assoluto della directory tmp"""
    return ensure_dir(get_path("tmp"))


def get_preview_dir() -> Path:
    """Restituisce il path assoluto della directory temp/preview"""
    return ensure_dir(get_path("tmp", "preview"))


def get_app_dir() -> Path:
    """Restituisce il path assoluto della directory app"""
    return ensure_dir(get_path("app"))


def get_excel_dir() -> Path:
    """Restituisce il path assoluto della directory excel"""
    return ensure_dir(get_path("excel"))


def get_corrections_dir() -> Path:
    """Restituisce il path assoluto della directory app/corrections"""
    return ensure_dir(get_path("app", "corrections"))


# Funzioni helper per file comuni
def get_excel_file() -> Path:
    """Restituisce il path assoluto del file Excel"""
    excel_dir = get_excel_dir()
    return excel_dir / "ddt.xlsx"


def get_processed_documents_file() -> Path:
    """Restituisce il path assoluto del file processed_documents.json"""
    app_dir = get_app_dir()
    return app_dir / "processed_documents.json"


def get_watchdog_queue_file() -> Path:
    """Restituisce il path assoluto del file watchdog_queue.json"""
    app_dir = get_app_dir()
    return app_dir / "watchdog_queue.json"


def get_corrections_file() -> Path:
    """Restituisce il path assoluto del file corrections.json"""
    corrections_dir = get_corrections_dir()
    return corrections_dir / "corrections.json"


def get_rules_file() -> Path:
    """Restituisce il path assoluto del file rules.json"""
    app_dir = get_app_dir()
    return app_dir / "rules" / "rules.json"


def get_layout_rules_file() -> Path:
    """Restituisce il path assoluto del file layout_rules.json"""
    app_dir = get_app_dir()
    return app_dir / "layout_rules" / "layout_rules.json"


def safe_copy(source: Path, dest: Path) -> Path:
    """
    Copia un file in modo sicuro usando path assoluti
    
    Args:
        source: Path assoluto del file sorgente
        dest: Path assoluto del file destinazione
        
    Returns:
        Path assoluto del file copiato
        
    Raises:
        FileNotFoundError: Se il file sorgente non esiste
        OSError: Se la copia fallisce o la destinazione non è scrivibile
        
    Note:
        - Garantisce che i path siano assoluti
        - Crea la directory destinazione se non esiste
        - Verifica scrivibilità della directory destinazione
    """
    import shutil
    
    # Converti in Path assoluti
    if not source.is_absolute():
        source = get_base_dir() / source
    source = source.resolve()
    
    if not dest.is_absolute():
        dest = get_base_dir() / dest
    dest = dest.resolve()
    
    # Verifica che il file sorgente esista
    if not source.exists():
        raise FileNotFoundError(f"File sorgente non trovato: {source}")
    
    if not source.is_file():
        raise ValueError(f"Path sorgente non è un file: {source}")
    
    # Crea directory destinazione se non esiste
    ensure_dir(dest.parent)
    
    # Copia il file
    try:
        shutil.copy2(str(source), str(dest))
        logger.debug(f"📋 File copiato: {source.name} → {dest}")
        return dest
    except Exception as e:
        error_msg = f"Errore copia file {source} → {dest}: {e}"
        logger.error(f"❌ {error_msg}")
        raise OSError(error_msg) from e


def safe_move(source: Path, dest: Path) -> Path:
    """
    Sposta un file in modo sicuro usando path assoluti
    
    Args:
        source: Path assoluto del file sorgente
        dest: Path assoluto del file destinazione
        
    Returns:
        Path assoluto del file spostato
        
    Raises:
        FileNotFoundError: Se il file sorgente non esiste
        OSError: Se lo spostamento fallisce o la destinazione non è scrivibile
        
    Note:
        - Garantisce che i path siano assoluti
        - Crea la directory destinazione se non esiste
        - Verifica scrivibilità della directory destinazione
    """
    import shutil
    
    # Converti in Path assoluti
    if not source.is_absolute():
        source = get_base_dir() / source
    source = source.resolve()
    
    if not dest.is_absolute():
        dest = get_base_dir() / dest
    dest = dest.resolve()
    
    # Verifica che il file sorgente esista
    if not source.exists():
        raise FileNotFoundError(f"File sorgente non trovato: {source}")
    
    if not source.is_file():
        raise ValueError(f"Path sorgente non è un file: {source}")
    
    # Crea directory destinazione se non esiste
    ensure_dir(dest.parent)
    
    # Sposta il file
    try:
        shutil.move(str(source), str(dest))
        logger.info(f"📦 File spostato: {source.name} → {dest}")
        return dest
    except Exception as e:
        error_msg = f"Errore spostamento file {source} → {dest}: {e}"
        logger.error(f"❌ {error_msg}")
        raise OSError(error_msg) from e


def safe_open(file_path: Path, mode: str = "r", **kwargs):
    """
    Apre un file in modo sicuro usando path assoluti
    
    Args:
        file_path: Path del file (può essere relativo o assoluto)
        mode: Modalità di apertura (default: "r")
        **kwargs: Argomenti aggiuntivi per open()
        
    Returns:
        File handle aperto
        
    Note:
        - Converte automaticamente path relativi in assoluti
        - Crea la directory parent se non esiste (solo per scrittura)
        - Verifica scrivibilità se in modalità scrittura
    """
    # Converti in Path assoluto
    if not file_path.is_absolute():
        file_path = get_base_dir() / file_path
    file_path = file_path.resolve()
    
    # Se in modalità scrittura, crea directory parent se necessario
    if any(m in mode for m in ['w', 'a', 'x']):
        ensure_dir(file_path.parent)
    
    return open(file_path, mode, **kwargs)
