"""
Utility functions per normalizzazione e validazione
"""
import re
from datetime import datetime
from typing import Optional


def normalize_date(date_str: str) -> Optional[str]:
    """
    Normalizza una stringa data in formato YYYY-MM-DD
    Supporta vari formati italiani comuni
    """
    if not date_str or not isinstance(date_str, str):
        return None
    
    date_str = date_str.strip()
    
    # Formati comuni da provare
    formats = [
        '%Y-%m-%d',
        '%d/%m/%Y',
        '%d-%m-%Y',
        '%Y/%m/%d',
        '%d.%m.%Y',
        '%d/%m/%y',  # formato breve anno
        '%d-%m-%y',
    ]
    
    # Se è già nel formato corretto
    try:
        datetime.strptime(date_str, '%Y-%m-%d')
        return date_str
    except ValueError:
        pass
    
    # Prova altri formati
    for fmt in formats:
        try:
            dt = datetime.strptime(date_str, fmt)
            return dt.strftime('%Y-%m-%d')
        except ValueError:
            continue
    
    return None


def normalize_float(value) -> Optional[float]:
    """Normalizza un valore in float, gestendo stringhe con virgola/ punto"""
    if value is None:
        return None
    
    if isinstance(value, (int, float)):
        return float(value)
    
    if isinstance(value, str):
        # Rimuovi spazi e converti virgola in punto
        cleaned = value.strip().replace(',', '.').replace(' ', '').replace('kg', '').replace('Kg', '')
        # Rimuovi tutti i caratteri non numerici tranne punto e segno meno
        cleaned = re.sub(r'[^\d\.\-]', '', cleaned)
        
        try:
            return float(cleaned) if cleaned else None
        except ValueError:
            return None
    
    return None


def normalize_text(text: str) -> str:
    """Normalizza un testo rimuovendo spazi extra e caratteri invisibili"""
    if not text or not isinstance(text, str):
        return ""
    
    # Rimuovi caratteri invisibili comuni
    text = text.replace('\xa0', ' ').replace('\u200b', '')
    # Normalizza spazi
    text = ' '.join(text.split())
    return text.strip()


def clean_company_name(name: str) -> str:
    """Pulisce e normalizza il nome di un'azienda"""
    if not name:
        return ""
    
    name = normalize_text(name)
    
    # Rimuovi prefissi comuni che potrebbero confondere
    prefixes_to_remove = [
        r'^(Spett\.le|Spettabile|Spett\s+\.?\s*le)\s+',
        r'^(A|Da|Per|Consegna a|Cantiere|Cliente|Destinatario|Mittente)[:\s]+',
    ]
    
    for prefix in prefixes_to_remove:
        name = re.sub(prefix, '', name, flags=re.IGNORECASE)
    
    return normalize_text(name)

