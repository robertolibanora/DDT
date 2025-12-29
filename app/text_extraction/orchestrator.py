"""
Orchestratore della pipeline di estrazione testo
Gestisce la sequenza: PyMuPDF -> pdfplumber -> OCR (solo se necessario)
"""
import logging
from typing import Optional

from app.text_extraction.pymupdf_extractor import extract_text_with_pymupdf
from app.text_extraction.pdfplumber_extractor import extract_text_with_pdfplumber
from app.text_extraction.ocr_fallback import extract_text_with_ocr, is_ocr_available
from app.text_extraction.decision import evaluate_extraction_result, TextExtractionResult

logger = logging.getLogger(__name__)


def extract_text_pipeline(file_path: str, max_pages: int = 5, enable_ocr: bool = True) -> TextExtractionResult:
    """
    Pipeline completa di estrazione testo con fallback controllati
    
    Sequenza:
    1. PyMuPDF (veloce, PDF nativi)
    2. pdfplumber (parsing strutturato, rule detection)
    3. OCR (solo se necessario e abilitato)
    
    Args:
        file_path: Percorso del file PDF
        max_pages: Numero massimo di pagine da processare
        enable_ocr: Se True, permette fallback OCR (default: True)
        
    Returns:
        TextExtractionResult con testo estratto e valutazione
    """
    # Step 1: Prova PyMuPDF (più veloce)
    logger.debug(f"Pipeline estrazione testo: tentativo PyMuPDF per {file_path}")
    text, metadata = extract_text_with_pymupdf(file_path, max_pages=max_pages)
    
    if text:
        result = evaluate_extraction_result(text, "pymupdf", metadata)
        if result.is_reliable:
            logger.info(f"✅ Testo affidabile estratto con PyMuPDF (confidence: {result.confidence_score:.2f})")
            return result
        else:
            logger.info(f"⚠️ PyMuPDF estratto ma testo non affidabile: {result.reason}")
            # Continua con pdfplumber per migliorare
    
    # Step 2: Prova pdfplumber (migliore per parsing strutturato)
    logger.debug(f"Pipeline estrazione testo: tentativo pdfplumber per {file_path}")
    text, metadata = extract_text_with_pdfplumber(file_path, max_pages=max_pages)
    
    if text:
        result = evaluate_extraction_result(text, "pdfplumber", metadata)
        if result.is_reliable:
            logger.info(f"✅ Testo affidabile estratto con pdfplumber (confidence: {result.confidence_score:.2f})")
            return result
        else:
            logger.info(f"⚠️ pdfplumber estratto ma testo non affidabile: {result.reason}")
    
    # Step 3: OCR solo come ultima risorsa (se abilitato)
    if enable_ocr and is_ocr_available():
        logger.info(f"Pipeline estrazione testo: tentativo OCR (fallback) per {file_path}")
        # OCR è più lento, limitiamo a 3 pagine
        text, metadata = extract_text_with_ocr(file_path, max_pages=min(3, max_pages), dpi=300)
        
        if text:
            result = evaluate_extraction_result(text, "ocr", metadata)
            logger.info(f"✅ Testo estratto con OCR (confidence: {result.confidence_score:.2f})")
            return result
        else:
            logger.warning(f"❌ OCR fallito: {metadata.get('error', 'unknown')}")
    elif enable_ocr and not is_ocr_available():
        logger.debug("OCR richiesto ma non disponibile nel sistema")
    
    # Nessun metodo ha funzionato
    logger.warning(f"❌ Nessun testo estratto da {file_path} con nessun metodo")
    return TextExtractionResult(
        text="",
        is_reliable=False,
        confidence_score=0.0,
        method="none",
        metadata={"error": "all_methods_failed"},
        reason="nessun_metodo_riuscito"
    )


def extract_text_for_rule_detection(file_path: str) -> str:
    """
    Estrae testo ottimizzato per rule detection
    Usa la pipeline completa ma ritorna solo il testo (compatibilità legacy)
    
    Args:
        file_path: Percorso del file PDF
        
    Returns:
        Testo estratto (stringa vuota se fallito)
    """
    result = extract_text_pipeline(file_path, max_pages=5, enable_ocr=False)  # OCR non necessario per rule detection
    return result.text if result else ""

