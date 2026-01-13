"""
Router per il salvataggio delle correzioni dall'anteprima modal
L'anteprima è ora integrata come modal globale, questo router gestisce solo il salvataggio
"""
import os
import logging
import json
from pathlib import Path
from fastapi import APIRouter, Request, HTTPException, Depends, Form
from fastapi.responses import JSONResponse, FileResponse

from app.dependencies import require_authentication
from app.corrections import save_correction, get_file_hash
from app.excel import update_or_append_to_excel
from app.config import INBOX_DIR
from app.watchdog_queue import get_all_items, mark_as_processed

# Directory temporanea per i file di anteprima
TEMP_PREVIEW_DIR = Path("temp/preview")
TEMP_PREVIEW_DIR.mkdir(parents=True, exist_ok=True)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/preview", tags=["preview"])


def generate_preview_png(pdf_path: str, file_hash: str) -> Path:
    """
    Genera una PNG di anteprima da un PDF e la salva in temp/preview
    Restituisce il path del file PNG generato
    """
    png_path = TEMP_PREVIEW_DIR / f"{file_hash}.png"
    
    # Se la PNG esiste già, restituiscila
    if png_path.exists():
        logger.debug(f"PNG già esistente: {png_path}")
        return png_path
    
    try:
        # Leggi il PDF
        with open(pdf_path, 'rb') as f:
            pdf_bytes = f.read()
        
        if len(pdf_bytes) == 0:
            raise ValueError("PDF vuoto")
        
        # Metodo 1: Prova con PyMuPDF (fitz) - migliore per Windows
        try:
            import fitz  # PyMuPDF
            
            logger.info(f"Generazione PNG con PyMuPDF da {pdf_path}")
            doc = fitz.open(stream=pdf_bytes, filetype="pdf")
            if len(doc) == 0:
                raise ValueError("PDF vuoto o non valido")
            
            # Converti la prima pagina in immagine
            page = doc[0]
            # Matrice di trasformazione per DPI 200 (200/72 = 2.78)
            zoom = 200 / 72.0
            mat = fitz.Matrix(zoom, zoom)
            pix = page.get_pixmap(matrix=mat)
            
            # Salva come PNG
            pix.save(str(png_path))
            doc.close()
            logger.info(f"PNG generata con PyMuPDF: {png_path} ({png_path.stat().st_size} bytes)")
            return png_path
            
        except ImportError:
            logger.warning("PyMuPDF non disponibile, provo con pdf2image...")
            # Metodo 2: Fallback a pdf2image
            try:
                from pdf2image import convert_from_bytes
                
                logger.info(f"Generazione PNG con pdf2image da {pdf_path}")
                images = convert_from_bytes(pdf_bytes, first_page=1, last_page=1, dpi=200)
                if not images:
                    raise ValueError("Impossibile convertire il PDF in immagine")
                
                images[0].save(str(png_path), 'PNG')
                logger.info(f"PNG generata con pdf2image: {png_path} ({png_path.stat().st_size} bytes)")
                return png_path
                
            except ImportError:
                error_msg = "Nessuna libreria disponibile per convertire PDF. Installa PyMuPDF (consigliato) o pdf2image+Poppler"
                logger.error(error_msg)
                raise ImportError(error_msg)
            except Exception as e:
                error_msg = f"Errore conversione PDF con pdf2image: {e}"
                logger.error(error_msg, exc_info=True)
                raise ValueError(error_msg) from e
                
        except Exception as e:
            logger.warning(f"Errore conversione PDF con PyMuPDF: {e}, provo fallback...")
            # Fallback a pdf2image se PyMuPDF fallisce
            try:
                from pdf2image import convert_from_bytes
                
                logger.info(f"Generazione PNG con pdf2image (fallback) da {pdf_path}")
                images = convert_from_bytes(pdf_bytes, first_page=1, last_page=1, dpi=200)
                if not images:
                    raise ValueError("Impossibile convertire il PDF in immagine")
                
                images[0].save(str(png_path), 'PNG')
                logger.info(f"PNG generata con pdf2image (fallback): {png_path} ({png_path.stat().st_size} bytes)")
                return png_path
            except Exception as e2:
                error_msg = f"Errore conversione PDF: PyMuPDF fallito ({e}), pdf2image fallito ({e2})"
                logger.error(error_msg, exc_info=True)
                raise ValueError(error_msg) from e2
                
    except Exception as e:
        logger.error(f"Errore generazione PNG: {e}", exc_info=True)
        raise


@router.get("/image/{file_hash}")
async def get_preview_image(
    file_hash: str,
    request: Request,
    auth: bool = Depends(require_authentication)
):
    """
    Endpoint per servire l'immagine PNG di anteprima del DDT
    Genera la PNG on-demand se non esiste già
    """
    try:
        png_path = TEMP_PREVIEW_DIR / f"{file_hash}.png"
        
        # Se la PNG non esiste, generala
        if not png_path.exists():
            # Cerca il PDF nella cartella inbox
            pdf_path = None
            inbox_path = Path(INBOX_DIR)
            
            if inbox_path.exists():
                for pdf_file in inbox_path.glob("*.pdf"):
                    try:
                        if get_file_hash(str(pdf_file)) == file_hash:
                            pdf_path = str(pdf_file)
                            break
                    except:
                        continue
            
            if not pdf_path:
                # Prova anche nella cartella temp/preview
                temp_pdf = TEMP_PREVIEW_DIR / f"{file_hash}.pdf"
                if temp_pdf.exists():
                    pdf_path = str(temp_pdf)
            
            if not pdf_path:
                logger.warning(f"PDF non trovato per hash {file_hash}")
                raise HTTPException(status_code=404, detail="File PDF non trovato")
            
            # Genera la PNG
            logger.info(f"Generazione PNG on-demand per hash {file_hash} da {pdf_path}")
            png_path = generate_preview_png(pdf_path, file_hash)
        
        # Verifica che il file esista
        if not png_path.exists():
            raise HTTPException(status_code=404, detail="Immagine di anteprima non trovata")
        
        # Restituisci la PNG con i header corretti
        return FileResponse(
            path=str(png_path),
            media_type="image/png",
            headers={
                "Content-Disposition": "inline",
                "Cache-Control": "no-store"
            }
        )
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Errore servizio immagine anteprima: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Errore durante la generazione dell'anteprima: {str(e)}")


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
    annotations: str = Form(None),
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
        
        # Parse annotazioni se presenti
        annotations_data = None
        if annotations:
            try:
                annotations_data = json.loads(annotations)
                logger.info(f"Annotazioni ricevute: {len(annotations_data)} campi annotati")
            except (json.JSONDecodeError, TypeError) as e:
                logger.warning(f"Errore parsing annotazioni: {e}")
        
        # Salva la correzione per l'apprendimento (con annotazioni)
        correction_id = save_correction(file_path, original_data_parsed, corrected_data, annotations=annotations_data)
        
        # Salva o aggiorna nel file Excel (evita duplicati)
        was_updated = update_or_append_to_excel(corrected_data)
        action = "aggiornato" if was_updated else "salvato"
        
        # Se questo file viene dalla coda watchdog, marcalo come processato
        try:
            queue_items = get_all_items()
            for item in queue_items:
                if item.get("file_hash") == file_hash and not item.get("processed", False):
                    mark_as_processed(item.get("id"))
                    logger.info(f"Elemento coda watchdog marcato come processato: {item.get('id')}")
                    break
        except Exception as e:
            logger.warning(f"Errore marcatura elemento coda watchdog: {e}")
        
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



