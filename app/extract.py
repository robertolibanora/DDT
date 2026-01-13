"""
Estrazione dati da DDT PDF usando OpenAI Vision
Con gestione robusta degli errori e validazione dati
Supporto per regole dinamiche e estrazione testo
"""
import base64
import logging
import sys
import os
from typing import Dict, Any, Optional
from openai import OpenAI, OpenAIError
from openai.types.chat import ChatCompletion

# Gestione path quando eseguito come script diretto
if __name__ == "__main__":
    script_dir = os.path.dirname(os.path.abspath(__file__))
    root_dir = os.path.dirname(script_dir)
    if root_dir not in sys.path:
        sys.path.insert(0, root_dir)

from app.config import OPENAI_API_KEY, MODEL
from app.models import DDTData
from app.utils import normalize_date, normalize_float, normalize_text, clean_company_name
from app.rules.rules import detect_rule, build_prompt_additions, reload_rules
from app.corrections import apply_learning_suggestions, get_annotations_for_mittente
from app.text_extraction.orchestrator import extract_text_pipeline, extract_text_for_rule_detection
from app.text_extraction.decision import TextExtractionResult
from app.layout_rules.manager import match_layout_rule
from app.layout_rules.extractor import extract_with_layout_rule, normalize_extracted_box_data
from pydantic import ValidationError

logger = logging.getLogger(__name__)

client = OpenAI(api_key=OPENAI_API_KEY)

BASE_PROMPT = """Sei un esperto estrattore di dati da Documenti di Trasporto (DDT) italiani.
La tua missione è estrarre SOLO i seguenti campi e restituire UNICAMENTE un JSON valido e corretto.

CAMPI RICERCATI:

1. **data**: Data del documento DDT
   - Cerca varianti: "Data DDT", "Del:", "Data documento", "Data emissione", "Emissione"
   - Formato output: YYYY-MM-DD (esempio: 2024-11-27)
   - Se trovi solo giorno/mese, usa anno corrente
   - Se non trovi la data, usa "1900-01-01" come fallback

2. **mittente**: Azienda che emette il DDT (chi spedisce)
   - Cerca varianti: "Mittente", "Da:", "Fornitore", "Spett.le", "Intestazione azienda", logo azienda
   - Prendi il nome completo dell'azienda (non solo il logo)
   - Rimuovi prefissi come "Spett.le", "Da:", ecc.
   - Output: solo il nome dell'azienda pulito

3. **destinatario**: Azienda che riceve la merce
   - Cerca varianti: "Destinatario", "A:", "Cliente", "Consegna a", "Cantiere", "Spedire a"
   - Prendi il nome completo dell'azienda/cliente
   - Rimuovi prefissi come "A:", "Per:", ecc.
   - Output: solo il nome pulito

4. **numero_documento**: Numero del DDT
   - Cerca varianti: "Numero DDT", "DDT N.", "N. documento", "Documento N.", "Numero"
   - Prendi il numero completo (esempio: "DDT-12345" o "001234")
   - Se c'è un prefisso tipo "DDT-", non includerlo.
   
5. **totale_kg**: Peso totale in chilogrammi
   - Cerca varianti: "Totale Kg", "Peso totale", "Kg complessivi", "Totale peso", "Peso (kg)"
   - Output: SOLO il numero (float), senza unità di misura
   - Se trovi più pesi, prendi il TOTALE
   - Se non trovi il peso totale, cerca la somma dei pesi parziali
   - Se non trovi nulla, usa 0.0 come fallback

REGOLE STRINGENTI:
- Restituisci SEMPRE un JSON valido
- NON inventare dati se non li trovi (usa fallback appropriati)
- NON includere campi aggiuntivi oltre a quelli richiesti
- Se un campo è ambiguo, scegli la soluzione più probabile
- Normalizza i testi: rimuovi spazi extra, caratteri strani
- Per date: converti sempre in YYYY-MM-DD
- Per numeri: solo valori numerici, nessun testo

ESEMPIO OUTPUT CORRETTO:
{
  "data": "2024-11-27",
  "mittente": "ACME S.r.l.",
  "destinatario": "Mario Rossi & C.",
  "numero_documento": "DDT-12345",
  "totale_kg": 1250.5
}

IMPORTANTE: Restituisci SOLO il JSON, senza commenti, senza spiegazioni."""


