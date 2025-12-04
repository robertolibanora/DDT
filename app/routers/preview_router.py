"""
Router per il salvataggio delle correzioni dall'anteprima modal
L'anteprima è ora integrata come modal globale, questo router gestisce solo il salvataggio
"""
import os
import logging
import json
from pathlib import Path
from fastapi import APIRouter, Request, HTTPException, Depends, Form
from fastapi.responses import JSONResponse

from app.dependencies import require_authentication
from app.corrections import save_correction, get_file_hash
from app.excel import update_or_append_to_excel
from app.config import INBOX_DIR

# Directory temporanea per i file di anteprima
TEMP_PREVIEW_DIR = Path("temp/preview")
TEMP_PREVIEW_DIR.mkdir(parents=True, exist_ok=True)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/preview", tags=["preview"])


# La pagina /preview è stata rimossa - l'anteprima è ora integrata come modal globale
# L'estrazione avviene tramite /upload, manteniamo solo l'endpoint di salvataggio

@router.post("/save")
async def save_preview(
    request: Request,
    file_hash: str = Form(...),
    file_name: str = Form(...),
    data: str = Form(...),
    mittente: str = Form(...),
    destinatario: str = Form(...),
    numero_documento: str = Form(...),
    totale_kg: str = Form(...),
    original_data: str = Form(None),
    auth: bool = Depends(require_authentication)
):
    """
    Salva i dati corretti dall'anteprima e applica l'apprendimento
    """
    try:
        # Prepara i dati corretti - arrotonda a 3 decimali
        kg_value = float(totale_kg) if totale_kg else 0.0
        corrected_data = {
            "data": data,
            "mittente": mittente.strip(),
            "destinatario": destinatario.strip(),
            "numero_documento": numero_documento.strip(),
            "totale_kg": round(kg_value, 3)
        }
        
        # Carica i dati originali estratti dal form
        try:
            if original_data and original_data != "{}":
                original_data_parsed = json.loads(original_data)
            else:
                original_data_parsed = corrected_data
        except (json.JSONDecodeError, TypeError):
            logger.warning("Impossibile parsare original_data, uso corrected_data")
            original_data_parsed = corrected_data
        
        # Cerca il file originale nella cartella preview temp o inbox
        file_path = None
        
        # Prima cerca nella cartella preview temp
        preview_file = TEMP_PREVIEW_DIR / f"{file_hash}.pdf"
        if preview_file.exists():
            file_path = str(preview_file)
        else:
            # Cerca nella cartella inbox
            inbox_path = Path(INBOX_DIR)
            if inbox_path.exists():
                for pdf_file in inbox_path.glob("*.pdf"):
                    try:
                        if get_file_hash(str(pdf_file)) == file_hash or pdf_file.name == file_name:
                            file_path = str(pdf_file)
                            break
                    except:
                        continue
        
        # Se non trovato, usa un path virtuale basato su hash
        if not file_path:
            file_path = f"temp/preview/{file_hash}_{file_name}"
        
        # Salva la correzione per l'apprendimento
        correction_id = save_correction(file_path, original_data_parsed, corrected_data)
        
        # Salva o aggiorna nel file Excel (evita duplicati)
        was_updated = update_or_append_to_excel(corrected_data)
        action = "aggiornato" if was_updated else "salvato"
        
        # Rimuovi il file temporaneo dopo il salvataggio (se è nella cartella preview)
        preview_file = TEMP_PREVIEW_DIR / f"{file_hash}.pdf"
        if preview_file.exists():
            try:
                preview_file.unlink()
                logger.info(f"File temporaneo rimosso: {preview_file}")
            except Exception as e:
                logger.warning(f"Impossibile rimuovere file temporaneo: {e}")
        
        logger.info(f"Anteprima {action}: {correction_id} - DDT {corrected_data.get('numero_documento')}")
        
        return JSONResponse({
            "success": True,
            "message": f"DDT {action} con successo",
            "updated": was_updated,
            "correction_id": correction_id,
            "data": corrected_data
        })
        
    except ValueError as e:
        logger.error(f"Errore validazione durante salvataggio anteprima: {e}")
        raise HTTPException(status_code=422, detail=f"Dati non validi: {str(e)}")
    except Exception as e:
        logger.error(f"Errore durante salvataggio anteprima: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Errore durante il salvataggio: {str(e)}")



