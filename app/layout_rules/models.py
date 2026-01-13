"""
Modelli Pydantic per le regole di layout DDT
"""
from typing import Dict, Optional, List
from pydantic import BaseModel, Field, field_validator


class BoxCoordinates(BaseModel):
    """Coordinate di un box in percentuale rispetto alla pagina"""
    x_pct: float = Field(..., ge=0.0, le=1.0, description="Posizione X in percentuale (0.0-1.0)")
    y_pct: float = Field(..., ge=0.0, le=1.0, description="Posizione Y in percentuale (0.0-1.0)")
    w_pct: float = Field(..., ge=0.0, le=1.0, description="Larghezza in percentuale (0.0-1.0)")
    h_pct: float = Field(..., ge=0.0, le=1.0, description="Altezza in percentuale (0.0-1.0)")

    @field_validator('x_pct', 'y_pct', 'w_pct', 'h_pct')
    @classmethod
    def validate_percentage(cls, v: float) -> float:
        """Valida che i valori siano percentuali valide"""
        if not 0.0 <= v <= 1.0:
            raise ValueError(f"Valore percentuale deve essere tra 0.0 e 1.0, ricevuto: {v}")
        return round(v, 4)  # Arrotonda a 4 decimali per precisione


class FieldBox(BaseModel):
    """Box per un campo specifico"""
    page: int = Field(..., ge=1, description="Numero pagina (base 1)")
    box: BoxCoordinates = Field(..., description="Coordinate del box in percentuale")


class LayoutRuleMatch(BaseModel):
    """Criteri di match per una regola di layout"""
    supplier: str = Field(..., min_length=1, description="Nome del fornitore (normalizzato)")
    page_count: Optional[int] = Field(None, ge=1, description="Numero di pagine (opzionale)")


class LayoutRule(BaseModel):
    """Regola completa di layout per un DDT"""
    match: LayoutRuleMatch = Field(..., description="Criteri di match")
    fields: Dict[str, FieldBox] = Field(..., description="Mappatura campo -> box")

    @field_validator('fields')
    @classmethod
    def validate_fields(cls, v: Dict[str, FieldBox]) -> Dict[str, FieldBox]:
        """Valida che ci sia almeno un campo definito"""
        if not v:
            raise ValueError("Deve essere definito almeno un campo")
        
        # Valida che i nomi dei campi siano validi
        valid_fields = {'mittente', 'destinatario', 'data', 'numero_documento', 'totale_kg'}
        for field_name in v.keys():
            if field_name not in valid_fields:
                raise ValueError(f"Campo non valido: {field_name}. Campi validi: {valid_fields}")
        
        return v


class LayoutRulesFile(BaseModel):
    """Struttura completa del file layout_rules.json"""
    rules: Dict[str, LayoutRule] = Field(default_factory=dict, description="Regole di layout per nome")

    class Config:
        json_schema_extra = {
            "example": {
                "FIORITAL_layout_v1": {
                    "match": {
                        "supplier": "FIORITAL",
                        "page_count": 1
                    },
                    "fields": {
                        "numero_documento": {
                            "page": 1,
                            "box": {
                                "x_pct": 0.42,
                                "y_pct": 0.18,
                                "w_pct": 0.25,
                                "h_pct": 0.04
                            }
                        }
                    }
                }
            }
        }
