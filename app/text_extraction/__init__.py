"""
Modulo di estrazione testo per DDT PDF
Pipeline intelligente con fallback controllati
"""

from app.text_extraction.pymupdf_extractor import extract_text_with_pymupdf
from app.text_extraction.pdfplumber_extractor import extract_text_with_pdfplumber
from app.text_extraction.ocr_fallback import extract_text_with_ocr, is_ocr_available
from app.text_extraction.decision import is_text_reliable, TextExtractionResult, evaluate_extraction_result
from app.text_extraction.orchestrator import extract_text_pipeline, extract_text_for_rule_detection

__all__ = [
    "extract_text_with_pymupdf",
    "extract_text_with_pdfplumber",
    "extract_text_with_ocr",
    "is_ocr_available",
    "is_text_reliable",
    "TextExtractionResult",
    "evaluate_extraction_result",
    "extract_text_pipeline",
    "extract_text_for_rule_detection",
]

