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
from app.corrections import apply_learning_suggestions

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
        
        # Applica suggerimenti di apprendimento automatico
        try:
            normalized_data = apply_learning_suggestions(normalized_data)
            logger.info("Suggerimenti di apprendimento applicati")
        except Exception as e:
            logger.warning(f"Errore applicazione suggerimenti apprendimento: {e}")
        
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
