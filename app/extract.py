"""
Estrazione dati da DDT PDF usando OpenAI Vision
Con gestione robusta degli errori e validazione dati
Supporto per regole dinamiche e estrazione testo
"""
import base64
import logging
import sys
import os
from pathlib import Path
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


def extract_missing_fields_with_ai(
    file_path: str,
    missing_fields: list[str],
    already_extracted: dict,
    pdf_bytes: bytes,
    pdf_text: Optional[str] = None,
    text_extraction_result: Optional[Any] = None
) -> dict:
    """
    Usa AI_FALLBACK per estrarre SOLO i campi mancanti,
    usando already_extracted come contesto vincolante.
    
    REGOLE FERREE:
    - L'AI NON può modificare campi già estratti dal layout
    - Prompt esplicito: "estrai SOLO questi campi"
    - Se AI fallisce → solleva ValueError
    
    Args:
        file_path: Percorso del file PDF
        missing_fields: Lista di campi da estrarre (es: ['destinatario'])
        already_extracted: Dizionario con campi già estratti dal layout model
        pdf_bytes: Contenuto del PDF in bytes
        pdf_text: Testo estratto dal PDF (opzionale, per grounding)
        text_extraction_result: Risultato estrazione testo (opzionale)
        
    Returns:
        Dizionario con SOLO i campi mancanti estratti
        
    Raises:
        ValueError: Se l'estrazione AI fallisce o non completa i campi mancanti
    """
    logger.info(f"🤖 AI fallback mirato per campi mancanti: {missing_fields}")
    logger.info(f"   Campi già estratti dal layout (NON modificabili): {list(already_extracted.keys())}")
    
    # Costruisci prompt specifico per campi mancanti
    field_descriptions = {
        'data': 'Data del documento DDT (formato YYYY-MM-DD)',
        'mittente': 'Azienda che emette il DDT (chi spedisce)',
        'destinatario': 'Azienda che riceve la merce',
        'numero_documento': 'Numero del DDT',
        'totale_kg': 'Peso totale in chilogrammi (solo numero, float)'
    }
    
    missing_fields_desc = "\n".join([
        f"- **{field}**: {field_descriptions.get(field, field)}"
        for field in missing_fields
    ])
    
    # Costruisci contesto con campi già estratti (per riferimento ma NON modificabili)
    context_fields = "\n".join([
        f"- **{field}**: {already_extracted.get(field, 'N/A')}"
        for field in already_extracted.keys()
    ])
    
    targeted_prompt = f"""Sei un esperto estrattore di dati da Documenti di Trasporto (DDT) italiani.

⚠️ IMPORTANTE - REGOLE FERREE:
1. Estrai SOLO i seguenti campi mancanti (NON modificare gli altri):
{missing_fields_desc}

2. Campi già estratti dal layout model (NON modificare questi):
{context_fields}

3. Restituisci UNICAMENTE un JSON con SOLO i campi mancanti richiesti.
4. NON includere campi già estratti nel JSON di risposta.
5. Se un campo mancante non è trovabile, usa fallback appropriati:
   - data: "1900-01-01"
   - mittente/destinatario/numero_documento: "Non specificato"
   - totale_kg: 0.0

CAMPI DA ESTRARRE (SOLO QUESTI):
{missing_fields_desc}

IMPORTANTE: Restituisci SOLO il JSON con i campi mancanti, senza commenti."""
    
    # Aggiungi grounding del testo se disponibile
    if pdf_text and text_extraction_result and text_extraction_result.is_reliable:
        text_preview = pdf_text[:2000] if len(pdf_text) > 2000 else pdf_text
        if len(pdf_text) > 2000:
            text_preview += "\n... (testo troncato)"
        
        targeted_prompt += f"""

---
📄 TESTO ESTRATTO AUTOMATICAMENTE DAL PDF (RIFERIMENTO):
<<<
{text_preview}
>>>

⚠️ IMPORTANTE:
- Usa questo testo come riferimento per trovare i campi mancanti
- Privilegia sempre la validazione visiva del documento
"""
    
    # Converti PDF in immagine (riusa logica esistente)
    img_b64 = None
    image_format = "image/png"
    
    try:
        import fitz
        doc = fitz.open(stream=pdf_bytes, filetype="pdf")
        if len(doc) == 0:
            raise ValueError("PDF vuoto o non valido")
        
        page = doc[0]
        zoom = 200 / 72.0
        mat = fitz.Matrix(zoom, zoom)
        pix = page.get_pixmap(matrix=mat)
        img_bytes = pix.tobytes("png")
        img_b64 = base64.b64encode(img_bytes).decode()
        doc.close()
    except ImportError:
        try:
            from pdf2image import convert_from_bytes
            from io import BytesIO
            images = convert_from_bytes(pdf_bytes, first_page=1, last_page=1, dpi=200)
            if not images:
                raise ValueError("Impossibile convertire il PDF in immagine")
            img_buffer = BytesIO()
            images[0].save(img_buffer, format='PNG')
            img_bytes = img_buffer.getvalue()
            img_b64 = base64.b64encode(img_bytes).decode()
        except ImportError:
            raise ImportError("Nessuna libreria disponibile per convertire PDF")
    except Exception as e:
        raise ValueError(f"Errore conversione PDF: {e}") from e
    
    if not img_b64:
        raise ValueError("Impossibile convertire il PDF in immagine")
    
    # Chiama OpenAI Vision
    try:
        response: ChatCompletion = client.chat.completions.create(
            model=MODEL,
            messages=[
                {"role": "system", "content": targeted_prompt},
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "text",
                            "text": (
                                f"Estrai SOLO i seguenti campi mancanti dal DDT: {', '.join(missing_fields)}. "
                                f"Non modificare i campi già estratti: {', '.join(already_extracted.keys())}."
                            )
                        },
                        {"type": "image_url", "image_url": {"url": f"data:{image_format};base64,{img_b64}"}}
                    ],
                },
            ],
            response_format={"type": "json_object"},
            temperature=0.1,
        )
    except OpenAIError as e:
        logger.error(f"Errore API OpenAI durante fallback mirato: {e}")
        raise ValueError(f"Errore durante estrazione AI campi mancanti: {str(e)}") from e
    
    # Estrai il JSON dalla risposta
    if not response.choices or not response.choices[0].message.content:
        raise ValueError("Risposta vuota da OpenAI durante fallback mirato")
    
    import json
    try:
        ai_raw_data = json.loads(response.choices[0].message.content)
    except json.JSONDecodeError as e:
        logger.error(f"Errore parsing JSON da OpenAI durante fallback mirato: {e}")
        raise ValueError(f"Risposta non valida da OpenAI: {str(e)}") from e
    
    logger.info(f"🤖 Dati grezzi estratti da AI (solo campi mancanti): {ai_raw_data}")
    
    # Normalizza solo i campi mancanti
    ai_normalized = {}
    for field in missing_fields:
        if field in ai_raw_data:
            ai_normalized[field] = ai_raw_data[field]
        else:
            # Campo non estratto da AI → usa fallback
            logger.warning(f"⚠️ Campo '{field}' non estratto da AI, uso fallback")
            if field == 'data':
                ai_normalized[field] = "1900-01-01"
            elif field in ['mittente', 'destinatario', 'numero_documento']:
                ai_normalized[field] = "Non specificato"
            elif field == 'totale_kg':
                ai_normalized[field] = 0.0
    
    # Normalizza usando le funzioni esistenti
    if 'data' in ai_normalized:
        ai_normalized['data'] = normalize_date(str(ai_normalized['data'])) or "1900-01-01"
    if 'mittente' in ai_normalized:
        ai_normalized['mittente'] = clean_company_name(str(ai_normalized['mittente'])) or "Non specificato"
    if 'destinatario' in ai_normalized:
        ai_normalized['destinatario'] = clean_company_name(str(ai_normalized['destinatario'])) or "Non specificato"
    if 'numero_documento' in ai_normalized:
        ai_normalized['numero_documento'] = normalize_text(str(ai_normalized['numero_documento'])) or "Non specificato"
    if 'totale_kg' in ai_normalized:
        ai_normalized['totale_kg'] = normalize_float(ai_normalized['totale_kg']) or 0.0
    
    # Verifica che tutti i campi mancanti siano stati estratti
    extracted_missing = list(ai_normalized.keys())
    still_missing = [f for f in missing_fields if f not in extracted_missing]
    
    if still_missing:
        error_msg = (
            f"AI fallback non ha completato tutti i campi mancanti. "
            f"Estratti: {extracted_missing}, Ancora mancanti: {still_missing}"
        )
        logger.error(f"❌ {error_msg}")
        raise ValueError(error_msg)
    
    logger.info(f"✅ AI fallback completato per campi: {extracted_missing}")
    return ai_normalized


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
        from app.paths import safe_open
        file_path_obj = Path(file_path)
        if not file_path_obj.is_absolute():
            from app.paths import get_base_dir
            file_path_obj = get_base_dir() / file_path_obj
        file_path_obj = file_path_obj.resolve()
        
        with safe_open(file_path_obj, "rb") as f:
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
        
        # Carica layout rules (usa cache automatica per performance)
        from app.layout_rules.manager import load_layout_rules, match_layout_rule, normalize_sender, detect_layout_model_advanced
        layout_rules_loaded = load_layout_rules()
        
        # FASE 1: PRE-DETECTION AVANZATA DEL LAYOUT MODEL
        # Usa multiple strategie (keyword, nome file, testo) PRIMA dell'estrazione AI
        layout_rule = None
        layout_rule_name = None
        extraction_mode = None
        box_extracted_data = None  # Inizializza sempre per evitare errori
        
        logger.debug(f"🔍 Fase pre-detection layout model...")
        detection_result = detect_layout_model_advanced(pdf_text, file_path, page_count)
        
        if detection_result:
            layout_rule_name, layout_rule = detection_result
            logger.info(f"📐 LAYOUT MODEL MATCHED: '{layout_rule_name}'")
            logger.info(f"   Supplier modello: '{layout_rule.match.supplier}'")
            logger.info(f"   Fields disponibili: {list(layout_rule.fields.keys())}")
            logger.info(f"   Page count modello: {layout_rule.match.page_count or 'Tutte'}")
            logger.info(f"   Page count documento: {page_count}")
            extraction_mode = "LAYOUT_MODEL"
        else:
            logger.info(f"❌ LAYOUT MODEL SKIPPED: nessun match trovato nella pre-detection")
            logger.info(f"   Motivo: nessun layout model ha superato la soglia di similarity")
            extraction_mode = "AI_FALLBACK"
        
        # HARD FAILOVER: Se layout model matcha, USA SOLO BOX EXTRACTION, NON chiamare LLM
        if layout_rule:
            supplier_name = layout_rule.match.supplier
            logger.info(f"📐 LAYOUT MODEL APPLIED: '{layout_rule_name}' - Using LAYOUT_MODEL extraction mode (NO LLM)")
            logger.info(f"   Supplier: '{supplier_name}'")
            logger.info(f"   Fields disponibili nel modello: {list(layout_rule.fields.keys())}")
            
            # FIX #2: Verifica OCR disponibilità PRIMA di tentare estrazione
            from app.text_extraction.ocr_fallback import is_ocr_available
            ocr_available = is_ocr_available()
            
            if not ocr_available:
                error_msg = (
                    f"Layout model '{layout_rule_name}' richiede OCR ma OCR non è disponibile. "
                    f"Installa pytesseract e tesseract-ocr: pip install pytesseract && apt-get install tesseract-ocr"
                )
                logger.error(f"❌ {error_msg}")
                raise ValueError(error_msg)
            
            try:
                box_raw_data = extract_with_layout_rule(file_path, layout_rule, supplier_name, page_count)
                
                # FIX #2: Distingui fallimento temporaneo (OCR) vs permanente (box vuoti)
                if not box_raw_data:
                    error_msg = (
                        f"Layout model '{layout_rule_name}' matchato ma nessun campo estratto dai box. "
                        f"Verifica che i box siano corretti nel layout model."
                    )
                    logger.error(f"❌ {error_msg}")
                    logger.error(f"   OCR disponibile: {ocr_available}")
                    raise ValueError(error_msg)
                
                box_extracted_data = normalize_extracted_box_data(box_raw_data)
                
                # Log diagnostico: campi estratti vs disponibili
                total_fields = len(layout_rule.fields)
                extracted_fields = len(box_extracted_data)
                logger.info(f"✅ Dati estratti da box: {list(box_extracted_data.keys())}")
                logger.info(f"📊 Box extraction stats: {extracted_fields}/{total_fields} campi estratti")
                
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
                    # Aggiungi extraction_mode al risultato per audit trail
                    result["_extraction_mode"] = extraction_mode
                    logger.info(f"✅ Dati validati con successo (estrazione box)")
                    logger.info(f"📊 Extraction mode used: {extraction_mode}")
                    logger.info(f"📐 LAYOUT MODEL APPLIED: '{layout_rule_name}' - Estrazione completata senza AI")
                    return result
                except ValidationError as e:
                    # NUOVA STRATEGIA: Partial Layout Extraction con fallback AI mirato
                    error_fields = [err.get("loc", [])[0] for err in e.errors() if err.get("loc")]
                    missing_fields = [f for f in error_fields if f not in box_extracted_data]
                    invalid_fields = [f for f in error_fields if f in box_extracted_data]
                    
                    # REGOLA FERREA: Se almeno 1 campo estratto → fallback parziale consentito
                    extracted_count = len(box_extracted_data)
                    total_required_fields = len(layout_rule.fields)
                    
                    if missing_fields:
                        # Campi mancanti → fallback AI mirato SOLO se almeno 1 campo estratto
                        if extracted_count == 0:
                            # Layout estrae 0 campi → AI_FALLBACK classico
                            logger.warning(f"⚠️ Layout model '{layout_rule_name}' estratto 0 campi → fallback AI classico")
                            extraction_mode = "AI_FALLBACK"
                            # Continua con AI extraction completa (non siamo qui, ma per sicurezza)
                            # NOTA: Questo caso non dovrebbe mai verificarsi perché già gestito sopra (riga 278)
                            raise ValueError(
                                f"Layout model '{layout_rule_name}' estratto 0 campi. "
                                f"Verifica che i box siano corretti nel layout model."
                            )
                        
                        # Estrazione parziale: almeno 1 campo estratto → fallback AI mirato
                        logger.info(f"📐 Layout model '{layout_rule_name}' estrazione parziale: {extracted_count}/{total_required_fields} campi")
                        logger.warning(f"⚠️ Campi mancanti dal layout model: {missing_fields} → fallback AI mirato")
                        
                        # Prepara dati per fallback AI (serve pdf_bytes e pdf_text)
                        # NOTA: pdf_bytes e pdf_text sono già disponibili nel contesto della funzione extract_from_pdf
                        # Li passeremo alla funzione extract_missing_fields_with_ai
                        
                        # Estrai campi mancanti con AI
                        try:
                            ai_missing_data = extract_missing_fields_with_ai(
                                file_path=file_path,
                                missing_fields=missing_fields,
                                already_extracted=box_extracted_data,
                                pdf_bytes=pdf_bytes,
                                pdf_text=pdf_text,
                                text_extraction_result=text_extraction_result
                            )
                            
                            # Unisci risultati: layout (priorità) + AI (solo mancanti)
                            # REGOLA FERREA: Layout ha priorità, AI NON sovrascrive campi layout
                            hybrid_data = {**box_extracted_data, **ai_missing_data}
                            
                            logger.info(f"🤖 AI fallback completato per campi: {list(ai_missing_data.keys())}")
                            
                            # Applica suggerimenti di apprendimento automatico al risultato ibrido
                            try:
                                hybrid_data = apply_learning_suggestions(hybrid_data)
                                logger.info("Suggerimenti di apprendimento applicati (risultato ibrido)")
                            except Exception as e:
                                logger.warning(f"Errore applicazione suggerimenti apprendimento: {e}")
                            
                            # Valida il risultato finale completo
                            ddt_data = DDTData(**hybrid_data)
                            result = ddt_data.model_dump()
                            
                            extraction_mode = "HYBRID_LAYOUT_AI"
                            # Aggiungi extraction_mode al risultato per audit trail
                            result["_extraction_mode"] = extraction_mode
                            logger.info(f"✅ Documento completato con strategia HYBRID_LAYOUT_AI")
                            logger.info(f"📊 Extraction mode used: {extraction_mode}")
                            logger.info(f"📐 Layout model '{layout_rule_name}': {extracted_count} campi + AI: {len(ai_missing_data)} campi")
                            return result
                            
                        except ValueError as ai_error:
                            # AI fallback fallito → ERROR_FINAL
                            error_msg = (
                                f"Layout model '{layout_rule_name}' estratto {extracted_count}/{total_required_fields} campi, "
                                f"ma AI fallback per campi mancanti {missing_fields} è fallito: {ai_error}"
                            )
                            logger.error(f"❌ {error_msg}")
                            raise ValueError(error_msg) from ai_error
                        except Exception as ai_error:
                            # Errore generico durante AI fallback
                            error_msg = (
                                f"Errore durante AI fallback per campi mancanti {missing_fields}: {ai_error}"
                            )
                            logger.error(f"❌ {error_msg}", exc_info=True)
                            raise ValueError(error_msg) from ai_error
                    else:
                        # Campi presenti ma invalidi → fallback AI solo per correzione
                        logger.warning(f"⚠️ Campi invalidi dopo box extraction: {invalid_fields}")
                        logger.warning(f"   Fallback ad AI per correzione campi invalidi")
                        extraction_mode = "AI_FALLBACK"
                        # Continua con AI extraction per correggere campi invalidi
                        
            except ValueError as ve:
                # ValueError espliciti (OCR non disponibile, box vuoti, campi mancanti) → rilanciare
                raise
            except Exception as e:
                logger.error(f"❌ Errore estrazione con layout rule: {e}", exc_info=True)
                error_msg = f"Errore durante estrazione con layout model '{layout_rule_name}': {e}"
                raise ValueError(error_msg) from e
        
        # FIX #1: GUARD CLAUSE - Invariante extraction_mode
        # Se layout_rule era matchato ma arriviamo qui, significa che box extraction è fallita
        # NON procedere con AI extraction - solleva errore esplicito
        # NOTA: Questa guard clause non dovrebbe mai essere raggiunta dopo i fix sopra,
        # ma la manteniamo come safety check finale
        if layout_rule and extraction_mode == "LAYOUT_MODEL":
            logger.error(f"❌ CRITICAL: Layout model '{layout_rule_name}' matchato ma estrazione fallita")
            logger.error(f"   Questo non dovrebbe mai accadere - tutti i casi dovrebbero essere gestiti sopra")
            
            # Distingui motivo fallimento per log chiaro
            from app.text_extraction.ocr_fallback import is_ocr_available
            ocr_available = is_ocr_available()
            
            if not ocr_available:
                error_msg = (
                    f"Layout model '{layout_rule_name}' richiede OCR ma OCR non è disponibile. "
                    f"Installa pytesseract e tesseract-ocr: pip install pytesseract && apt-get install tesseract-ocr"
                )
                logger.error(f"   Motivo fallimento: OCR non disponibile")
            elif 'box_extracted_data' in locals() and not box_extracted_data:
                error_msg = (
                    f"Layout model '{layout_rule_name}' matchato ma box extraction vuota. "
                    f"Verifica che i box siano corretti nel layout model."
                )
                logger.error(f"   Motivo fallimento: box extraction vuota")
            elif 'box_extracted_data' in locals():
                error_msg = (
                    f"Layout model '{layout_rule_name}' matchato ma validazione fallita. "
                    f"Dati parziali estratti: {list(box_extracted_data.keys())}"
                )
                logger.error(f"   Motivo fallimento: validazione fallita")
            else:
                error_msg = (
                    f"Layout model '{layout_rule_name}' matchato ma estrazione fallita prima di completare. "
                    f"Verifica i log sopra per dettagli."
                )
                logger.error(f"   Motivo fallimento: errore durante estrazione")
            
            raise ValueError(error_msg)
        
        # Se siamo qui, extraction_mode è AI_FALLBACK (nessun layout matchato)
        
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
        
        # HARD FAILOVER: Se extraction_mode è LAYOUT_MODEL o HYBRID_LAYOUT_AI, NON dovremmo essere qui
        # Se siamo qui, significa che extraction_mode è AI_FALLBACK
        # (Questo check non dovrebbe mai essere raggiunto dopo le modifiche, ma lo manteniamo come safety check)
        if extraction_mode in ("LAYOUT_MODEL", "HYBRID_LAYOUT_AI"):
            layout_rule_name_safe = layout_rule_name if 'layout_rule_name' in locals() else 'UNKNOWN'
            error_msg = (
                f"❌ CRITICAL BUG: extraction_mode è {extraction_mode} ma siamo nella sezione AI extraction! "
                f"Questo indica un bug nel codice - la sezione layout model dovrebbe aver già restituito."
            )
            logger.error(error_msg)
            logger.error(f"   Stack trace completo necessario per debug")
            raise RuntimeError(error_msg)  # RuntimeError invece di ValueError per bug critico
        
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
            # Aggiungi extraction_mode al risultato per audit trail
            result["_extraction_mode"] = extraction_mode
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


