"""
Estrattore testo usando pdfplumber
Mantenuto per rule detection e parsing mirato di tabelle/strutture complesse
"""
import logging
from typing import Optional, Tuple

logger = logging.getLogger(__name__)


def extract_text_with_pdfplumber(file_path: str, max_pages: int = 5) -> Tuple[Optional[str], dict]:
    """
    Estrae testo da PDF usando pdfplumber - utile per parsing strutturato
    
    Args:
        file_path: Percorso del file PDF
        max_pages: Numero massimo di pagine da processare (default: 5)
        
    Returns:
        Tupla (testo_estratto, metadati):
        - testo_estratto: Testo estratto o None se fallito
        - metadati: Dict con info su estrazione
    """
    try:
        import pdfplumber
        
        text_parts = []
        metadata = {
            "method": "pdfplumber",
            "pages_processed": 0,
            "total_pages": 0,
            "success": False
        }
        
        with pdfplumber.open(file_path) as pdf:
            metadata["total_pages"] = len(pdf.pages)
            pages_to_process = min(max_pages, len(pdf.pages))
            
            for i in range(pages_to_process):
                try:
                    page = pdf.pages[i]
                    page_text = page.extract_text()
                    
                    if page_text and page_text.strip():
                        text_parts.append(page_text)
                        metadata["pages_processed"] += 1
                except Exception as e:
                    logger.debug(f"Errore estrazione pagina {i} con pdfplumber: {e}")
                    continue
        
        if text_parts:
            full_text = "\n".join(text_parts)
            metadata["success"] = True
            logger.info(f"pdfplumber: estratto testo da {metadata['pages_processed']}/{metadata['total_pages']} pagine")
            return full_text, metadata
        else:
            logger.warning(f"pdfplumber: nessun testo estratto da {file_path}")
            return None, metadata
            
    except ImportError:
        logger.debug("pdfplumber non disponibile")
        return None, {"method": "pdfplumber", "error": "not_installed", "success": False}
    except Exception as e:
        logger.warning(f"Errore estrazione pdfplumber: {e}")
        return None, {"method": "pdfplumber", "error": str(e), "success": False}

