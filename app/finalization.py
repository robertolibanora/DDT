"""
Sistema di finalizzazione documenti DDT
Gestisce rinomina, spostamento e archiviazione deterministica
"""
import os
import shutil
import logging
import re
from pathlib import Path
from typing import Optional, Tuple
from app.config import INBOX_DIR, PROCESSATI_DIR

logger = logging.getLogger(__name__)


def sanitize_filename(text: str) -> str:
    """
    Sanitizza un testo per usarlo come nome file
    
    Args:
        text: Testo da sanitizzare
        
    Returns:
        Testo sanitizzato (spazi → _, rimozione caratteri speciali)
    """
    if not text:
        return "UNKNOWN"
    
    # Sostituisci spazi con underscore
    sanitized = text.replace(" ", "_")
    
    # Rimuovi caratteri speciali non validi per nomi file
    # Mantieni solo lettere, numeri, underscore, trattini e punti
    sanitized = re.sub(r'[^\w\-_.]', '', sanitized)
    
    # Rimuovi underscore multipli
    sanitized = re.sub(r'_+', '_', sanitized)
    
    # Rimuovi underscore iniziali/finali
    sanitized = sanitized.strip('_')
    
    if not sanitized:
        return "UNKNOWN"
    
    return sanitized


def generate_final_filename(mittente: str, destinatario: str, numero_documento: str) -> str:
    """
    Genera il nome file finale standardizzato
    
    Args:
        mittente: Nome mittente
        destinatario: Nome destinatario
        numero_documento: Numero documento
        
    Returns:
        Nome file nel formato: Mittente_Destinatario_Numero.pdf
    """
    mittente_sanitized = sanitize_filename(mittente)
    destinatario_sanitized = sanitize_filename(destinatario)
    numero_sanitized = sanitize_filename(numero_documento)
    
    filename = f"{mittente_sanitized}_{destinatario_sanitized}_{numero_sanitized}.pdf"
    
    # Limita lunghezza totale (255 caratteri per filesystem)
    if len(filename) > 250:
        # Tronca mantenendo le parti più importanti
        max_mittente = min(80, len(mittente_sanitized))
        max_destinatario = min(80, len(destinatario_sanitized))
        max_numero = min(50, len(numero_sanitized))
        
        mittente_sanitized = mittente_sanitized[:max_mittente]
        destinatario_sanitized = destinatario_sanitized[:max_destinatario]
        numero_sanitized = numero_sanitized[:max_numero]
        
        filename = f"{mittente_sanitized}_{destinatario_sanitized}_{numero_sanitized}.pdf"
    
    return filename


def finalize_document(
    file_path: str,
    doc_hash: str,
    data_inserimento: str,
    mittente: str,
    destinatario: str,
    numero_documento: str
) -> Tuple[bool, Optional[str], Optional[str]]:
    """
    Finalizza un documento: rinomina, crea cartella, sposta e elimina da inbox
    
    Args:
        file_path: Percorso del file PDF originale (in inbox)
        doc_hash: Hash SHA256 del documento
        data_inserimento: Data di inserimento scelta dall'utente (gg-mm-yyyy)
        mittente: Nome mittente
        destinatario: Nome destinatario
        numero_documento: Numero documento
        
    Returns:
        Tupla (success: bool, final_path: Optional[str], error_message: Optional[str])
        
    Raises:
        ValueError: Se data_inserimento non è valida o manca
        OSError: Se operazioni filesystem falliscono
    """
    if not data_inserimento:
        raise ValueError("data_inserimento è obbligatoria per la finalizzazione")
    
    # Valida formato data (gg-mm-yyyy)
    try:
        parts = data_inserimento.split("-")
        if len(parts) != 3:
            raise ValueError("Formato data non valido")
        giorno, mese, anno = parts
        int(giorno), int(mese), int(anno)  # Verifica che siano numeri
        if len(anno) != 4:
            raise ValueError("Anno deve essere a 4 cifre")
    except (ValueError, AttributeError) as e:
        raise ValueError(f"Formato data_inserimento non valido (atteso gg-mm-yyyy): {data_inserimento}") from e
    
    # Verifica che il file esista
    source_path = Path(file_path)
    if not source_path.exists():
        error_msg = f"File sorgente non trovato: {file_path}"
        logger.error(f"❌ {error_msg}")
        return False, None, error_msg
    
    # Verifica che il file sia in inbox (sicurezza)
    from app.paths import get_inbox_dir, ensure_dir, safe_move
    inbox_path = get_inbox_dir()
    source_path = source_path.resolve()
    if not str(source_path).startswith(str(inbox_path.resolve())):
        error_msg = f"File non è in inbox: {file_path}"
        logger.error(f"❌ {error_msg}")
        return False, None, error_msg
    
    try:
        # Genera nome file finale
        final_filename = generate_final_filename(mittente, destinatario, numero_documento)
        logger.info(f"📝 Nome file finale generato: {final_filename}")
        
        # Crea percorso destinazione: processati/gg-mm-yyyy/
        from app.paths import get_processed_dir
        processati_base = get_processed_dir()
        target_dir = processati_base / data_inserimento
        
        # Crea cartella se non esiste e verifica scrivibilità
        target_dir = ensure_dir(target_dir)
        logger.info(f"📁 Cartella destinazione: {target_dir}")
        
        # Percorso file finale
        target_path = target_dir / final_filename
        
        # Se il file esiste già, aggiungi un contatore
        counter = 1
        original_target_path = target_path
        while target_path.exists():
            name_part = original_target_path.stem
            target_path = target_dir / f"{name_part}_{counter}.pdf"
            counter += 1
            if counter > 1000:  # Protezione contro loop infiniti
                raise OSError(f"Troppi file duplicati per {final_filename}")
        
        # Sposta il file (operazione atomica) usando safe_move
        target_path = safe_move(source_path, target_path)
        logger.info(f"✅ File spostato: {source_path.name} → {target_path}")
        
        # Verifica che il file sia stato spostato correttamente
        if not target_path.exists():
            error_msg = f"File non trovato dopo spostamento: {target_path}"
            logger.error(f"❌ {error_msg}")
            return False, None, error_msg
        
        # Verifica che il file originale non esista più in inbox
        if source_path.exists():
            logger.warning(f"⚠️ File ancora presente in inbox dopo spostamento: {source_path}")
            # Prova a eliminarlo manualmente
            try:
                source_path.unlink()
                logger.info(f"🗑️ File rimosso manualmente da inbox: {source_path}")
            except Exception as e:
                logger.error(f"❌ Impossibile rimuovere file da inbox: {e}")
        
        final_path_str = str(target_path)
        logger.info(f"✅ Documento finalizzato con successo: {final_path_str}")
        return True, final_path_str, None
        
    except OSError as e:
        error_msg = f"Errore filesystem durante finalizzazione: {str(e)}"
        logger.error(f"❌ {error_msg}", exc_info=True)
        return False, None, error_msg
    except Exception as e:
        error_msg = f"Errore imprevisto durante finalizzazione: {str(e)}"
        logger.error(f"❌ {error_msg}", exc_info=True)
        return False, None, error_msg
