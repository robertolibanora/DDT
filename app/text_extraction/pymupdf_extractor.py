"""
Estrattore testo veloce usando PyMuPDF (fitz)
Primo livello della pipeline - estrazione performante per PDF nativi
"""
import logging
from typing import Optional, Tuple

logger = logging.getLogger(__name__)


def extract_text_with_pymupdf(file_path: str, max_pages: int = 5) -> Tuple[Optional[str], dict]:
    """
    Estrae testo da PDF usando PyMuPDF (fitz) - metodo veloce per PDF nativi
    
    Args:
        file_path: Percorso del file PDF
        max_pages: Numero massimo di pagine da processare (default: 5)
        
    Returns:
        Tupla (testo_estratto, metadati):
        - testo_estratto: Testo estratto o None se fallito
        - metadati: Dict con info su estrazione (pages_processed, total_pages, method)
    """
    try:
        import fitz  # PyMuPDF
        
        text_parts = []
        metadata = {
            "method": "pymupdf",
            "pages_processed": 0,
            "total_pages": 0,
            "success": False
        }
        
        doc = fitz.open(file_path)
        metadata["total_pages"] = len(doc)
        
        # Processa fino a max_pages pagine (o tutte se meno)
        pages_to_process = min(max_pages, len(doc))
        
        for page_num in range(pages_to_process):
            try:
                page = doc[page_num]
                page_text = page.get_text()
                
                if page_text and page_text.strip():
                    text_parts.append(page_text)
                    metadata["pages_processed"] += 1
            except Exception as e:
                logger.debug(f"Errore estrazione pagina {page_num} con PyMuPDF: {e}")
                continue
        
        doc.close()
        
        if text_parts:
            full_text = "\n".join(text_parts)
            metadata["success"] = True
            logger.info(f"PyMuPDF: estratto testo da {metadata['pages_processed']}/{metadata['total_pages']} pagine")
            return full_text, metadata
        else:
            logger.warning(f"PyMuPDF: nessun testo estratto da {file_path}")
            return None, metadata
            
    except ImportError:
        logger.debug("PyMuPDF (fitz) non disponibile")
        return None, {"method": "pymupdf", "error": "not_installed", "success": False}
    except Exception as e:
        logger.warning(f"Errore estrazione PyMuPDF: {e}")
        return None, {"method": "pymupdf", "error": str(e), "success": False}