def extract_text_from_pdf(file_path: str) -> str:
    """
    Estrae il testo grezzo da un PDF per il rilevamento delle regole
    (Funzione legacy mantenuta per compatibilità)
    
    Args:
        file_path: Percorso del file PDF
        
    Returns:
        Testo estratto dal PDF
    """
    return extract_text_for_rule_detection(file_path)


def build_dynamic_prompt(rule_name: Optional[str] = None, extracted_text: Optional[str] = None, annotations: Optional[Dict[str, Any]] = None) -> str:
    """
    Costruisce il prompt dinamico con eventuali regole aggiuntive e grounding del testo
    
    Args:
        rule_name: Nome della regola da applicare (opzionale)
        extracted_text: Testo estratto automaticamente per grounding (opzionale)
        annotations: Dizionario con coordinate dei riquadri annotati dall'utente (opzionale)
                    Formato: {field: {x, y, width, height}}
        
    Returns:
        Prompt completo con eventuali aggiunte e grounding
    """
    prompt = BASE_PROMPT
    
    # Aggiungi regole specifiche se presenti
    if rule_name:
        additions = build_prompt_additions(rule_name)
        if additions:
            prompt += additions
    
    # Aggiungi informazioni sulle annotazioni grafiche se disponibili
    if annotations:
        prompt += """

---
🎯 ANNOTAZIONI GRAFICHE (POSIZIONI INDICATE DALL'UTENTE):
L'utente ha indicato graficamente dove si trovano i dati nel documento. 
Usa queste informazioni come riferimento per cercare i dati nelle aree indicate.

"""
        field_labels = {
            'data': 'Data DDT',
            'mittente': 'Mittente',
            'destinatario': 'Destinatario',
            'numero_documento': 'Numero Documento',
            'totale_kg': 'Totale Kg'
        }
        
        for field, rect in annotations.items():
            field_label = field_labels.get(field, field)
            prompt += f"- **{field_label}**: Cerca nell'area approssimativa alle coordinate (x: {rect.get('x', 0):.0f}, y: {rect.get('y', 0):.0f}, larghezza: {rect.get('width', 0):.0f}, altezza: {rect.get('height', 0):.0f})\n"
        
        prompt += """
⚠️ NOTA: Le coordinate sono relative all'immagine del documento. 
Cerca i dati nelle aree indicate, ma verifica sempre che i dati estratti siano corretti.
"""
    
    # Aggiungi grounding del testo estratto se disponibile e affidabile
    if extracted_text and extracted_text.strip():
        # Limita la lunghezza del testo per evitare prompt troppo lunghi
        text_preview = extracted_text[:2000] if len(extracted_text) > 2000 else extracted_text
        if len(extracted_text) > 2000:
            text_preview += "\n... (testo troncato)"
        
        prompt += f"""

---
📄 TESTO ESTRATTO AUTOMATICAMENTE DAL PDF (RIFERIMENTO):
<<<
{text_preview}
>>>

⚠️ IMPORTANTE:
- Questo testo è stato estratto automaticamente e potrebbe essere incompleto o impreciso
- USA SEMPRE la validazione visiva del documento per confermare i dati
- Il testo serve come RIFERIMENTO per migliorare la precisione, non come fonte unica
- Se ci sono discrepanze tra testo e immagine, privilegia SEMPRE l'immagine
- Verifica attentamente numeri, date e nomi aziende confrontandoli con il documento visivo
"""
    
    return prompt

