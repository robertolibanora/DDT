"""
Router FastAPI per gestione regole dinamiche
"""
import logging
from fastapi import APIRouter, HTTPException
from typing import Dict, Any, List
from pydantic import BaseModel

from app.rules.rules import (
    get_all_rules,
    get_rule,
    add_rule,
    delete_rule,
    reload_rules
)
from app.models import RuleData

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/rules", tags=["rules"])


class RuleResponse(BaseModel):
    """Risposta con tutte le regole"""
    rules: Dict[str, Any]


class RuleCreateRequest(BaseModel):
    """Richiesta per creare/aggiornare una regola"""
    name: str
    rule: RuleData


class RuleDeleteResponse(BaseModel):
    """Risposta per eliminazione regola"""
    success: bool
    message: str


@router.get("", response_model=RuleResponse)
async def list_rules():
    """
    Ottiene tutte le regole disponibili
    """
    try:
        rules = get_all_rules()
        return {"rules": rules}
    except Exception as e:
        logger.error(f"Errore lettura regole: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Errore durante la lettura delle regole: {str(e)}")


@router.get("/{name}")
async def get_rule_by_name(name: str):
    """
    Ottiene una regola specifica per nome
    """
    try:
        rule = get_rule(name)
        if not rule:
            raise HTTPException(status_code=404, detail=f"Regola '{name}' non trovata")
        return {"name": name, "rule": rule}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Errore lettura regola '{name}': {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Errore durante la lettura della regola: {str(e)}")


@router.post("/add")
async def create_or_update_rule(request: RuleCreateRequest):
    """
    Crea o aggiorna una regola
    """
    try:
        # Valida i dati usando Pydantic
        rule_dict = request.rule.model_dump()
        
        # Salva la regola
        add_rule(request.name, rule_dict)
        
        # Ricarica le regole per applicarle immediatamente
        reload_rules()
        
        logger.info(f"Regola '{request.name}' creata/aggiornata con successo")
        return {
            "success": True,
            "message": f"Regola '{request.name}' salvata con successo",
            "rule": rule_dict
        }
    except Exception as e:
        logger.error(f"Errore salvataggio regola '{request.name}': {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Errore durante il salvataggio della regola: {str(e)}")


@router.put("/{name}")
async def update_rule(name: str, rule: RuleData):
    """
    Aggiorna una regola esistente
    """
    try:
        # Verifica che la regola esista
        existing = get_rule(name)
        if not existing:
            raise HTTPException(status_code=404, detail=f"Regola '{name}' non trovata")
        
        # Aggiorna la regola
        rule_dict = rule.model_dump()
        add_rule(name, rule_dict)
        
        # Ricarica le regole
        reload_rules()
        
        logger.info(f"Regola '{name}' aggiornata con successo")
        return {
            "success": True,
            "message": f"Regola '{name}' aggiornata con successo",
            "rule": rule_dict
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Errore aggiornamento regola '{name}': {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Errore durante l'aggiornamento della regola: {str(e)}")


@router.delete("/{name}", response_model=RuleDeleteResponse)
async def remove_rule(name: str):
    """
    Elimina una regola
    """
    try:
        deleted = delete_rule(name)
        if not deleted:
            raise HTTPException(status_code=404, detail=f"Regola '{name}' non trovata")
        
        # Ricarica le regole
        reload_rules()
        
        return {
            "success": True,
            "message": f"Regola '{name}' eliminata con successo"
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Errore eliminazione regola '{name}': {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Errore durante l'eliminazione della regola: {str(e)}")


@router.post("/reload")
async def reload_rules_endpoint():
    """
    Ricarica le regole dal file (utile dopo modifiche manuali)
    """
    try:
        reload_rules()
        return {
            "success": True,
            "message": "Regole ricaricate con successo"
        }
    except Exception as e:
        logger.error(f"Errore ricaricamento regole: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Errore durante il ricaricamento delle regole: {str(e)}")

