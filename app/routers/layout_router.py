"""
Router per la gestione delle regole di layout DDT
Permette di salvare, caricare e gestire le regole di layout grafiche
"""
import logging
from fastapi import APIRouter, Request, HTTPException, Depends, Form
from fastapi.responses import JSONResponse
from typing import Optional, Dict, Any

from app.dependencies import require_authentication
from app.layout_rules.manager import (
    save_layout_rule,
    get_all_layout_rules,
    delete_layout_rule,
    match_layout_rule,
    load_layout_rules
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/layout", tags=["layout"])


@router.get("/rules")
async def get_layout_rules(
    request: Request,
    auth: bool = Depends(require_authentication)
):
    """
    Restituisce tutte le regole di layout salvate
    """
    try:
        rules = get_all_layout_rules()
        return JSONResponse({
            "success": True,
            "rules": rules
        })
    except Exception as e:
        logger.error(f"Errore lettura layout rules: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Errore durante la lettura delle regole: {str(e)}")


@router.post("/rules/save")
async def save_layout_rule_endpoint(
    request: Request,
    rule_name: str = Form(...),
    supplier: str = Form(...),
    page_count: Optional[int] = Form(None),
    fields: str = Form(...),  # JSON string con i campi
    auth: bool = Depends(require_authentication)
):
    """
    Salva una nuova regola di layout
    
    Args:
        rule_name: Nome della regola (es: "FIORITAL_layout_v1")
        supplier: Nome del fornitore
        page_count: Numero di pagine (opzionale)
        fields: JSON string con struttura {campo: {page: int, box: {x_pct, y_pct, w_pct, h_pct}}}
    """
    try:
        import json
        
        # Parse dei campi
        try:
            fields_data = json.loads(fields)
        except json.JSONDecodeError as e:
            raise HTTPException(status_code=400, detail=f"Formato JSON non valido per fields: {e}")
        
        # Valida che ci sia almeno un campo
        if not fields_data:
            raise HTTPException(status_code=400, detail="Deve essere definito almeno un campo")
        
        # Normalizza il supplier per il salvataggio (mantieni originale ma assicura consistenza)
        supplier_clean = supplier.strip() if supplier else ""
        if not supplier_clean:
            raise HTTPException(status_code=400, detail="Il nome del fornitore non può essere vuoto")
        
        # Salva la regola
        saved_name = save_layout_rule(rule_name, supplier_clean, page_count, fields_data)
        
        logger.info(f"💾 Layout model saved successfully: {saved_name} for sender: '{supplier_clean}'")
        
        return JSONResponse({
            "success": True,
            "message": f"Regola di layout '{saved_name}' salvata con successo",
            "rule_name": saved_name
        })
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Errore salvataggio layout rule: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Errore durante il salvataggio: {str(e)}")


@router.delete("/rules/{rule_name}")
async def delete_layout_rule_endpoint(
    rule_name: str,
    request: Request,
    auth: bool = Depends(require_authentication)
):
    """
    Elimina una regola di layout
    """
    try:
        deleted = delete_layout_rule(rule_name)
        
        if not deleted:
            raise HTTPException(status_code=404, detail=f"Regola '{rule_name}' non trovata")
        
        return JSONResponse({
            "success": True,
            "message": f"Regola '{rule_name}' eliminata con successo"
        })
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Errore eliminazione layout rule: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Errore durante l'eliminazione: {str(e)}")


@router.get("/match")
async def match_layout_rule_endpoint(
    request: Request,
    supplier: str,
    page_count: Optional[int] = None,
    auth: bool = Depends(require_authentication)
):
    """
    Trova una regola di layout che corrisponde ai criteri
    
    Args:
        supplier: Nome del fornitore
        page_count: Numero di pagine (opzionale)
    """
    try:
        rule = match_layout_rule(supplier, page_count)
        
        if rule:
            return JSONResponse({
                "success": True,
                "matched": True,
                "rule": rule.model_dump()
            })
        else:
            return JSONResponse({
                "success": True,
                "matched": False,
                "rule": None
            })
    except Exception as e:
        logger.error(f"Errore match layout rule: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Errore durante il matching: {str(e)}")