def extract_from_pdf(file_path: str) -> Dict[str, Any]:
    """
    Estrae dati strutturati da un PDF DDT usando OpenAI Vision
    
    Args:
        file_path: Percorso del file PDF
        
    Returns:
        Dizionario con i dati estratti e validati
        
    Raises:
        ValueError: Se l'estrazione o validazione fallisce
        OpenAIError: Se c'è un errore con l'API OpenAI
        FileNotFoundError: Se il file PDF non esiste
    """
    if not file_path:
        raise ValueError("Il percorso del file non può essere vuoto")
    
    try:
        # Leggi il file PDF
        with open(file_path, "rb") as f:
            pdf_bytes = f.read()
        
        if not pdf_bytes:
            raise ValueError(f"Il file {file_path} è vuoto")
        
        logger.info(f"Elaborazione PDF: {file_path} ({len(pdf_bytes)} bytes)")
        
        # Controlla numero di pagine per layout rule matching
        try:
            import fitz
            doc_temp = fitz.open(stream=pdf_bytes, filetype="pdf")
            page_count = len(doc_temp)
            doc_temp.close()
        except:
            page_count = 1
        
        # Estrai testo usando la nuova pipeline intelligente
        text_extraction_result = extract_text_pipeline(file_path, max_pages=5, enable_ocr=False)
        pdf_text = text_extraction_result.text if text_extraction_result else ""
        
        # IMPORTANTE: Carica layout rules ad ogni chiamata per garantire consistenza
        from app.layout_rules.manager import load_layout_rules, match_layout_rule, normalize_sender, detect_layout_model_advanced
        layout_rules_loaded = load_layout_rules()
        
        # FASE 1: PRE-DETECTION AVANZATA DEL LAYOUT MODEL
        # Usa multiple strategie (keyword, nome file, testo) PRIMA dell'estrazione AI
        layout_rule = None
        layout_rule_name = None
        extraction_mode = None
        box_extracted_data = None  # Inizializza sempre per evitare errori
        
        logger.info(f"🔍 Fase pre-detection layout model...")
        detection_result = detect_layout_model_advanced(pdf_text, file_path, page_count)
        
        if detection_result:
            layout_rule_name, layout_rule = detection_result
            logger.info(f"📐 LAYOUT MODEL APPLIED: '{layout_rule_name}'")
            logger.info(f"   Supplier: '{layout_rule.match.supplier}'")
            logger.info(f"   Fields: {list(layout_rule.fields.keys())}")
            extraction_mode = "LAYOUT_MODEL"
        else:
            logger.info(f"❌ LAYOUT MODEL SKIPPED: nessun match trovato nella pre-detection")
            extraction_mode = "AI_FALLBACK"
        
        # HARD FAILOVER: Se layout model matcha, USA SOLO BOX EXTRACTION, NON chiamare LLM
        if layout_rule:
            supplier_name = layout_rule.match.supplier
            logger.info(f"📐 LAYOUT MODEL APPLIED: '{layout_rule_name}' - Using LAYOUT_MODEL extraction mode (NO LLM)")
            logger.info(f"   Supplier: '{supplier_name}'")
            
            try:
                box_raw_data = extract_with_layout_rule(file_path, layout_rule, supplier_name, page_count)
                if box_raw_data:
                    box_extracted_data = normalize_extracted_box_data(box_raw_data)
                    logger.info(f"✅ Dati estratti da box: {list(box_extracted_data.keys())}")
                    
                    # Valida i dati estratti dai box
                    try:
                        normalized_data = box_extracted_data
                        # Applica suggerimenti di apprendimento automatico
                        try:
                            normalized_data = apply_learning_suggestions(normalized_data)
                            logger.info("Suggerimenti di apprendimento applicati")
                        except Exception as e:
                            logger.warning(f"Errore applicazione suggerimenti apprendimento: {e}")
                        
                        # Valida usando Pydantic
                        ddt_data = DDTData(**normalized_data)
                        result = ddt_data.model_dump()
                        logger.info(f"✅ Dati validati con successo (estrazione box)")
                        logger.info(f"📊 Extraction mode used: {extraction_mode}")
                        logger.info(f"📐 LAYOUT MODEL APPLIED: '{layout_rule_name}' - Estrazione completata senza AI")
                        return result
                    except ValidationError as e:
                        logger.error(f"❌ Validazione fallita per dati box: {e}")
                        logger.error(f"❌ Fallback to AI extraction due to validation failure")
                        extraction_mode = "AI_FALLBACK"
                        # Continua con AI extraction
                else:
                    logger.error(f"❌ Estrazione box fallita completamente")
                    logger.error(f"❌ Fallback to AI extraction")
                    extraction_mode = "AI_FALLBACK"
                    # Continua con AI extraction
            except Exception as e:
                logger.error(f"❌ Errore estrazione con layout rule: {e}", exc_info=True)
                logger.error(f"❌ Fallback to AI extraction")
                extraction_mode = "AI_FALLBACK"
                # Continua con AI extraction
        # Se siamo qui, extraction_mode è già impostato da detect_layout_model_advanced
        # (LAYOUT_MODEL se matchato, AI_FALLBACK altrimenti)
        
        # Log dettagli estrazione testo
        if text_extraction_result:
            logger.info(
                f"Estrazione testo: metodo={text_extraction_result.method}, "
                f"affidabile={text_extraction_result.is_reliable}, "
                f"confidence={text_extraction_result.confidence_score:.2f}, "
                f"motivo={text_extraction_result.reason}"
            )
        else:
            logger.warning("Estrazione testo fallita completamente")
        
        # Rilevamento regole usando il testo estratto
        rule_name = detect_rule(pdf_text) if pdf_text else None
        
        if rule_name:
            logger.info(f"Regola '{rule_name}' rilevata per questo documento")
        else:
            logger.info("Nessuna regola specifica rilevata, uso prompt standard")
        
        # Prova a ottenere annotazioni grafiche basate su un'estrazione preliminare del mittente
        # (se disponibile dal testo estratto, solo se non abbiamo già dati dai box)
        annotations = None
        if pdf_text and not box_extracted_data:
            # Estrai un possibile mittente dal testo per cercare annotazioni simili
            # Questo è un tentativo preliminare, le annotazioni verranno usate se disponibili
            try:
                # Cerca pattern comuni di mittente nel testo
                import re
                mittente_patterns = [
                    r'(?:Mittente|Da:|Fornitore|Spett\.le)\s*:?\s*([A-Z][A-Za-z0-9\s&\.]+)',
                    r'([A-Z][A-Za-z0-9\s&\.]+)\s*(?:S\.r\.l\.|S\.p\.A\.|S\.A\.S\.|S\.A\.)',
                ]
                potential_mittente_ann = None
                for pattern in mittente_patterns:
                    match = re.search(pattern, pdf_text[:500], re.IGNORECASE)
                    if match:
                        potential_mittente_ann = match.group(1).strip()
                        break
                
                if potential_mittente_ann:
                    annotations = get_annotations_for_mittente(potential_mittente_ann)
                    if annotations:
                        logger.info(f"Trovate annotazioni grafiche per mittente simile: {potential_mittente_ann}")
            except Exception as e:
                logger.debug(f"Errore ricerca annotazioni preliminari: {e}")
        
        # Costruisci prompt dinamico con grounding del testo (se affidabile) e annotazioni
        extracted_text_for_grounding = pdf_text if (text_extraction_result and text_extraction_result.is_reliable) else None
        dynamic_prompt = build_dynamic_prompt(rule_name, extracted_text=extracted_text_for_grounding, annotations=annotations)
        
        # Converti PDF in immagini (OpenAI Vision richiede immagini, non PDF)
        # Prova prima PyMuPDF (non richiede Poppler), poi pdf2image come fallback
        img_b64 = None
        image_format = "image/png"
        
        # Metodo 1: Prova con PyMuPDF (fitz) - migliore per Windows, non richiede Poppler
        try:
            import fitz  # PyMuPDF
            from io import BytesIO
            
            logger.info("Conversione PDF in immagine con PyMuPDF...")
            doc = fitz.open(stream=pdf_bytes, filetype="pdf")
            if len(doc) == 0:
                raise ValueError("PDF vuoto o non valido")
            
            # Converti la prima pagina in immagine
            page = doc[0]
            # Matrice di trasformazione per DPI 200 (200/72 = 2.78)
            zoom = 200 / 72.0
            mat = fitz.Matrix(zoom, zoom)
            pix = page.get_pixmap(matrix=mat)
            
            # Converti in PNG
            img_bytes = pix.tobytes("png")
            img_b64 = base64.b64encode(img_bytes).decode()
            doc.close()
            logger.info(f"PDF convertito in immagine PNG con PyMuPDF ({len(img_bytes)} bytes)")
            
        except ImportError:
            logger.warning("PyMuPDF non disponibile, provo con pdf2image...")
            # Metodo 2: Fallback a pdf2image (richiede Poppler su Windows)
            try:
                from pdf2image import convert_from_bytes
                from io import BytesIO
                
                logger.info("Conversione PDF in immagine con pdf2image...")
                images = convert_from_bytes(pdf_bytes, first_page=1, last_page=1, dpi=200)
                if not images:
                    raise ValueError("Impossibile convertire il PDF in immagine")
                
                img_buffer = BytesIO()
                images[0].save(img_buffer, format='PNG')
                img_bytes = img_buffer.getvalue()
                img_b64 = base64.b64encode(img_bytes).decode()
                logger.info(f"PDF convertito in immagine PNG con pdf2image ({len(img_bytes)} bytes)")
                
            except ImportError:
                error_msg = "Nessuna libreria disponibile per convertire PDF. Installa PyMuPDF (consigliato) o pdf2image+Poppler"
                logger.error(error_msg)
                raise ImportError(error_msg)
            except Exception as e:
                error_msg = f"Errore conversione PDF con pdf2image: {e}. Suggerimento: su Windows installa Poppler o usa PyMuPDF"
                logger.error(error_msg, exc_info=True)
                raise ValueError(error_msg) from e
        except Exception as e:
            logger.warning(f"Errore conversione PDF con PyMuPDF: {e}, provo fallback...")
            # Fallback a pdf2image se PyMuPDF fallisce
            try:
                from pdf2image import convert_from_bytes
                from io import BytesIO
                
                logger.info("Conversione PDF in immagine con pdf2image (fallback)...")
                images = convert_from_bytes(pdf_bytes, first_page=1, last_page=1, dpi=200)
                if not images:
                    raise ValueError("Impossibile convertire il PDF in immagine")
                
                img_buffer = BytesIO()
                images[0].save(img_buffer, format='PNG')
                img_bytes = img_buffer.getvalue()
                img_b64 = base64.b64encode(img_bytes).decode()
                logger.info(f"PDF convertito in immagine PNG con pdf2image (fallback) ({len(img_bytes)} bytes)")
            except Exception as e2:
                error_msg = f"Errore conversione PDF: PyMuPDF fallito ({e}), pdf2image fallito ({e2})"
                logger.error(error_msg, exc_info=True)
                raise ValueError(error_msg) from e2
        
        if not img_b64:
            raise ValueError("Impossibile convertire il PDF in immagine con nessun metodo disponibile")
        
        # Chiama OpenAI Vision
        try:
            response: ChatCompletion = client.chat.completions.create(
                model=MODEL,
                messages=[
                    {"role": "system", "content": dynamic_prompt},
                    {
                        "role": "user",
                        "content": [
                            {
                                "type": "text",
                                "text": (
                                    "Estrai i dati dal DDT nell'immagine seguente. "
                                    "Sii preciso e accurato. "
                                    + ("Il prompt include testo estratto automaticamente come riferimento - "
                                       "usa sempre la validazione visiva per confermare i dati." 
                                       if extracted_text_for_grounding else "")
                                )
                            },
                            {"type": "image_url", "image_url": {"url": f"data:{image_format};base64,{img_b64}"}}
                        ],
                    },
                ],
                response_format={"type": "json_object"},
                temperature=0.1,  # Bassa temperatura per risultati più deterministici
            )
        except OpenAIError as e:
            logger.error(f"Errore API OpenAI: {e}")
            raise ValueError(f"Errore durante l'estrazione dati: {str(e)}") from e
        
        # Estrai il JSON dalla risposta
        if not response.choices or not response.choices[0].message.content:
            raise ValueError("Risposta vuota da OpenAI")
        
        import json
        try:
            raw_data = json.loads(response.choices[0].message.content)
        except json.JSONDecodeError as e:
            logger.error(f"Errore parsing JSON da OpenAI: {e}")
            raise ValueError(f"Risposta non valida da OpenAI: {str(e)}") from e
        
        logger.info(f"Dati grezzi estratti: {raw_data}")
        
        # Normalizza i dati prima della validazione
        normalized_data = _normalize_extracted_data(raw_data)
        
        # HARD FAILOVER: Se extraction_mode è LAYOUT_MODEL, NON dovremmo essere qui
        # Se siamo qui, significa che extraction_mode è AI_FALLBACK
        if extraction_mode == "LAYOUT_MODEL":
            logger.error(f"❌ CRITICAL: extraction_mode è LAYOUT_MODEL ma siamo nella sezione AI extraction!")
            logger.error(f"❌ Questo non dovrebbe mai accadere - layout model dovrebbe aver già restituito")
            extraction_mode = "AI_FALLBACK"  # Forza fallback per sicurezza
        
        # Applica suggerimenti di apprendimento automatico
        try:
            normalized_data = apply_learning_suggestions(normalized_data)
            logger.info("Suggerimenti di apprendimento applicati")
        except Exception as e:
            logger.warning(f"Errore applicazione suggerimenti apprendimento: {e}")
        
        # Controllo preventivo: verifica che mittente e destinatario non siano identici
        mittente = normalized_data.get("mittente", "").strip()
        destinatario = normalized_data.get("destinatario", "").strip()
        
        if mittente.lower() == destinatario.lower():
            if mittente == "Non specificato" or not mittente:
                error_msg = (
                    f"Impossibile estrarre mittente e destinatario dal PDF. "
                    f"Entrambi i campi risultano vuoti o non specificati dopo la normalizzazione. "
                    f"Verifica che il PDF contenga informazioni chiare per mittente e destinatario."
                )
            else:
                error_msg = (
                    f"Mittente e destinatario risultano identici dopo la normalizzazione: '{mittente}'. "
                    f"Questo potrebbe indicare un errore nell'estrazione dei dati dal PDF. "
                    f"Verifica che il PDF contenga informazioni distinte per mittente e destinatario."
                )
            logger.error(error_msg)
            logger.error(f"Dati normalizzati completi: {normalized_data}")
            logger.error(f"Dati grezzi estratti: {raw_data}")
            raise ValueError(error_msg)
        
        # Assicura che extraction_mode sia impostato
        if extraction_mode is None:
            extraction_mode = "AI_FALLBACK"
        
        # Valida usando Pydantic
        try:
            ddt_data = DDTData(**normalized_data)
            result = ddt_data.model_dump()
            logger.info(f"✅ Dati validati con successo")
            logger.info(f"📊 Extraction mode used: {extraction_mode}")
            return result
        except ValidationError as e:
            # Estrai un messaggio più chiaro dagli errori di validazione Pydantic
            error_messages = []
            for error in e.errors():
                field = error.get("loc", [])
                field_name = " -> ".join(str(f) for f in field) if field else "campo sconosciuto"
                error_type = error.get("type", "unknown")
                error_msg = error.get("msg", "Errore di validazione")
                
                # Messaggi personalizzati per errori comuni
                if "Mittente e destinatario non possono essere identici" in str(error_msg):
                    mittente_val = normalized_data.get("mittente", "N/A")
                    error_messages.append(
                        f"Mittente e destinatario risultano identici: '{mittente_val}'. "
                        f"Verifica che il PDF contenga informazioni distinte per mittente e destinatario."
                    )
                elif error_type == "value_error":
                    error_messages.append(f"{field_name}: {error_msg}")
                else:
                    error_messages.append(f"{field_name}: {error_msg}")
            
            error_str = "; ".join(error_messages) if error_messages else str(e)
            logger.error(f"Errore validazione Pydantic: {e}")
            logger.error(f"Dati normalizzati: {normalized_data}")
            raise ValueError(f"Dati estratti non validi: {error_str}") from e
        except ValueError as e:
            # Se l'errore è già stato gestito sopra (mittente/destinatario identici), rilancia così com'è
            error_str = str(e)
            if "Mittente e destinatario risultano identici" in error_str:
                raise
            # Altrimenti, fornisci un messaggio più chiaro
            logger.error(f"Errore validazione dati: {e}")
            logger.error(f"Dati normalizzati: {normalized_data}")
            raise ValueError(f"Dati estratti non validi: {str(e)}") from e
        except Exception as e:
            logger.error(f"Errore validazione dati: {e}")
            logger.error(f"Dati normalizzati: {normalized_data}")
            raise ValueError(f"Dati estratti non validi: {str(e)}") from e
        
    except FileNotFoundError:
        raise FileNotFoundError(f"File PDF non trovato: {file_path}")
    except ValueError:
        # Rilancia ValueError così com'è (già gestito con messaggi chiari)
        raise
    except Exception as e:
        logger.error(f"Errore generico durante estrazione: {e}", exc_info=True)
        raise ValueError(f"Errore durante l'elaborazione del PDF: {str(e)}") from e


