"""
Router FastAPI per reprocessing DDT
"""
import os
import logging
from pathlib import Path
from fastapi import APIRouter, HTTPException, Depends, Request, Body
from typing import Dict, Any, Optional
from pydantic import BaseModel

from app.extract import extract_from_pdf
from app.excel import read_excel_as_dict, update_or_append_to_excel
from app.config import INBOX_DIR
from app.dependencies import require_authentication

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/reprocess", tags=["reprocess"])


class ReprocessRequest(BaseModel):
    """Richiesta per reprocessing"""
    numero_documento: Optional[str] = None
    file_path: Optional[str] = None  # Opzionale: percorso file PDF


class ReprocessResponse(BaseModel):
    """Risposta per reprocessing"""
    success: bool
    message: str
    data: Dict[str, Any] = None


@router.post("/{numero_documento}")
async def reprocess_ddt(numero_documento: str, http_request: Request, request_data: Optional[ReprocessRequest] = Body(None), auth: bool = Depends(require_authentication)):
    """
    Riprocessa un DDT specifico usando le regole aggiornate
    
    Args:
        numero_documento: Numero del documento da riprocessare
        request_data: Opzionale, può contenere file_path personalizzato
    """
    try:
        # Cerca il file PDF
        pdf_path = None
        
        # Se è fornito un percorso personalizzato nel body della richiesta
        if request_data and hasattr(request_data, 'file_path') and request_data.file_path and os.path.exists(request_data.file_path):
            pdf_path = request_data.file_path
        else:
            # Cerca nella cartella inbox
            from app.paths import get_inbox_dir
            inbox_path = get_inbox_dir()
            if inbox_path.exists():
                for pdf_file in inbox_path.glob("*.pdf"):
                    # Prova a estrarre il numero documento dal nome file o dal contenuto
                    # Per semplicità, cerchiamo tutti i PDF e processiamo quello corrispondente
                    try:
                        # Estrai dati per verificare il numero documento
                        temp_data = extract_from_pdf(str(pdf_file))
                        if temp_data.get("numero_documento") == numero_documento:
                            pdf_path = str(pdf_file)
                            break
                    except Exception as e:
                        logger.debug(f"Errore verifica file {pdf_file}: {e}")
                        continue
        
        if not pdf_path or not os.path.exists(pdf_path):
            raise HTTPException(
                status_code=404,
                detail=f"File PDF per DDT '{numero_documento}' non trovato. Fornisci il percorso del file."
            )
        
        logger.info(f"Riprocessamento DDT '{numero_documento}' da file: {pdf_path}")
        
        # Estrai i dati con le regole aggiornate
        try:
            extracted_data = extract_from_pdf(pdf_path)
            
            # Verifica che il numero documento corrisponda
            if extracted_data.get("numero_documento") != numero_documento:
                logger.warning(
                    f"Numero documento estratto '{extracted_data.get('numero_documento')}' "
                    f"differisce da quello richiesto '{numero_documento}'"
                )
            
            # Aggiorna il file Excel (sovrascrive la riga esistente se presente, altrimenti aggiunge)
            was_updated = update_or_append_to_excel(extracted_data)
            action = "aggiornato" if was_updated else "aggiunto"
            
            # FINALIZZA il documento nel sistema di tracking
            try:
                from app.processed_documents import calculate_file_hash, mark_document_finalized
                doc_hash = calculate_file_hash(pdf_path)
                mark_document_finalized(doc_hash)
                logger.info(f"✅ Documento FINALIZED dopo reprocess: hash={doc_hash[:16]}... numero={numero_documento}")
            except Exception as e:
                logger.warning(f"Errore finalizzazione documento dopo reprocess: {e}")
            
            logger.info(f"DDT '{numero_documento}' riprocessato con successo ({action})")
            
            return {
                "success": True,
                "message": f"DDT '{numero_documento}' riprocessato con successo ({action})",
                "data": extracted_data,
                "updated": was_updated
            }
            
        except (OSError, IOError, PermissionError) as e:
            # Errori di I/O su path critici: solleva HTTPException 500 esplicito
            logger.error("Errore I/O durante reprocessing: %s", str(e), exc_info=True)
            raise HTTPException(
                status_code=500,
                detail=f"Errore accesso directory Excel: {str(e)}. Verifica i permessi di scrittura su /var/www/DDT/excel"
            )
        except ValueError as e:
            logger.error("Errore validazione durante reprocessing: %s", str(e))
            raise HTTPException(status_code=422, detail=f"Dati estratti non validi: {str(e)}")
        except Exception as e:
            logger.error("Errore durante reprocessing: %s", str(e), exc_info=True)
            raise HTTPException(status_code=500, detail=f"Errore durante il reprocessing: {str(e)}")
            
    except HTTPException:
        raise
    except (OSError, IOError, PermissionError) as e:
        # Errori di I/O su path critici: solleva HTTPException 500 esplicito
        logger.error("Errore I/O durante reprocessing: %s", str(e), exc_info=True)
        raise HTTPException(
            status_code=500,
            detail=f"Errore accesso directory Excel: {str(e)}. Verifica i permessi di scrittura su /var/www/DDT/excel"
        )
    except Exception as e:
        logger.error("Errore generico durante reprocessing: %s", str(e), exc_info=True)
        raise HTTPException(status_code=500, detail=f"Errore durante il reprocessing: {str(e)}")


