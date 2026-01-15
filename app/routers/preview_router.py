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
from app.extract import generate_preview_png
from app.layout_rules.manager import get_all_layout_rules, match_layout_rule, load_layout_rules

from app.paths import get_preview_dir
# Directory temporanea per i file di anteprima (path assoluto)
TEMP_PREVIEW_DIR = get_preview_dir()

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
        from app.paths import safe_open
        pdf_path_obj = Path(pdf_path)
        if not pdf_path_obj.is_absolute():
            from app.paths import get_base_dir
            pdf_path_obj = get_base_dir() / pdf_path_obj
        pdf_path_obj = pdf_path_obj.resolve()
        
        with safe_open(pdf_path_obj, 'rb') as f:
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
            from app.paths import get_inbox_dir
            inbox_path = get_inbox_dir()
            
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
                # Cambiato da WARNING a DEBUG: 404 è normale se file non esiste
                logger.debug(f"PDF non trovato per hash {file_hash[:16]}... (404 normale)")
                raise HTTPException(status_code=404, detail="File PDF non trovato")
            
            # Genera la PNG
            logger.debug(f"Generazione PNG on-demand per hash {file_hash[:16]}... da {Path(pdf_path).name}")
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

@router.get("/image/{file_hash}")
async def get_preview_image(
    file_hash: str,
    request: Request,
    auth: bool = Depends(require_authentication)
):
    """
    Serve l'immagine PNG di anteprima del DDT
    
    Args:
        file_hash: Hash del file PDF
        
    Returns:
        FileResponse con la PNG o 404 se non trovata
    """
    try:
        png_path = TEMP_PREVIEW_DIR / f"{file_hash}.png"
        
        # Se la PNG non esiste, prova a generarla dal PDF
        if not png_path.exists():
            logger.debug(f"PNG anteprima non trovata per hash {file_hash[:16]}..., provo a generarla...")
            
            # Cerca il PDF nella cartella inbox
            from app.paths import get_inbox_dir
            inbox_path = get_inbox_dir()
            pdf_file = None
            
            if inbox_path.exists():
                for pdf_path in inbox_path.glob("*.pdf"):
                    try:
                        if get_file_hash(str(pdf_path)) == file_hash:
                            pdf_file = pdf_path
                            break
                    except Exception:
                        continue
            
            if pdf_file and pdf_file.exists():
                # Genera la PNG
                generated_path = generate_preview_png(str(pdf_file), file_hash, str(TEMP_PREVIEW_DIR))
                if generated_path:
                    png_path = Path(generated_path)
                else:
                    logger.debug(f"Impossibile generare PNG per {file_hash[:16]}... (404 normale)")
                    raise HTTPException(status_code=404, detail="Anteprima non disponibile")
            else:
                # Cambiato da WARNING a DEBUG: 404 è normale se file non esiste
                logger.debug(f"PDF non trovato per hash {file_hash[:16]}... (404 normale)")
                raise HTTPException(status_code=404, detail="File PDF non trovato")
        
        if not png_path.exists():
            raise HTTPException(status_code=404, detail="Anteprima non trovata")
        
        # Restituisci la PNG con headers corretti
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
        logger.error(f"Errore servizio PNG anteprima: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Errore durante il caricamento dell'anteprima: {str(e)}")


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
    data_inserimento: str = Form(None),  # DEPRECATO: ignorato, viene sempre usata la data globale dalla configurazione
    original_data: str = Form(None),
    annotations: str = Form(None),
    auth: bool = Depends(require_authentication)
):
    """
    Salva i dati corretti dall'anteprima, finalizza il documento e applica l'apprendimento
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
        
        # IMPORTANTE: Usa sempre la data globale dalla configurazione
        # Ignora data_inserimento passata dal form (mantenuta per retrocompatibilità)
        from app.global_config import get_active_output_date
        try:
            data_inserimento = get_active_output_date()
            logger.info(f"📅 [PREVIEW] Usata data output globale: {data_inserimento}")
        except Exception as e:
            logger.error(f"Errore lettura data output globale: {e}", exc_info=True)
            # Fallback: usa quella passata dal form se disponibile
            if not data_inserimento or not data_inserimento.strip():
                raise HTTPException(status_code=500, detail="Impossibile recuperare la data di destinazione. Verifica la configurazione nella dashboard.")
            data_inserimento = data_inserimento.strip()
            logger.warning(f"⚠️ [PREVIEW] Fallback a data_inserimento dal form: {data_inserimento}")
        
        # Cerca il file originale nella cartella inbox (priorità)
        file_path = None
        from app.paths import get_inbox_dir
        inbox_path = get_inbox_dir()
        
        if inbox_path.exists():
            for pdf_file in inbox_path.glob("*.pdf"):
                try:
                    if get_file_hash(str(pdf_file)) == file_hash or pdf_file.name == file_name:
                        file_path = str(pdf_file)
                        break
                except:
                    continue
        
        # Fallback: cerca nella cartella preview temp
        if not file_path:
            preview_file = TEMP_PREVIEW_DIR / f"{file_hash}.pdf"
            if preview_file.exists():
                file_path = str(preview_file)
        
        # Se non trovato, usa un path virtuale basato su hash (per correzioni senza file)
        if not file_path:
            file_path = str(TEMP_PREVIEW_DIR / f"{file_hash}_{file_name}")
        
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
        
        # FINALIZZAZIONE: rinomina, sposta e archivia il documento
        final_path = None
        finalization_error = None
        
        # Verifica che il file sia in inbox (necessario per finalizzazione)
        from app.paths import get_inbox_dir
        inbox_path_obj = get_inbox_dir()
        file_path_obj = Path(file_path) if file_path else None
        
        if file_path_obj and file_path_obj.exists():
            file_path_obj = file_path_obj.resolve()
            if str(file_path_obj).startswith(str(inbox_path_obj.resolve())):
                try:
                    from app.finalization import finalize_document
                    from app.processed_documents import calculate_file_hash
                    
                    # Verifica hash corrispondenza
                    actual_hash = calculate_file_hash(str(file_path_obj))
                    if actual_hash != file_hash:
                        logger.warning(f"⚠️ Hash mismatch: atteso {file_hash[:16]}..., trovato {actual_hash[:16]}...")
                    
                    # Finalizza il documento
                    success, final_path, error_msg = finalize_document(
                        file_path=str(file_path_obj),
                        doc_hash=file_hash,
                        data_inserimento=data_inserimento,
                        mittente=corrected_data["mittente"],
                        destinatario=corrected_data["destinatario"],
                        numero_documento=corrected_data["numero_documento"]
                    )
                    
                    if success:
                        logger.info(f"✅ Documento finalizzato: {final_path}")
                    else:
                        finalization_error = error_msg
                        logger.error(f"❌ Errore finalizzazione: {error_msg}")
                        
                except Exception as e:
                    finalization_error = str(e)
                    logger.error(f"❌ Errore durante finalizzazione: {e}", exc_info=True)
            else:
                logger.warning(f"⚠️ File non in inbox, finalizzazione saltata: {file_path}")
        else:
            logger.warning(f"⚠️ File non trovato o non accessibile, finalizzazione saltata: {file_path}")
        
        # FINALIZZA il documento nel sistema di tracking (con data_inserimento)
        try:
            from app.processed_documents import mark_document_finalized
            mark_document_finalized(file_hash, data_inserimento=data_inserimento)
            logger.info(f"✅ Documento FINALIZED nel tracking: hash={file_hash[:16]}... data_inserimento={data_inserimento}")
        except Exception as e:
            logger.warning(f"Errore finalizzazione tracking: {e}")
        
        # Se questo file viene dalla coda watchdog, marcalo come processato
        try:
            queue_items = get_all_items()
            for item in queue_items:
                if item.get("file_hash") == file_hash and not item.get("processed", False):
                    mark_as_processed(item.get("id"))
                    logger.debug(f"Elemento coda watchdog marcato come processato: {item.get('id')}")
                    break
        except Exception as e:
            logger.debug(f"Errore marcatura elemento coda watchdog: {e}")
        
        # Rimuovi il file temporaneo dopo il salvataggio (se è nella cartella preview)
        preview_file = TEMP_PREVIEW_DIR / f"{file_hash}.pdf"
        if preview_file.exists():
            try:
                preview_file.unlink()
                logger.info(f"File temporaneo rimosso: {preview_file}")
            except Exception as e:
                logger.warning(f"Impossibile rimuovere file temporaneo: {e}")
        
        logger.info(f"Anteprima {action}: {correction_id} - DDT {corrected_data.get('numero_documento')}")
        
        # Prepara risposta
        response_data = {
            "success": True,
            "message": f"DDT {action} con successo",
            "updated": was_updated,
            "correction_id": correction_id,
            "data": corrected_data,
            "finalized": final_path is not None,
            "final_path": final_path
        }
        
        # Se c'è stato un errore di finalizzazione, aggiungilo alla risposta
        if finalization_error:
            response_data["finalization_error"] = finalization_error
            response_data["message"] = f"DDT {action} con successo, ma errore durante finalizzazione: {finalization_error}"
        
        return JSONResponse(response_data)
        
    except ValueError as e:
        logger.error(f"Errore validazione durante salvataggio anteprima: {e}")
        raise HTTPException(status_code=422, detail=f"Dati non validi: {str(e)}")
    except Exception as e:
        logger.error(f"Errore durante salvataggio anteprima: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Errore durante il salvataggio: {str(e)}")


@router.get("/detect-model")
async def detect_model(
    request: Request,
    mittente: str,
    page_count: int = None,
    auth: bool = Depends(require_authentication)
):
    """
    Cerca automaticamente un modello di layout basato sul mittente estratto
    
    Args:
        mittente: Nome del mittente estratto dal documento
        page_count: Numero di pagine del documento (opzionale)
    """
    try:
        if not mittente or not mittente.strip():
            return JSONResponse({
                "success": True,
                "matched": False,
                "model": None,
                "available_models": []
            })
        
        # Cerca modello automatico
        layout_rule = match_layout_rule(mittente.strip(), page_count)
        
        matched_model = None
        if layout_rule:
            # Ottieni tutti i modelli per mostrare anche quelli disponibili
            all_rules = load_layout_rules()
            for rule_name, rule in all_rules.items():
                if rule == layout_rule:
                    matched_model = {
                        "id": rule_name,
                        "name": rule.match.supplier,
                        "rule_name": rule_name,
                        "fields_count": len(rule.fields),
                        "fields": list(rule.fields.keys())
                    }
                    break
        
        # Ottieni lista di tutti i modelli disponibili
        all_models = []
        all_rules = get_all_layout_rules()
        for rule_name, rule_data in all_rules.items():
            supplier = rule_data.get('match', {}).get('supplier', 'Sconosciuto')
            fields = rule_data.get('fields', {})
            all_models.append({
                "id": rule_name,
                "name": supplier,
                "rule_name": rule_name,
                "fields_count": len(fields),
                "fields": list(fields.keys())
            })
        
        # Ordina per nome
        all_models.sort(key=lambda x: x['name'].upper())
        
        return JSONResponse({
            "success": True,
            "matched": matched_model is not None,
            "model": matched_model,
            "available_models": all_models
        })
    except Exception as e:
        logger.error(f"Errore rilevamento modello: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Errore durante il rilevamento: {str(e)}")


@router.post("/apply-model")
async def apply_model(
    request: Request,
    file_hash: str = Form(...),
    model_id: str = Form(...),
    auth: bool = Depends(require_authentication)
):
    """
    Applica un modello di layout specifico per riprocessare il documento
    
    Args:
        file_hash: Hash del file PDF
        model_id: ID del modello da applicare (rule_name)
    """
    try:
        from app.extract import extract_from_pdf
        from pathlib import Path
        
        # Trova il file PDF
        file_path = None
        preview_file = TEMP_PREVIEW_DIR / f"{file_hash}.pdf"
        if preview_file.exists():
            file_path = str(preview_file)
        else:
            from app.paths import get_inbox_dir
            inbox_path = get_inbox_dir()
            if inbox_path.exists():
                for pdf_file in inbox_path.glob("*.pdf"):
                    try:
                        if get_file_hash(str(pdf_file)) == file_hash:
                            file_path = str(pdf_file)
                            break
                    except:
                        continue
        
        if not file_path or not Path(file_path).exists():
            raise HTTPException(status_code=404, detail="File PDF non trovato")
        
        # Verifica che il documento non sia già finalizzato
        from app.processed_documents import get_document_status, DocumentStatus, is_document_finalized
        if is_document_finalized(file_hash):
            raise HTTPException(
                status_code=400, 
                detail="Impossibile applicare template: documento già finalizzato"
            )
        
        # Carica il modello e verifica che esista
        all_rules = load_layout_rules()
        if model_id not in all_rules:
            raise HTTPException(status_code=404, detail=f"Modello '{model_id}' non trovato")
        
        layout_rule = all_rules[model_id]
        supplier = layout_rule.match.supplier
        
        # FIX FASE 2: Marca documento per ricalcolo (i dati precedenti non sono più validi)
        from app.processed_documents import mark_document_needs_recalculation, clear_document_recalculation_flag
        mark_document_needs_recalculation(file_hash, template_id=model_id)
        logger.info(f"🔄 Documento marcato per ricalcolo: template '{model_id}' applicato manualmente")
        
        # FIX FASE 2: FORZA l'applicazione del template selezionato dall'operatore
        # Passa template_id a extract_from_pdf() per bypassare il matching automatico
        logger.info(f"🎯 Applicazione template forzato dall'operatore: '{model_id}' per mittente '{supplier}'")
        extracted_data = extract_from_pdf(file_path, template_id=model_id)
        
        # Estrai extraction_mode dal risultato
        extraction_mode = extracted_data.pop("_extraction_mode", None)
        
        # FIX FASE 2: Aggiorna la coda watchdog con i nuovi dati estratti
        from app.watchdog_queue import update_queue_item_by_hash
        queue_updated = update_queue_item_by_hash(file_hash, extracted_data, extraction_mode)
        if queue_updated:
            logger.info(f"✅ Coda watchdog aggiornata con nuovi dati estratti: file_hash={file_hash[:16]}...")
        else:
            logger.warning(f"⚠️ Elemento non trovato nella coda watchdog: file_hash={file_hash[:16]}... (potrebbe essere già processato)")
        
        # Rimuovi flag ricalcolo dopo successo
        clear_document_recalculation_flag(file_hash)
        
        logger.info(f"✅ Documento riprocessato con modello '{model_id}' per mittente '{supplier}'")
        
        return JSONResponse({
            "success": True,
            "message": f"Modello '{model_id}' applicato con successo",
            "extracted_data": extracted_data,
            "model_applied": {
                "id": model_id,
                "name": supplier
            },
            "extraction_mode": extraction_mode
        })
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Errore applicazione modello: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Errore durante l'applicazione del modello: {str(e)}")