def generate_preview_png(file_path: str, file_hash: str, output_dir: str = "temp/preview") -> Optional[str]:
    """
    Genera e salva una PNG di anteprima dalla prima pagina del PDF
    
    Args:
        file_path: Percorso del file PDF
        file_hash: Hash del file (usato come nome file PNG)
        output_dir: Directory dove salvare la PNG (default: temp/preview)
        
    Returns:
        Percorso del file PNG salvato o None se fallito
    """
    from pathlib import Path
    
    try:
        # Leggi il file PDF
        with open(file_path, "rb") as f:
            pdf_bytes = f.read()
        
        if not pdf_bytes:
            logger.warning(f"File PDF vuoto: {file_path}")
            return None
        
        # Crea directory se non esiste
        preview_dir = Path(output_dir)
        preview_dir.mkdir(parents=True, exist_ok=True)
        
        png_path = preview_dir / f"{file_hash}.png"
        
        # Se esiste già, restituisci il percorso
        if png_path.exists():
            logger.debug(f"PNG anteprima già esistente: {png_path}")
            return str(png_path)
        
        img_bytes = None
        
        # Metodo 1: Prova con PyMuPDF (fitz) - migliore per Windows
        try:
            import fitz  # PyMuPDF
            
            logger.info(f"Generazione PNG anteprima con PyMuPDF per {file_path}...")
            doc = fitz.open(stream=pdf_bytes, filetype="pdf")
            if len(doc) == 0:
                raise ValueError("PDF vuoto o non valido")
            
            # Converti la prima pagina in immagine
            page = doc[0]
            # Matrice di trasformazione per DPI 200 (200/72 = 2.78)
            zoom = 200 / 72.0
            mat = fitz.Matrix(zoom, zoom)
            pix = page.get_pixmap(matrix=mat)
            
            # Converti in PNG
            img_bytes = pix.tobytes("png")
            doc.close()
            logger.info(f"PNG generata con PyMuPDF ({len(img_bytes)} bytes)")
            
        except ImportError:
            logger.warning("PyMuPDF non disponibile, provo con pdf2image...")
            # Metodo 2: Fallback a pdf2image
            try:
                from pdf2image import convert_from_bytes
                from io import BytesIO
                
                logger.info(f"Generazione PNG anteprima con pdf2image per {file_path}...")
                images = convert_from_bytes(pdf_bytes, first_page=1, last_page=1, dpi=200)
                if not images:
                    raise ValueError("Impossibile convertire il PDF in immagine")
                
                img_buffer = BytesIO()
                images[0].save(img_buffer, format='PNG')
                img_bytes = img_buffer.getvalue()
                logger.info(f"PNG generata con pdf2image ({len(img_bytes)} bytes)")
                
            except ImportError:
                logger.error("Nessuna libreria disponibile per convertire PDF. Installa PyMuPDF (consigliato) o pdf2image+Poppler")
                return None
            except Exception as e:
                logger.error(f"Errore conversione PDF con pdf2image: {e}")
                return None
        except Exception as e:
            logger.warning(f"Errore conversione PDF con PyMuPDF: {e}, provo fallback...")
            # Fallback a pdf2image se PyMuPDF fallisce
            try:
                from pdf2image import convert_from_bytes
                from io import BytesIO
                
                logger.info(f"Generazione PNG anteprima con pdf2image (fallback) per {file_path}...")
                images = convert_from_bytes(pdf_bytes, first_page=1, last_page=1, dpi=200)
                if not images:
                    raise ValueError("Impossibile convertire il PDF in immagine")
                
                img_buffer = BytesIO()
                images[0].save(img_buffer, format='PNG')
                img_bytes = img_buffer.getvalue()
                logger.info(f"PNG generata con pdf2image (fallback) ({len(img_bytes)} bytes)")
            except Exception as e2:
                logger.error(f"Errore conversione PDF: PyMuPDF fallito ({e}), pdf2image fallito ({e2})")
                return None
        
        if not img_bytes:
            logger.error("Impossibile generare PNG anteprima")
            return None
        
        # Salva la PNG
        with open(png_path, 'wb') as f:
            f.write(img_bytes)
        
        logger.info(f"✅ PNG anteprima salvata: {png_path} ({len(img_bytes)} bytes)")
        return str(png_path)
        
    except FileNotFoundError:
        logger.error(f"File PDF non trovato: {file_path}")
        return None
    except Exception as e:
        logger.error(f"Errore generazione PNG anteprima: {e}", exc_info=True)
        return None


