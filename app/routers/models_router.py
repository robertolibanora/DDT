"""
Router per la gestione dei modelli di layout DDT
Mostra tutti i modelli salvati tramite il layout trainer
"""
import logging
from fastapi import APIRouter, Request, HTTPException, Depends
from fastapi.responses import JSONResponse
from typing import List, Dict, Any

from app.dependencies import require_authentication
from app.layout_rules.manager import (
    get_all_layout_rules,
    delete_layout_rule,
    load_layout_rules
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/models", tags=["models"])


@router.get("")
async def get_models(
    request: Request,
    auth: bool = Depends(require_authentication)
):
    """
    Restituisce tutti i modelli di layout salvati
    Formattati per la visualizzazione nella pagina Modelli
    """
    try:
        rules = get_all_layout_rules()
        
        # Trasforma le regole in modelli per la visualizzazione
        models = []
        for rule_name, rule_data in rules.items():
            supplier = rule_data.get('match', {}).get('supplier', 'Sconosciuto')
            fields = rule_data.get('fields', {})
            page_count = rule_data.get('match', {}).get('page_count')
            
            # Conta i campi definiti
            fields_count = len(fields)
            fields_list = list(fields.keys())
            
            model = {
                'id': rule_name,
                'name': supplier,
                'rule_name': rule_name,
                'fields_count': fields_count,
                'fields': fields_list,
                'page_count': page_count,
                'status': 'ATTIVO',
                'fields_data': fields  # Per l'anteprima
            }
            models.append(model)
        
        # Ordina per nome mittente
        models.sort(key=lambda x: x['name'].upper())
        
        return JSONResponse({
            "success": True,
            "models": models,
            "total": len(models)
        })
    except Exception as e:
        logger.error(f"Errore lettura modelli: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Errore durante la lettura dei modelli: {str(e)}")


@router.delete("/{model_id}")
async def delete_model(
    model_id: str,
    request: Request,
    auth: bool = Depends(require_authentication)
):
    """
    Elimina un modello di layout
    
    Args:
        model_id: ID del modello (rule_name)
    """
    try:
        deleted = delete_layout_rule(model_id)
        
        if not deleted:
            raise HTTPException(status_code=404, detail=f"Modello '{model_id}' non trovato")
        
        logger.info(f"🗑️ Modello eliminato: {model_id}")
        
        return JSONResponse({
            "success": True,
            "message": f"Modello '{model_id}' eliminato con successo"
        })
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Errore eliminazione modello: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Errore durante l'eliminazione: {str(e)}")


@router.get("/{model_id}")
async def get_model(
    model_id: str,
    request: Request,
    auth: bool = Depends(require_authentication)
):
    """
    Restituisce i dettagli di un singolo modello
    
    Args:
        model_id: ID del modello (rule_name)
    """
    try:
        rules = load_layout_rules()
        
        if model_id not in rules:
            raise HTTPException(status_code=404, detail=f"Modello '{model_id}' non trovato")
        
        rule = rules[model_id]
        rule_data = rule.model_dump()
        
        supplier = rule_data.get('match', {}).get('supplier', 'Sconosciuto')
        fields = rule_data.get('fields', {})
        page_count = rule_data.get('match', {}).get('page_count')
        
        model = {
            'id': model_id,
            'name': supplier,
            'rule_name': model_id,
            'fields_count': len(fields),
            'fields': list(fields.keys()),
            'page_count': page_count,
            'status': 'ATTIVO',
            'fields_data': fields,
            'full_data': rule_data
        }
        
        return JSONResponse({
            "success": True,
            "model": model
        })
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Errore lettura modello: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Errore durante la lettura del modello: {str(e)}")
