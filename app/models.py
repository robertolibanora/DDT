"""
Modelli Pydantic per validazione e normalizzazione dati DDT
"""
from datetime import datetime
from typing import Optional, List, Dict, Any
from pydantic import BaseModel, Field, field_validator, model_validator


class DDTData(BaseModel):
    """Modello per i dati estratti da un DDT"""
    data: str = Field(..., description="Data del DDT in formato YYYY-MM-DD")
    mittente: str = Field(..., min_length=1, description="Nome dell'azienda mittente")
    destinatario: str = Field(..., min_length=1, description="Nome dell'azienda destinataria")
    numero_documento: str = Field(..., min_length=1, description="Numero del documento DDT")
    totale_kg: float = Field(..., ge=0, description="Peso totale in kg (>= 0)")

    @field_validator('data')
    @classmethod
    def validate_date(cls, v: str) -> str:
        """Valida e normalizza la data"""
        if not v:
            raise ValueError("La data non può essere vuota")
        
        # Prova vari formati di data comuni
        date_formats = [
            '%Y-%m-%d',
            '%d/%m/%Y',
            '%d-%m-%Y',
            '%Y/%m/%d',
            '%d.%m.%Y',
        ]
        
        # Se è già nel formato corretto, restituiscilo
        try:
            datetime.strptime(v, '%Y-%m-%d')
            return v
        except ValueError:
            pass
        
        # Prova a parsare altri formati
        for fmt in date_formats:
            try:
                dt = datetime.strptime(v.strip(), fmt)
                return dt.strftime('%Y-%m-%d')
            except ValueError:
                continue
        
        raise ValueError(f"Formato data non valido: {v}. Atteso formato YYYY-MM-DD o varianti comuni")

    @field_validator('totale_kg', mode='before')
    @classmethod
    def normalize_kg(cls, v) -> float:
        """Normalizza il valore dei kg convertendo stringhe in float"""
        if isinstance(v, (int, float)):
            return float(v)
        if isinstance(v, str):
            # Rimuovi spazi e caratteri non numerici eccetto punto e virgola
            cleaned = v.strip().replace(',', '.').replace(' ', '')
            try:
                return float(cleaned)
            except ValueError:
                raise ValueError(f"Impossibile convertire '{v}' in numero per totale_kg")
        raise ValueError(f"Tipo non valido per totale_kg: {type(v)}")

    @field_validator('mittente', 'destinatario', 'numero_documento', mode='before')
    @classmethod
    def normalize_text(cls, v) -> str:
        """Normalizza i testi rimuovendo spazi extra"""
        if not v:
            return ""
        if not isinstance(v, str):
            v = str(v)
        # Rimuovi spazi multipli e trim
        normalized = ' '.join(v.strip().split())
        if not normalized:
            raise ValueError("Il campo non può essere vuoto dopo la normalizzazione")
        return normalized

    @model_validator(mode='after')
    def validate_consistency(self):
        """Validazioni aggiuntive di coerenza"""
        # Mittente e destinatario non possono essere uguali
        if self.mittente.lower() == self.destinatario.lower():
            raise ValueError("Mittente e destinatario non possono essere identici")
        
        return self

    class Config:
        json_schema_extra = {
            "example": {
                "data": "2024-11-27",
                "mittente": "ACME S.r.l.",
                "destinatario": "Mario Rossi & C.",
                "numero_documento": "DDT-12345",
                "totale_kg": 1250.5
            }
        }


class RuleOverride(BaseModel):
    """Modello per gli override di una regola"""
    totale_kg_mode: Optional[str] = Field(None, description="Modalità calcolo totale kg (es: 'sum_rows')")
    multipage: Optional[bool] = Field(None, description="Se il documento è multipagina")


class RuleData(BaseModel):
    """Modello per una regola completa"""
    detect: List[str] = Field(..., min_length=1, description="Lista di keyword per rilevare la regola")
    instructions: str = Field(..., min_length=1, description="Istruzioni specifiche per l'estrazione")
    overrides: Optional[Dict[str, Any]] = Field(default_factory=dict, description="Override per comportamenti speciali")
    
    @field_validator('detect')
    @classmethod
    def validate_detect(cls, v: List[str]) -> List[str]:
        """Valida che detect contenga almeno un elemento"""
        if not v or len(v) == 0:
            raise ValueError("La lista 'detect' deve contenere almeno un keyword")
        return [keyword.strip() for keyword in v if keyword.strip()]
    
    class Config:
        json_schema_extra = {
            "example": {
                "detect": ["DEVA", "Armanini"],
                "instructions": "Il totale non è presente. Calcola somma dei KG delle righe.",
                "overrides": {
                    "totale_kg_mode": "sum_rows",
                    "multipage": True
                }
            }
        }