@router.post("/by-file")
async def reprocess_by_file(http_request: Request, request_data: Dict[str, Any], auth: bool = Depends(require_authentication)):
    """
    Riprocessa un DDT fornendo direttamente il percorso del file PDF
    
    Args:
        request_data: Dizionario con chiave 'file_path' contenente il percorso del file PDF
    """
    file_path = request_data.get('file_path')
    if not file_path:
        raise HTTPException(status_code=400, detail="Parametro 'file_path' mancante")
    
    if not os.path.exists(file_path):
        raise HTTPException(status_code=404, detail=f"File non trovato: {file_path}")
    
    if not file_path.lower().endswith('.pdf'):
        raise HTTPException(status_code=400, detail="Il file deve essere un PDF")
    
    try:
        logger.info(f"Riprocessamento DDT da file: {file_path}")
        
        # Estrai i dati con le regole aggiornate
        extracted_data = extract_from_pdf(file_path)
        numero_documento = extracted_data.get("numero_documento", "N/A")
        
        # Aggiorna il file Excel (sovrascrive la riga esistente se presente, altrimenti aggiunge)
        was_updated = update_or_append_to_excel(extracted_data)
        action = "aggiornato" if was_updated else "aggiunto"
        
        # FINALIZZA il documento nel sistema di tracking
        try:
            from app.processed_documents import calculate_file_hash, mark_document_finalized
            doc_hash = calculate_file_hash(file_path)
            mark_document_finalized(doc_hash)
            logger.info(f"✅ Documento FINALIZED dopo reprocess by-file: hash={doc_hash[:16]}... numero={numero_documento}")
        except Exception as e:
            logger.warning(f"Errore finalizzazione documento dopo reprocess by-file: {e}")
        
        logger.info("DDT '%s' riprocessato con successo (%s)", numero_documento, action)
        
        return {
            "success": True,
            "message": f"DDT '{numero_documento}' riprocessato con successo ({action})",
            "data": extracted_data,
            "updated": was_updated
        }
        
    except (OSError, IOError, PermissionError) as e:
        # Errori di I/O su path critici: solleva HTTPException 500 esplicito
        logger.error("Errore I/O durante reprocessing by-file: %s", str(e), exc_info=True)
        raise HTTPException(
            status_code=500,
            detail=f"Errore accesso directory Excel: {str(e)}. Verifica i permessi di scrittura su /var/www/DDT/excel"
        )
    except ValueError as e:
        logger.error("Errore validazione durante reprocessing: %s", str(e))
        raise HTTPException(status_code=422, detail=f"Dati estratti non validi: {str(e)}")
    except Exception as e:
        logger.error("Errore durante reprocessing: %s", str(e), exc_info=True)
        raise HTTPException(status_code=500, detail=f"Errore durante il reprocessing: {str(e)}")

