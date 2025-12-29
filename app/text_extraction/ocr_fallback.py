"""
OCR fallback usando pytesseract
Usato SOLO quando PyMuPDF e pdfplumber falliscono
NON è il metodo di default - solo come ultima risorsa
"""
import logging
from typing import Optional, Tuple

logger = logging.getLogger(__name__)


def extract_text_with_ocr(file_path: str, max_pages: int = 3, dpi: int = 300) -> Tuple[Optional[str], dict]:
    """
    Estrae testo da PDF usando OCR (pytesseract) - SOLO come fallback
    
    ⚠️ ATTENZIONE: OCR è lento e costoso in termini di risorse.
    Usare solo quando gli altri metodi falliscono.
    
    Args:
        file_path: Percorso del file PDF
        max_pages: Numero massimo di pagine da processare (default: 3, limitato per performance)
        dpi: DPI per conversione immagine (default: 300, alto per qualità)
        
    Returns:
        Tupla (testo_estratto, metadati):
        - testo_estratto: Testo estratto o None se fallito
        - metadati: Dict con info su estrazione
    """
    try:
        import pytesseract
        from pdf2image import convert_from_path
        from PIL import Image
        
        metadata = {
            "method": "ocr",
            "pages_processed": 0,
            "total_pages": 0,
            "success": False,
            "dpi": dpi
        }
        
        # Converti PDF in immagini
        try:
            images = convert_from_path(file_path, dpi=dpi, first_page=1, last_page=max_pages)
            metadata["total_pages"] = len(images)
        except Exception as e:
            logger.warning(f"Errore conversione PDF in immagini per OCR: {e}")
            return None, {**metadata, "error": str(e)}
        
        if not images:
            logger.warning("OCR: nessuna immagine generata dal PDF")
            return None, {**metadata, "error": "no_images"}
        
        text_parts = []
        
        # Processa ogni immagine con OCR
        for i, image in enumerate(images):
            try:
                # OCR con lingua italiana e inglese
                page_text = pytesseract.image_to_string(
                    image,
                    lang='ita+eng',
                    config='--psm 6'  # Assume un unico blocco di testo uniforme
                )
                
                if page_text and page_text.strip():
                    text_parts.append(page_text)
                    metadata["pages_processed"] += 1
                    
            except Exception as e:
                logger.warning(f"Errore OCR pagina {i+1}: {e}")
                continue
        
        if text_parts:
            full_text = "\n".join(text_parts)
            metadata["success"] = True
            logger.info(f"OCR: estratto testo da {metadata['pages_processed']}/{metadata['total_pages']} pagine (DPI: {dpi})")
            return full_text, metadata
        else:
            logger.warning(f"OCR: nessun testo estratto da {file_path}")
            return None, metadata
            
    except ImportError as e:
        missing_module = str(e).split()[-1] if " " in str(e) else "unknown"
        logger.debug(f"OCR non disponibile: {missing_module} non installato")
        return None, {
            "method": "ocr",
            "error": f"not_installed_{missing_module}",
            "success": False
        }
    except Exception as e:
        logger.warning(f"Errore estrazione OCR: {e}")
        return None, {"method": "ocr", "error": str(e), "success": False}


def is_ocr_available() -> bool:
    """
    Verifica se OCR è disponibile nel sistema
    
    Returns:
        True se pytesseract e pdf2image sono installati e funzionanti
    """
    try:
        import pytesseract
        from pdf2image import convert_from_path
        
        # Prova a verificare che tesseract sia installato
        pytesseract.get_tesseract_version()
        return True
    except (ImportError, Exception):
        return False

