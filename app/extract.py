"""
Estrazione dati da DDT PDF usando OpenAI Vision
Con gestione robusta degli errori e validazione dati
Supporto per regole dinamiche e estrazione testo
"""
import base64
import logging
from typing import Dict, Any, Optional
from openai import OpenAI, OpenAIError
from openai.types.chat import ChatCompletion

from app.config import OPENAI_API_KEY, MODEL
from app.models import DDTData
from app.utils import normalize_date, normalize_float, normalize_text, clean_company_name
from app.rules.rules import detect_rule, build_prompt_additions, reload_rules

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
   - Se c'è un prefisso tipo "DDT-", includilo

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
    
    Args:
        file_path: Percorso del file PDF
        
    Returns:
        Testo estratto dal PDF
    """
    try:
        import pdfplumber
        
        text_parts = []
        with pdfplumber.open(file_path) as pdf:
            # Estrai testo da tutte le pagine (limite ragionevole: prime 5 pagine)
            max_pages = min(5, len(pdf.pages))
            for i in range(max_pages):
                page = pdf.pages[i]
                page_text = page.extract_text()
                if page_text:
                    text_parts.append(page_text)
        
        return "\n".join(text_parts)
    except ImportError:
        logger.warning("pdfplumber non installato, uso fallback OCR")
        # Fallback: prova a usare pdf2image + OCR se disponibile
        try:
            from pdf2image import convert_from_bytes
            from io import BytesIO
            
            with open(file_path, "rb") as f:
                pdf_bytes = f.read()
            
            images = convert_from_bytes(pdf_bytes, first_page=1, last_page=1, dpi=200)
            if images:
                # Per ora restituiamo una stringa vuota se non abbiamo OCR
                # In futuro si potrebbe integrare pytesseract
                return ""
        except Exception as e:
            logger.warning(f"Errore estrazione testo fallback: {e}")
            return ""
    except Exception as e:
        logger.warning(f"Errore estrazione testo PDF: {e}")
        return ""


def build_dynamic_prompt(rule_name: Optional[str] = None) -> str:
    """
    Costruisce il prompt dinamico con eventuali regole aggiuntive
    
    Args:
        rule_name: Nome della regola da applicare (opzionale)
        
    Returns:
        Prompt completo con eventuali aggiunte
    """
    prompt = BASE_PROMPT
    
    if rule_name:
        additions = build_prompt_additions(rule_name)
        if additions:
            prompt += additions
    
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
        
        # Estrai testo per rilevamento regole
        pdf_text = extract_text_from_pdf(file_path)
        rule_name = detect_rule(pdf_text) if pdf_text else None
        
        if rule_name:
            logger.info(f"Regola '{rule_name}' rilevata per questo documento")
        else:
            logger.info("Nessuna regola specifica rilevata, uso prompt standard")
        
        # Costruisci prompt dinamico
        dynamic_prompt = build_dynamic_prompt(rule_name)
        
        # Converti PDF in immagini (OpenAI Vision richiede immagini, non PDF)
        try:
            from pdf2image import convert_from_bytes
            from io import BytesIO
            
            logger.info("Conversione PDF in immagine...")
            # Converti la prima pagina del PDF in immagine
            images = convert_from_bytes(pdf_bytes, first_page=1, last_page=1, dpi=200)
            if not images:
                raise ValueError("Impossibile convertire il PDF in immagine")
            
            # Converti l'immagine in base64
            img_buffer = BytesIO()
            images[0].save(img_buffer, format='PNG')
            img_bytes = img_buffer.getvalue()
            img_b64 = base64.b64encode(img_bytes).decode()
            image_format = "image/png"
            logger.info(f"PDF convertito in immagine PNG ({len(img_bytes)} bytes)")
            
        except ImportError:
            logger.error("pdf2image non installato. Installalo con: pip install pdf2image Pillow")
            raise ImportError("La libreria pdf2image è richiesta per processare i PDF. Installala con: pip install pdf2image Pillow")
        except Exception as e:
            logger.error(f"Errore conversione PDF in immagine: {e}", exc_info=True)
            raise ValueError(f"Errore durante la conversione del PDF in immagine: {str(e)}") from e
        
        # Chiama OpenAI Vision
        try:
            response: ChatCompletion = client.chat.completions.create(
                model=MODEL,
                messages=[
                    {"role": "system", "content": dynamic_prompt},
                    {
                        "role": "user",
                        "content": [
                            {"type": "text", "text": "Estrai i dati dal DDT nell'immagine seguente. Sii preciso e accurato."},
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
        
        # Valida usando Pydantic
        try:
            ddt_data = DDTData(**normalized_data)
            result = ddt_data.model_dump()
            logger.info(f"Dati validati con successo: {result}")
            return result
        except Exception as e:
            logger.error(f"Errore validazione dati: {e}")
            logger.error(f"Dati normalizzati: {normalized_data}")
            raise ValueError(f"Dati estratti non validi: {str(e)}") from e
        
    except FileNotFoundError:
        raise FileNotFoundError(f"File PDF non trovato: {file_path}")
    except Exception as e:
        logger.error(f"Errore generico durante estrazione: {e}", exc_info=True)
        raise ValueError(f"Errore durante l'elaborazione del PDF: {str(e)}") from e


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