def _normalize_extracted_data(raw_data: Dict[str, Any]) -> Dict[str, Any]:
    """
    Normalizza i dati grezzi estratti prima della validazione Pydantic
    
    Args:
        raw_data: Dati grezzi dal JSON di OpenAI
        
    Returns:
        Dizionario normalizzato pronto per validazione
    """
    normalized = {}
    
    # Normalizza data
    data_raw = raw_data.get("data", "")
    if isinstance(data_raw, str):
        normalized["data"] = normalize_date(data_raw) or "1900-01-01"
    else:
        normalized["data"] = "1900-01-01"
    
    # Normalizza mittente
    mittente_raw = raw_data.get("mittente", "")
    normalized["mittente"] = clean_company_name(str(mittente_raw)) or "Non specificato"
    
    # Normalizza destinatario
    destinatario_raw = raw_data.get("destinatario", "")
    normalized["destinatario"] = clean_company_name(str(destinatario_raw)) or "Non specificato"
    
    # Normalizza numero documento
    numero_raw = raw_data.get("numero_documento", "")
    normalized["numero_documento"] = normalize_text(str(numero_raw)) or "Non specificato"
    
    # Normalizza totale_kg
    kg_raw = raw_data.get("totale_kg", 0)
    normalized["totale_kg"] = normalize_float(kg_raw) or 0.0
    
    return normalized