def generate_preview_png(file_path: str, file_hash: str, output_dir: Optional[str] = None) -> Optional[str]:
    """
    Genera e salva una PNG di anteprima dalla prima pagina del PDF
    
    Args:
        file_path: Percorso del file PDF
        file_hash: Hash del file (usato come nome file PNG)
        output_dir: Directory dove salvare la PNG (default: usa get_preview_dir())
        
    Returns:
        Percorso del file PNG salvato o None se fallito
    """
    from app.paths import get_preview_dir, safe_open, ensure_dir
    
    try:
        # Leggi il file PDF
        file_path_obj = Path(file_path)
        if not file_path_obj.is_absolute():
            from app.paths import get_base_dir
            file_path_obj = get_base_dir() / file_path_obj
        file_path_obj = file_path_obj.resolve()
        
        with safe_open(file_path_obj, "rb") as f:
            pdf_bytes = f.read()
        
        if not pdf_bytes:
            logger.warning(f"File PDF vuoto: {file_path}")
            return None
        
        # Usa directory preview standardizzata se non specificata
        if output_dir is None:
            preview_dir = get_preview_dir()
        else:
            preview_dir = Path(output_dir)
            if not preview_dir.is_absolute():
                from app.paths import get_base_dir
                preview_dir = get_base_dir() / preview_dir
            preview_dir = ensure_dir(preview_dir.resolve())
        
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
        from app.paths import safe_open
        with safe_open(png_path, 'wb') as f:
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
