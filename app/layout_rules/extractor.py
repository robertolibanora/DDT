"""
Estrazione dati usando layout rules con box grafici
Quando una layout rule è attiva, estrae i dati dai box specificati
"""
import logging
from typing import Dict, Any, Optional, Tuple
from PIL import Image
import io
import base64

from app.layout_rules.models import LayoutRule, FieldBox
from app.text_extraction.ocr_fallback import extract_text_with_ocr, is_ocr_available

logger = logging.getLogger(__name__)


def extract_field_from_box(
    image_path: str,
    field_box: FieldBox,
    image_width: int,
    image_height: int
) -> Optional[str]:
    """
    Estrae testo da un box specifico dell'immagine usando OCR
    
    Args:
        image_path: Percorso dell'immagine PNG
        field_box: Box del campo da estrarre
        image_width: Larghezza dell'immagine in pixel
        image_height: Altezza dell'immagine in pixel
        
    Returns:
        Testo estratto o None se fallito
    """
    try:
        # Calcola coordinate reali in pixel dalle percentuali
        x = int(field_box.box.x_pct * image_width)
        y = int(field_box.box.y_pct * image_height)
        w = int(field_box.box.w_pct * image_width)
        h = int(field_box.box.h_pct * image_height)
        
        # Assicurati che le coordinate siano valide
        x = max(0, min(x, image_width - 1))
        y = max(0, min(y, image_height - 1))
        w = max(1, min(w, image_width - x))
        h = max(1, min(h, image_height - y))
        
        logger.debug(f"📦 Estrazione campo da box: x={x}, y={y}, w={w}, h={h}")
        
        # Carica l'immagine
        img = Image.open(image_path)
        
        # Ritaglia il box
        cropped = img.crop((x, y, x + w, y + h))
        
        # OCR sul box ritagliato
        if not is_ocr_available():
            logger.warning("OCR non disponibile per estrazione box")
            return None
        
        try:
            import pytesseract
            text = pytesseract.image_to_string(
                cropped,
                lang='ita+eng',
                config='--psm 7'  # Singola riga di testo
            )
            
            # Pulisci il testo
            text = text.strip()
            
            if text:
                logger.info(f"✅ Campo estratto da box: '{text[:50]}...'")
                return text
            else:
                logger.debug(f"⚠️ Box vuoto o nessun testo riconosciuto")
                return None
                
        except Exception as e:
            logger.warning(f"Errore OCR su box: {e}")
            return None
            
    except Exception as e:
        logger.error(f"Errore estrazione campo da box: {e}", exc_info=True)
        return None


def extract_with_layout_rule(
    pdf_path: str,
    layout_rule: LayoutRule,
    supplier: str,
    page_count: int
) -> Dict[str, Any]:
    """
    Estrae dati da un PDF usando una layout rule con box grafici
    
    Args:
        pdf_path: Percorso del file PDF
        layout_rule: Regola di layout da applicare
        supplier: Nome del fornitore (per logging)
        page_count: Numero di pagine del documento
        
    Returns:
        Dizionario con i dati estratti (può essere parziale, con fallback necessario)
    """
    logger.info(f"🎯 Estrazione con layout rule per supplier: {supplier}")
    
    # Converti PDF in PNG (prima pagina)
    try:
        import fitz  # PyMuPDF
        with open(pdf_path, 'rb') as f:
            pdf_bytes = f.read()
        
        doc = fitz.open(stream=pdf_bytes, filetype="pdf")
        if len(doc) == 0:
            raise ValueError("PDF vuoto")
        
        # Converti prima pagina in immagine
        page = doc[0]
        zoom = 200 / 72.0
        mat = fitz.Matrix(zoom, zoom)
        pix = page.get_pixmap(matrix=mat)
        
        # Salva temporaneamente l'immagine
        import tempfile
        with tempfile.NamedTemporaryFile(delete=False, suffix='.png') as tmp_file:
            tmp_path = tmp_file.name
            pix.save(tmp_path)
        
        image_width = pix.width
        image_height = pix.height
        
        doc.close()
        
        logger.info(f"✅ PNG generata: {image_width}x{image_height} pixel")
        
    except ImportError:
        logger.warning("PyMuPDF non disponibile, provo pdf2image...")
        try:
            from pdf2image import convert_from_bytes
            images = convert_from_bytes(pdf_bytes, first_page=1, last_page=1, dpi=200)
            if not images:
                raise ValueError("Impossibile convertire PDF")
            
            import tempfile
            with tempfile.NamedTemporaryFile(delete=False, suffix='.png') as tmp_file:
                tmp_path = tmp_file.name
                images[0].save(tmp_path, 'PNG')
            
            image_width, image_height = images[0].size
            logger.info(f"✅ PNG generata con pdf2image: {image_width}x{image_height} pixel")
            
        except Exception as e:
            logger.error(f"Errore conversione PDF in PNG: {e}")
            raise ValueError(f"Impossibile convertire PDF in immagine: {e}")
    except Exception as e:
        logger.error(f"Errore conversione PDF: {e}")
        raise ValueError(f"Errore conversione PDF: {e}")
    
    # Estrai campi dai box
    extracted_data = {}
    
    try:
        for field_name, field_box in layout_rule.fields.items():
            # Verifica che la pagina sia corretta (per ora solo pagina 1)
            if field_box.page != 1:
                logger.debug(f"Campo {field_name} su pagina {field_box.page}, salto (solo pagina 1 supportata)")
                continue
            
            logger.info(f"📦 Estrazione campo da box: {field_name}")
            text = extract_field_from_box(tmp_path, field_box, image_width, image_height)
            
            if text:
                extracted_data[field_name] = text
                logger.info(f"✅ Campo estratto da box: {field_name} = '{text[:50]}...'")
            else:
                logger.warning(f"⚠️ Campo vuoto da box: {field_name}")
        
        # Pulisci file temporaneo
        import os
        try:
            os.unlink(tmp_path)
        except:
            pass
        
        return extracted_data
        
    except Exception as e:
        logger.error(f"Errore durante estrazione con layout rule: {e}", exc_info=True)
        # Pulisci file temporaneo
        import os
        try:
            os.unlink(tmp_path)
        except:
            pass
        raise


def normalize_extracted_box_data(raw_data: Dict[str, str]) -> Dict[str, Any]:
    """
    Normalizza i dati estratti dai box per essere compatibili con il formato standard
    
    Args:
        raw_data: Dizionario con campo -> testo estratto
        
    Returns:
        Dizionario normalizzato pronto per validazione
    """
    from app.utils import normalize_date, normalize_float, normalize_text, clean_company_name
    
    normalized = {}
    
    # Normalizza ogni campo
    if 'data' in raw_data:
        normalized['data'] = normalize_date(raw_data['data']) or "1900-01-01"
    
    if 'mittente' in raw_data:
        normalized['mittente'] = clean_company_name(raw_data['mittente']) or "Non specificato"
    
    if 'destinatario' in raw_data:
        normalized['destinatario'] = clean_company_name(raw_data['destinatario']) or "Non specificato"
    
    if 'numero_documento' in raw_data:
        normalized['numero_documento'] = normalize_text(raw_data['numero_documento']) or "Non specificato"
    
    if 'totale_kg' in raw_data:
        normalized['totale_kg'] = normalize_float(raw_data['totale_kg']) or 0.0
    
    return normalized
