"""
Decision engine per valutare l'affidabilità del testo estratto
Determina se il testo è sufficientemente affidabile per rule detection e grounding
"""
import logging
import re
from dataclasses import dataclass

logger = logging.getLogger(__name__)


@dataclass
class TextExtractionResult:
    """Risultato dell'estrazione testo con metadati"""
    text: str
    is_reliable: bool
    confidence_score: float
    method: str
    metadata: dict
    reason: str  # Motivo della decisione


# Keyword chiave per DDT italiani
DDT_KEYWORDS = [
    "ddt", "documento", "trasporto", "data", "kg", "totale",
    "mittente", "destinatario", "numero", "documento", "peso",
    "quantità", "descrizione", "unità", "prezzo", "importo"
]


def _calculate_keyword_density(text: str) -> float:
    """
    Calcola la densità di keyword DDT nel testo
    
    Returns:
        Float tra 0 e 1 rappresentante la densità keyword
    """
    if not text:
        return 0.0
    
    text_lower = text.lower()
    found_keywords = sum(1 for keyword in DDT_KEYWORDS if keyword in text_lower)
    total_keywords = len(DDT_KEYWORDS)
    
    return found_keywords / total_keywords if total_keywords > 0 else 0.0


def _calculate_readability_score(text: str) -> float:
    """
    Calcola uno score di leggibilità del testo
    
    Criteri:
    - Percentuale caratteri alfanumerici
    - Assenza di pattern OCR-like (molte lettere isolate)
    - Presenza di spazi e punteggiatura normale
    
    Returns:
        Float tra 0 e 1
    """
    if not text:
        return 0.0
    
    # Percentuale caratteri leggibili (alfanumerici + spazi + punteggiatura comune)
    readable_chars = sum(1 for c in text if c.isalnum() or c in " .,;:/-()[]{}")
    readability_ratio = readable_chars / len(text) if len(text) > 0 else 0.0
    
    # Penalizza pattern OCR-like (molte lettere isolate)
    # Pattern: spazio + singola lettera + spazio (es. " a b c ")
    isolated_letters = len(re.findall(r'\s[a-zA-Z]\s', text))
    isolated_penalty = min(isolated_letters / max(len(text.split()), 1), 0.3)
    
    # Penalizza sequenze di caratteri strani ripetuti
    # Nota: il trattino - deve essere escapato o messo alla fine/inizio della classe caratteri
    strange_patterns = len(re.findall(r'[^\w\s.,;:/()\[\]{}-]{3,}', text))
    strange_penalty = min(strange_patterns * 0.1, 0.2)
    
    final_score = readability_ratio - isolated_penalty - strange_penalty
    return max(0.0, min(1.0, final_score))


def is_text_reliable(text: str, min_length: int = 100) -> Tuple[bool, float, str]:
    """
    Valuta se il testo estratto è affidabile per rule detection e grounding
    
    Criteri di affidabilità:
    1. Lunghezza minima (default: 100 caratteri)
    2. Densità keyword DDT (almeno 20% delle keyword presenti)
    3. Leggibilità (score > 0.5)
    4. Assenza di pattern OCR-like eccessivi
    
    Args:
        text: Testo estratto da valutare
        min_length: Lunghezza minima richiesta (default: 100)
        
    Returns:
        Tupla (is_reliable, confidence_score, reason):
        - is_reliable: True se il testo è affidabile
        - confidence_score: Score di confidenza tra 0 e 1
        - reason: Motivo della decisione (per logging)
    """
    if not text or not isinstance(text, str):
        return False, 0.0, "testo_vuoto_o_non_valido"
    
    text = text.strip()
    
    # Criterio 1: Lunghezza minima
    if len(text) < min_length:
        return False, 0.0, f"testo_troppo_corto_{len(text)}_caratteri"
    
    # Criterio 2: Densità keyword
    keyword_density = _calculate_keyword_density(text)
    if keyword_density < 0.2:  # Almeno 20% delle keyword devono essere presenti
        return False, keyword_density, f"densità_keyword_bassa_{keyword_density:.2f}"
    
    # Criterio 3: Leggibilità
    readability_score = _calculate_readability_score(text)
    if readability_score < 0.5:
        return False, readability_score, f"leggibilità_bassa_{readability_score:.2f}"
    
    # Calcola score complessivo (media pesata)
    # Keyword density pesa 40%, readability 60%
    confidence_score = (keyword_density * 0.4) + (readability_score * 0.6)
    
    # Soglia finale: almeno 0.6 di confidenza
    is_reliable = confidence_score >= 0.6
    
    if is_reliable:
        reason = f"affidabile_density_{keyword_density:.2f}_readability_{readability_score:.2f}"
    else:
        reason = f"non_affidabile_score_{confidence_score:.2f}"
    
    return is_reliable, confidence_score, reason


def evaluate_extraction_result(text: str, method: str, metadata: dict) -> TextExtractionResult:
    """
    Valuta un risultato di estrazione e crea un TextExtractionResult
    
    Args:
        text: Testo estratto
        method: Metodo usato (pymupdf, pdfplumber, ocr)
        metadata: Metadati dell'estrazione
        
    Returns:
        TextExtractionResult con valutazione completa
    """
    if not text:
        return TextExtractionResult(
            text="",
            is_reliable=False,
            confidence_score=0.0,
            method=method,
            metadata=metadata,
            reason="nessun_testo_estratto"
        )
    
    is_reliable, confidence_score, reason = is_text_reliable(text)
    
    return TextExtractionResult(
        text=text,
        is_reliable=is_reliable,
        confidence_score=confidence_score,
        method=method,
        metadata=metadata,
        reason=reason
    )