if __name__ == "__main__":
    """
    Permette di eseguire extract.py direttamente per test
    Uso: python app/extract.py <percorso_file.pdf>
    """
    import sys
    import os
    
    # Aggiungi la directory root al path Python quando eseguito come script
    script_dir = os.path.dirname(os.path.abspath(__file__))
    root_dir = os.path.dirname(script_dir)
    if root_dir not in sys.path:
        sys.path.insert(0, root_dir)
    
    if len(sys.argv) < 2:
        print("Uso: python app/extract.py <percorso_file.pdf>")
        print("\nEsempio:")
        print("  python app/extract.py inbox/file.pdf")
        sys.exit(1)
    
    pdf_path = sys.argv[1]
    
    if not os.path.exists(pdf_path):
        print(f"❌ Errore: File non trovato: {pdf_path}")
        sys.exit(1)
    
    if not pdf_path.lower().endswith('.pdf'):
        print("❌ Errore: Il file deve essere un PDF")
        sys.exit(1)
    
    print(f"📄 Estrazione dati da: {pdf_path}")
    print("⏳ Elaborazione in corso...\n")
    
    try:
        data = extract_from_pdf(pdf_path)
        print("✅ Estrazione completata con successo!\n")
        print("📋 Dati estratti:")
        print(f"  📅 Data: {data.get('data', 'N/A')}")
        print(f"  🏢 Mittente: {data.get('mittente', 'N/A')}")
        print(f"  📍 Destinatario: {data.get('destinatario', 'N/A')}")
        print(f"  🔢 Numero Documento: {data.get('numero_documento', 'N/A')}")
        print(f"  ⚖️ Totale Kg: {data.get('totale_kg', 'N/A')}")
        print("\n✅ Test completato!")
    except Exception as e:
        print(f"\n❌ Errore durante l'estrazione: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
