"""
Manager per la gestione delle regole di layout DDT
Gestisce il salvataggio, caricamento e matching delle regole
"""
import json
import logging
import time
from pathlib import Path
from typing import Optional, Dict, Any, List
from app.layout_rules.models import LayoutRule, LayoutRulesFile, BoxCoordinates, FieldBox, LayoutRuleMatch

logger = logging.getLogger(__name__)

# Percorso del file delle regole
LAYOUT_RULES_FILE = Path(__file__).parent / "layout_rules.json"

# Cache per layout rules (evita ricaricamento continuo)
_layout_rules_cache: Optional[Dict[str, LayoutRule]] = None
_layout_rules_cache_timestamp: Optional[float] = None

# Soglia di similarità configurabile per fuzzy matching
LAYOUT_MODEL_SIMILARITY_THRESHOLD = 0.6

# Soglia di similarità geometrica per layout matching (più alta = più rigorosa)
LAYOUT_GEOMETRY_SIMILARITY_THRESHOLD = 0.85  # 85% di similarità geometrica richiesta


def calculate_sender_similarity(sender1: str, sender2: str) -> float:
    """
    Calcola la similarità tra due mittenti usando multiple strategie
    
    Strategie combinate:
    1. SequenceMatcher (difflib) - matching sequenziale
    2. Token overlap - matching basato su parole comuni
    3. Ignora parole non discriminanti (SRL, SPA, ecc.)
    
    Args:
        sender1: Primo mittente (normalizzato)
        sender2: Secondo mittente (normalizzato)
        
    Returns:
        Score di similarità tra 0.0 e 1.0
    """
    import difflib
    
    if not sender1 or not sender2:
        return 0.0
    
    # Parole non discriminanti da ignorare nel matching
    stop_words = {'srl', 'spa', 'sas', 'snc', 'srl', 'spa', 'sas', 'snc', 
                  'societa', 'società', 'con', 'socio', 'unico', 'di', 'da', 
                  'e', 'il', 'la', 'le', 'un', 'una', 'per', 'in', 'a'}
    
    # Tokenizza e filtra stop words
    def tokenize_and_filter(text: str) -> set:
        tokens = set(text.lower().split())
        return tokens - stop_words
    
    tokens1 = tokenize_and_filter(sender1)
    tokens2 = tokenize_and_filter(sender2)
    
    # Calcola token overlap (Jaccard similarity)
    if tokens1 or tokens2:
        intersection = tokens1 & tokens2
        union = tokens1 | tokens2
        token_similarity = len(intersection) / len(union) if union else 0.0
    else:
        token_similarity = 0.0
    
    # Calcola sequence similarity (difflib)
    sequence_similarity = difflib.SequenceMatcher(None, sender1.lower(), sender2.lower()).ratio()
    
    # Combina i due score (media pesata: 60% token, 40% sequence)
    # Token overlap è più robusto per variazioni OCR
    combined_similarity = (token_similarity * 0.6) + (sequence_similarity * 0.4)
    
    return combined_similarity


def normalize_sender(name: str) -> str:
    """
    Normalizza il nome del mittente per il matching deterministico
    
    Processo:
    - lowercase
    - rimuove punteggiatura
    - rimuove suffissi comuni (spa, srl, s.p.a., ecc.)
    - trim e spazi singoli
    
    Args:
        name: Nome del mittente originale
        
    Returns:
        Nome normalizzato per matching
    """
    if not name:
        return ""
    
    import re
    
    # Lowercase
    normalized = name.lower().strip()
    
    # Rimuovi punteggiatura comune
    normalized = normalized.replace(".", " ")
    normalized = normalized.replace(",", " ")
    normalized = normalized.replace("-", " ")
    normalized = normalized.replace("_", " ")
    normalized = normalized.replace("/", " ")
    normalized = normalized.replace("\\", " ")
    
    # Rimuovi suffissi comuni (case-insensitive)
    suffixes = [
        r'\bspa\b',
        r'\bsrl\b',
        r'\bs\.r\.l\.',
        r'\bs\.p\.a\.',
        r'\bspa\.',
        r'\bsas\b',
        r'\bs\.a\.s\.',
        r'\bsa\b',
        r'\bs\.a\.',
        r'\bcon socio unico\b',
        r'\bcon socio unico\.',
        r'\bsocietà\b',
        r'\bsocieta\b',
        r'\bsnc\b',
        r'\bs\.n\.c\.',
        r'\bsas\b',
        r'\bs\.a\.s\.',
    ]
    
    for suffix in suffixes:
        normalized = re.sub(suffix, '', normalized, flags=re.IGNORECASE)
    
    # Normalizza spazi multipli in singolo spazio
    normalized = re.sub(r'\s+', ' ', normalized)
    
    # Trim finale
    normalized = normalized.strip()
    
    return normalized


# Alias per compatibilità
normalize_supplier_name = normalize_sender


def load_layout_rules(force_reload: bool = False) -> Dict[str, LayoutRule]:
    """
    Carica tutte le regole di layout dal file JSON
    Usa cache per evitare ricaricamento continuo (refresh automatico se file modificato)
    
    Args:
        force_reload: Se True, forza il ricaricamento ignorando la cache
        
    Returns:
        Dizionario con nome_regola -> LayoutRule
    """
    global _layout_rules_cache, _layout_rules_cache_timestamp
    
    # Usa cache se disponibile e file non modificato
    if not force_reload and _layout_rules_cache is not None and LAYOUT_RULES_FILE.exists():
        try:
            file_mtime = LAYOUT_RULES_FILE.stat().st_mtime
            if _layout_rules_cache_timestamp == file_mtime:
                return _layout_rules_cache
        except Exception:
            # Se errore controllo timestamp, ricarica
            pass
    
    if not LAYOUT_RULES_FILE.exists():
        logger.warning(f"❌ File layout rules non trovato: {LAYOUT_RULES_FILE}")
        logger.info(f"📁 Creo file vuoto: {LAYOUT_RULES_FILE}")
        # Crea directory se non esiste
        LAYOUT_RULES_FILE.parent.mkdir(parents=True, exist_ok=True)
        save_layout_rules({})
        logger.info(f"✅ Loaded 0 layout rules: []")
        return {}
    
    try:
        with open(LAYOUT_RULES_FILE, 'r', encoding='utf-8') as f:
            file_content = f.read()
            if not file_content.strip():
                logger.error(f"❌ File layout rules è vuoto: {LAYOUT_RULES_FILE}")
                logger.info(f"✅ Loaded 0 layout rules: []")
                return {}
            data = json.loads(file_content)
        
        if not data:
            logger.warning(f"❌ File layout rules contiene dati vuoti: {LAYOUT_RULES_FILE}")
            logger.info(f"✅ Loaded 0 layout rules: []")
            return {}
        
        rules = {}
        sender_counts = {}
        
        for rule_name, rule_data in data.items():
            try:
                rule = LayoutRule(**rule_data)
                rules[rule_name] = rule
                
                # Conta per mittente
                supplier = rule.match.supplier
                sender_normalized = normalize_sender(supplier)
                sender_counts[sender_normalized] = sender_counts.get(sender_normalized, 0) + 1
                
            except Exception as e:
                logger.warning(f"⚠️ Errore caricamento regola {rule_name}: {e}")
                continue
        
        # Log dettagliato per mittente
        for sender_norm, count in sender_counts.items():
            logger.info(f"📦 Caricate {count} layout model(s) per sender: {sender_norm}")
        
        # Log esplicito con lista delle chiavi
        rule_keys = list(rules.keys())
        if rule_keys:
            logger.info(f"✅ Loaded {len(rules)} layout rules: {rule_keys}")
        else:
            logger.info(f"✅ Loaded {len(rules)} layout rules: []")
        
        # Aggiorna cache
        _layout_rules_cache = rules
        try:
            _layout_rules_cache_timestamp = LAYOUT_RULES_FILE.stat().st_mtime if LAYOUT_RULES_FILE.exists() else None
        except Exception:
            _layout_rules_cache_timestamp = None
        
        return rules
    except json.JSONDecodeError as e:
        logger.error(f"❌ Errore parsing JSON layout rules da {LAYOUT_RULES_FILE}: {e}")
        logger.error(f"❌ File potrebbe essere corrotto o malformato")
        logger.info(f"✅ Loaded 0 layout rules: []")
        return {}
    except Exception as e:
        logger.error(f"❌ Errore caricamento layout rules da {LAYOUT_RULES_FILE}: {e}", exc_info=True)
        logger.info(f"✅ Loaded 0 layout rules: []")
        return {}


def save_layout_rules(rules: Dict[str, LayoutRule]):
    """
    Salva le regole di layout nel file JSON
    
    Args:
        rules: Dizionario con nome_regola -> LayoutRule
    """
    global _layout_rules_cache, _layout_rules_cache_timestamp
    
    try:
        # Assicura che la directory esista
        LAYOUT_RULES_FILE.parent.mkdir(parents=True, exist_ok=True)
        
        # Converti le regole in dizionario JSON-serializzabile
        data = {}
        sender_counts = {}
        
        for rule_name, rule in rules.items():
            data[rule_name] = rule.model_dump()
            
            # Conta per mittente
            supplier = rule.match.supplier
            sender_normalized = normalize_sender(supplier)
            sender_counts[sender_normalized] = sender_counts.get(sender_normalized, 0) + 1
        
        # Salva nel file
        with open(LAYOUT_RULES_FILE, 'w', encoding='utf-8') as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
        
        # Log dettagliato
        for sender_norm, count in sender_counts.items():
            logger.info(f"💾 Layout model saved for sender: {sender_norm} ({count} model(s))")
        
        logger.info(f"✅ Salvate {len(rules)} regole di layout in {LAYOUT_RULES_FILE}")
        
        # Invalida cache per forzare ricaricamento al prossimo accesso
        _layout_rules_cache = None
        _layout_rules_cache_timestamp = None
    except Exception as e:
        logger.error(f"❌ Errore salvataggio layout rules: {e}", exc_info=True)
        raise


def match_layout_rule(
    supplier: str, 
    page_count: Optional[int] = None,
    similarity_threshold: float = LAYOUT_MODEL_SIMILARITY_THRESHOLD
) -> Optional[LayoutRule]:
    """
    Trova una regola di layout usando FUZZY MATCHING robusto
    
    Args:
        supplier: Nome del fornitore (mittente) - può avere variazioni OCR/formattazione
        page_count: Numero di pagine del documento (opzionale)
        similarity_threshold: Soglia minima di similarità (default: 0.6)
        
    Returns:
        LayoutRule se trovata con similarity >= threshold, None altrimenti
    """
    if not supplier or not supplier.strip():
        logger.debug("⚠️ Supplier vuoto, nessun matching possibile")
        return None
    
    rules = load_layout_rules()
    
    if not rules:
        logger.debug("⚠️ Nessuna regola di layout disponibile")
        return None
    
    normalized_supplier = normalize_sender(supplier)
    
    logger.debug(f"🔍 Fuzzy matching layout rule per sender: '{supplier}' (normalizzato: '{normalized_supplier}'), pagine: {page_count}, threshold: {similarity_threshold:.2f}")
    
    candidate_rules = []
    
    for rule_name, rule in rules.items():
        match_criteria = rule.match
        rule_supplier_original = match_criteria.supplier
        normalized_rule_supplier = normalize_sender(rule_supplier_original)
        
        # FIX #3: Page count check più flessibile - warning invece di hard skip
        page_count_mismatch = False
        if match_criteria.page_count is not None:
            if page_count != match_criteria.page_count:
                page_count_mismatch = True
                logger.debug(f"  ⚠️ Regola {rule_name}: page_count mismatch ({match_criteria.page_count} vs {page_count})")
                # Non skip immediato, ma penalizza se similarity bassa
        
        # Calcola similarity usando fuzzy matching
        similarity = calculate_sender_similarity(normalized_supplier, normalized_rule_supplier)
        
        # Log dettagli solo in DEBUG per evitare rumore
        logger.debug(f"  📊 Modello candidato: '{rule_name}'")
        logger.debug(f"     Supplier modello: '{rule_supplier_original}' (normalizzato: '{normalized_rule_supplier}')")
        logger.debug(f"     Similarity score: {similarity:.3f} {'✅' if similarity >= similarity_threshold else '❌'}")
        if page_count_mismatch:
            logger.debug(f"     ⚠️ Page count mismatch: regola={match_criteria.page_count}, doc={page_count}")
        
        # FIX #3: Se page_count mismatch ma similarity alta (>= 0.8) → procedi con warning
        if similarity >= similarity_threshold:
            if page_count_mismatch and similarity < 0.8:
                # Similarity < 0.8 e page_count mismatch → skip
                logger.debug(f"  ⏭️ Regola {rule_name} scartata: page_count mismatch e similarity < 0.8")
                continue
            elif page_count_mismatch:
                # Similarity >= 0.8 ma page_count mismatch → warning ma procedi
                logger.warning(
                    f"  ⚠️ Page count mismatch ({match_criteria.page_count} vs {page_count}) "
                    f"ma similarity alta ({similarity:.3f}) → procedo con warning"
                )
            candidate_rules.append((rule_name, rule, similarity))
    
    if candidate_rules:
        # Seleziona il modello con similarity più alta
        candidate_rules.sort(key=lambda x: x[2], reverse=True)
        rule_name, rule, best_similarity = candidate_rules[0]
        
        logger.info(f"✅ LAYOUT MODEL MATCHED: '{rule_name}'")
        logger.info(f"   Supplier estratto: '{supplier}' (normalizzato: '{normalized_supplier}')")
        logger.info(f"   Supplier modello: '{rule.match.supplier}' (normalizzato: '{normalize_sender(rule.match.supplier)}')")
        logger.info(f"   Similarity score: {best_similarity:.3f} (threshold: {similarity_threshold:.2f})")
        logger.info(f"   Fields disponibili: {list(rule.fields.keys())}")
        
        # Log altri candidati se presenti
        if len(candidate_rules) > 1:
            logger.info(f"   Altri candidati scartati:")
            for other_name, _, other_sim in candidate_rules[1:]:
                logger.info(f"     - {other_name}: similarity {other_sim:.3f}")
        
        return rule
    else:
        # Cambiato da WARNING a INFO: non è un errore, è normale per fornitori non noti
        logger.info(f"ℹ️ NO LAYOUT MODEL MATCHED per sender: '{supplier}' (normalizzato: '{normalized_supplier}')")
        logger.debug(f"   Motivo: nessun modello ha superato la soglia di similarity ({similarity_threshold:.2f})")
        return None


def calculate_layout_signature(rule: LayoutRule) -> List[float]:
    """
    Calcola la signature geometrica di un layout rule basata sulle posizioni delle box
    
    La signature è un vettore normalizzato che rappresenta la geometria del layout,
    indipendente dal contenuto testuale. Questo permette di matchare PDF con stesso
    layout ma dati diversi.
    
    Args:
        rule: LayoutRule da cui calcolare la signature
        
    Returns:
        Lista di float rappresentante la signature geometrica normalizzata
    """
    # Ordine standard dei campi per garantire consistenza
    standard_fields = ['mittente', 'destinatario', 'data', 'numero_documento', 'totale_kg']
    
    signature = []
    
    # Per ogni campo standard, aggiungi le coordinate normalizzate
    # Se il campo non è presente nel rule, usa [0, 0, 0, 0] come placeholder
    for field_name in standard_fields:
        if field_name in rule.fields:
            field_box = rule.fields[field_name]
            # Aggiungi centro e dimensione normalizzati
            # Centro X, Centro Y, Larghezza, Altezza (tutti in percentuale)
            center_x = field_box.box.x_pct + (field_box.box.w_pct / 2.0)
            center_y = field_box.box.y_pct + (field_box.box.h_pct / 2.0)
            signature.extend([center_x, center_y, field_box.box.w_pct, field_box.box.h_pct])
        else:
            # Campo non presente: usa zeri
            signature.extend([0.0, 0.0, 0.0, 0.0])
    
    return signature


def extract_pdf_layout_signature(file_path: str, page_count: Optional[int] = None) -> Optional[List[float]]:
    """
    Estrae la signature geometrica di un PDF analizzando le posizioni dei testi
    
    Usa pdfplumber per trovare le posizioni dei testi che corrispondono ai campi standard.
    Cerca pattern comuni nei label (es. "Mittente:", "Data:", ecc.) e usa le loro posizioni.
    
    Args:
        file_path: Percorso del file PDF
        page_count: Numero di pagine (opzionale, usa prima pagina se None)
        
    Returns:
        Lista di float rappresentante la signature geometrica o None se fallito
    """
    try:
        import pdfplumber
        
        # Campi standard da cercare con i loro pattern di label
        field_patterns = {
            'mittente': [r'mittente', r'da:', r'fornitore', r'spett\.le'],
            'destinatario': [r'destinatario', r'a:', r'cliente', r'consegna'],
            'data': [r'data', r'data\s+ddt', r'data\s+documento', r'emissione'],
            'numero_documento': [r'numero', r'ddt\s+n\.', r'numero\s+ddt', r'documento\s+n\.'],
            'totale_kg': [r'totale\s+kg', r'peso\s+totale', r'kg\s+complessivi', r'totale\s+peso']
        }
        
        signature = []
        page_to_use = 0  # Prima pagina (base 0)
        
        if page_count and page_count > 1:
            # Per ora usiamo solo la prima pagina
            page_to_use = 0
        
        with pdfplumber.open(file_path) as pdf:
            if len(pdf.pages) == 0:
                return None
            
            page = pdf.pages[page_to_use]
            
            # Estrai tutti i testi con le loro posizioni
            words = page.extract_words()
            
            if not words:
                return None
            
            # Ottieni dimensioni pagina per normalizzazione
            page_width = float(page.width)
            page_height = float(page.height)
            
            # Per ogni campo standard, cerca il pattern e estrai posizione
            for field_name, patterns in field_patterns.items():
                field_found = False
                
                # Cerca pattern nei testi
                for word in words:
                    word_text = word.get('text', '').lower()
                    
                    # Verifica se il testo corrisponde a uno dei pattern
                    for pattern in patterns:
                        import re
                        if re.search(pattern, word_text, re.IGNORECASE):
                            # Trovato! Estrai posizione del valore (di solito a destra del label)
                            x0 = word.get('x0', 0)
                            y0 = word.get('top', 0)  # pdfplumber usa 'top' invece di 'y0'
                            x1 = word.get('x1', 0)
                            bottom = word.get('bottom', 0)
                            
                            # Normalizza in percentuale
                            center_x = ((x0 + x1) / 2.0) / page_width
                            center_y = ((y0 + bottom) / 2.0) / page_height
                            width = (x1 - x0) / page_width
                            height = (bottom - y0) / page_height
                            
                            # Cerca il valore associato (di solito il prossimo testo a destra)
                            # Per semplicità, usa la posizione del label come proxy
                            signature.extend([center_x, center_y, width, height])
                            field_found = True
                            break
                    
                    if field_found:
                        break
                
                if not field_found:
                    # Campo non trovato: usa zeri
                    signature.extend([0.0, 0.0, 0.0, 0.0])
        
        # Verifica che la signature sia completa (deve avere 20 valori: 5 campi * 4 coordinate)
        if len(signature) != 20:
            logger.warning(f"⚠️ Signature incompleta: {len(signature)} valori invece di 20")
            return None
        
        return signature
        
    except ImportError:
        logger.debug("pdfplumber non disponibile per estrazione signature")
        return None
    except Exception as e:
        logger.debug(f"Errore estrazione signature PDF: {e}")
        return None


def calculate_geometry_similarity(signature1: List[float], signature2: List[float]) -> float:
    """
    Calcola la similarità geometrica tra due layout signature
    
    Usa distanza euclidea normalizzata e la converte in score di similarità (0.0-1.0).
    
    Args:
        signature1: Prima signature (da template)
        signature2: Seconda signature (da PDF)
        
    Returns:
        Score di similarità tra 0.0 e 1.0 (1.0 = identico, 0.0 = completamente diverso)
    """
    if len(signature1) != len(signature2):
        return 0.0
    
    if len(signature1) == 0:
        return 0.0
    
    # Calcola distanza euclidea normalizzata
    import math
    squared_diff = sum((a - b) ** 2 for a, b in zip(signature1, signature2))
    euclidean_distance = math.sqrt(squared_diff)
    
    # Normalizza la distanza (max possibile è sqrt(len(signature) * max_diff^2))
    # Assumendo che i valori siano tra 0.0 e 1.0, max_diff = 1.0
    max_possible_distance = math.sqrt(len(signature1) * 1.0)
    
    if max_possible_distance == 0:
        return 1.0
    
    normalized_distance = euclidean_distance / max_possible_distance
    
    # Converti distanza in similarità (1.0 - distanza normalizzata)
    similarity = 1.0 - normalized_distance
    
    # Assicura che sia tra 0.0 e 1.0
    return max(0.0, min(1.0, similarity))


def detect_layout_model_by_geometry(
    file_path: str,
    page_count: Optional[int] = None,
    similarity_threshold: float = LAYOUT_GEOMETRY_SIMILARITY_THRESHOLD
) -> Optional[tuple[str, LayoutRule]]:
    """
    Detect layout model usando SOLO similarità geometrica (box positions)
    
    Strategia PRIORITARIA: ignora completamente il testo, confronta solo le posizioni delle box.
    Questo risolve il caso di PDF con stesso layout ma dati diversi.
    
    Args:
        file_path: Percorso del file PDF
        page_count: Numero di pagine del documento
        similarity_threshold: Soglia minima di similarità geometrica (default: 0.85)
        
    Returns:
        Tupla (rule_name, LayoutRule) se trovata, None altrimenti
    """
    rules = load_layout_rules()
    
    if not rules:
        logger.debug("⚠️ Nessuna regola di layout disponibile per matching geometrico")
        return None
    
    # Estrai signature dal PDF
    pdf_signature = extract_pdf_layout_signature(file_path, page_count)
    
    if not pdf_signature:
        logger.debug("⚠️ Impossibile estrarre signature geometrica dal PDF")
        return None
    
    logger.debug(f"🔍 Layout matching geometrico: analizzando {len(rules)} template(s)")
    
    candidate_rules = []
    
    for rule_name, rule in rules.items():
        # Calcola signature del template
        template_signature = calculate_layout_signature(rule)
        
        # Calcola similarità geometrica
        geometry_similarity = calculate_geometry_similarity(template_signature, pdf_signature)
        
        # Verifica page_count se specificato
        page_count_match = True
        if rule.match.page_count is not None:
            if page_count != rule.match.page_count:
                page_count_match = False
                logger.debug(f"  ⚠️ Regola {rule_name}: page_count mismatch ({rule.match.page_count} vs {page_count})")
        
        # Se similarity supera la soglia, aggiungi ai candidati
        if geometry_similarity >= similarity_threshold:
            # Penalizza leggermente se page_count non matcha (ma non escludere completamente)
            final_similarity = geometry_similarity
            if not page_count_match:
                final_similarity *= 0.95  # Penalità del 5%
            
            logger.debug(f"  📐 Template candidato: '{rule_name}'")
            logger.debug(f"     Similarity geometrica: {geometry_similarity:.3f} {'✅' if final_similarity >= similarity_threshold else '❌'}")
            if not page_count_match:
                logger.debug(f"     ⚠️ Page count mismatch (penalità applicata)")
            
            if final_similarity >= similarity_threshold:
                candidate_rules.append((rule_name, rule, final_similarity))
    
    if candidate_rules:
        # Seleziona il template con similarity più alta
        candidate_rules.sort(key=lambda x: x[2], reverse=True)
        rule_name, rule, best_similarity = candidate_rules[0]
        
        logger.info(f"✅ LAYOUT MODEL MATCHED (GEOMETRY): '{rule_name}'")
        logger.info(f"   Similarity geometrica: {best_similarity:.3f} (threshold: {similarity_threshold:.2f})")
        logger.info(f"   Supplier modello: '{rule.match.supplier}'")
        logger.info(f"   Fields disponibili: {list(rule.fields.keys())}")
        
        # Log altri candidati se presenti
        if len(candidate_rules) > 1:
            logger.info(f"   Altri candidati geometrici:")
            for other_name, _, other_sim in candidate_rules[1:3]:  # Top 3
                logger.info(f"     - {other_name}: similarity {other_sim:.3f}")
        
        return (rule_name, rule)
    else:
        logger.debug(f"ℹ️ LAYOUT MODEL SKIPPED (GEOMETRY): nessun match con similarity >= {similarity_threshold:.2f}")
        return None


def detect_layout_model_advanced(
    pdf_text: str,
    file_path: str,
    page_count: Optional[int] = None,
    similarity_threshold: float = LAYOUT_MODEL_SIMILARITY_THRESHOLD
) -> Optional[tuple[str, LayoutRule]]:
    """
    Pre-detection avanzata del layout model usando STRATEGIE COMBINATE
    
    Strategie combinate (in ordine di priorità):
    1. Layout similarity (box geometry) - PRIORITARIA, ignora testo completamente
    2. Keyword matching nel testo (prime righe) + fuzzy matching
    3. Nome file matching + fuzzy matching
    4. Mittente estratto dal testo + fuzzy matching
    
    La strategia geometrica risolve il caso di PDF con stesso layout ma dati diversi.
    
    Args:
        pdf_text: Testo estratto dal PDF (usato solo per fallback se geometry fallisce)
        file_path: Percorso del file PDF
        page_count: Numero di pagine del documento
        similarity_threshold: Soglia minima di similarità per fallback testuale (default: 0.6)
        
    Returns:
        Tupla (rule_name, LayoutRule) se trovata, None altrimenti
    """
    # STRATEGIA 1: Layout similarity (GEOMETRY) - PRIORITARIA
    # Ignora completamente il testo, confronta solo le posizioni delle box
    logger.debug(f"🔍 Strategia 1: Layout matching geometrico (PRIORITARIA)")
    geometry_match = detect_layout_model_by_geometry(file_path, page_count)
    
    if geometry_match:
        rule_name, rule = geometry_match
        logger.info(f"✅ LAYOUT MODEL MATCHED via GEOMETRY: '{rule_name}' (skip fallback testuale)")
        return geometry_match
    
    # STRATEGIA 2: Fallback a matching testuale (solo se geometry fallisce)
    logger.debug(f"🔍 Strategia 2: Layout matching testuale (fallback)")
    rules = load_layout_rules()
    
    if not rules:
        logger.debug("⚠️ Nessuna regola di layout disponibile per pre-detection")
        return None
    
    import os
    import re
    from pathlib import Path
    
    file_name = Path(file_path).stem.lower()
    logger.debug(f"🔍 Layout pre-detection avanzata: analizzando file '{file_name}' (threshold: {similarity_threshold:.2f})")
    
    # Strategia 1: Keyword matching nel testo (prime 500 caratteri) + fuzzy
    text_sample = (pdf_text[:500] if pdf_text else "").lower()
    
    # Estrai potenziali mittenti dal testo per fuzzy matching
    extracted_mittenti = []
    if pdf_text:
        try:
            mittente_patterns = [
                r'(?:Mittente|Da:|Fornitore|Spett\.le)\s*:?\s*([A-Z][A-Za-z0-9\s&\.]+(?:S\.r\.l\.|S\.p\.A\.|S\.A\.S\.|S\.A\.|SRL|SPA)?)',
                r'([A-Z][A-Za-z0-9\s&\.]+)\s*(?:S\.r\.l\.|S\.p\.A\.|S\.A\.S\.|S\.A\.|SRL|SPA)',
            ]
            for pattern in mittente_patterns:
                match = re.search(pattern, pdf_text[:1000], re.IGNORECASE)
                if match:
                    extracted_mittente = match.group(1).strip()
                    extracted_mittenti.append(extracted_mittente)
        except Exception as e:
            logger.debug(f"Errore estrazione mittente per pre-detection: {e}")
    
    candidate_rules = []
    
    for rule_name, rule in rules.items():
        match_criteria = rule.match
        supplier_original = match_criteria.supplier
        supplier_normalized = normalize_sender(supplier_original)
        
        # Estrai keyword dal nome del supplier (prime 2-3 parole significative)
        supplier_words = supplier_normalized.split()[:3]
        keywords = [w for w in supplier_words if len(w) > 3]  # Solo parole > 3 caratteri
        
        # FIX #3: Page count check più flessibile - warning invece di hard skip
        page_count_mismatch = False
        if match_criteria.page_count is not None:
            if page_count != match_criteria.page_count:
                page_count_mismatch = True
                logger.debug(f"  ⚠️ Regola {rule_name}: page_count mismatch ({match_criteria.page_count} vs {page_count})")
                # Non skip immediato, ma penalizza se similarity bassa
        
        best_similarity = 0.0
        match_reason = None
        
        # Test 1: Keyword nel testo + fuzzy matching su mittenti estratti
        if keywords and text_sample:
            keyword_found = any(keyword in text_sample for keyword in keywords)
            if keyword_found:
                # Se keyword trovata, prova fuzzy matching con mittenti estratti
                for extracted_mittente in extracted_mittenti:
                    extracted_normalized = normalize_sender(extracted_mittente)
                    similarity = calculate_sender_similarity(extracted_normalized, supplier_normalized)
                    if similarity > best_similarity:
                        best_similarity = similarity
                        match_reason = f"keyword '{keywords[0]}' + fuzzy match (mittente estratto: '{extracted_mittente}')"
        
        # Test 2: Nome file + fuzzy matching
        if supplier_normalized:
            # Prova con supplier normalizzato completo
            if supplier_normalized in file_name:
                similarity = 0.9  # Match esatto nel nome file = alta confidence
                if similarity > best_similarity:
                    best_similarity = similarity
                    match_reason = "nome file contiene supplier completo"
            else:
                # Prova fuzzy matching con nome file
                # Estrai potenziali mittenti dal nome file
                file_tokens = set(file_name.split('_'))
                supplier_tokens = set(supplier_normalized.split())
                if supplier_tokens & file_tokens:  # Se ci sono token comuni
                    similarity = calculate_sender_similarity(file_name, supplier_normalized)
                    if similarity > best_similarity:
                        best_similarity = similarity
                        match_reason = f"fuzzy match con nome file"
        
        # Test 3: Fuzzy matching diretto con mittenti estratti
        for extracted_mittente in extracted_mittenti:
            extracted_normalized = normalize_sender(extracted_mittente)
            similarity = calculate_sender_similarity(extracted_normalized, supplier_normalized)
            if similarity > best_similarity:
                best_similarity = similarity
                match_reason = f"fuzzy match diretto (mittente estratto: '{extracted_mittente}')"
        
        # FIX #3: Se page_count mismatch ma similarity alta (>= 0.8) → procedi con warning
        if best_similarity >= similarity_threshold:
            if page_count_mismatch and best_similarity < 0.8:
                # Similarity < 0.8 e page_count mismatch → skip
                logger.debug(f"  ⏭️ Regola {rule_name} scartata: page_count mismatch e similarity < 0.8")
                continue
            elif page_count_mismatch:
                # Similarity >= 0.8 ma page_count mismatch → warning ma procedi
                logger.warning(
                    f"  ⚠️ Page count mismatch ({match_criteria.page_count} vs {page_count}) "
                    f"ma similarity alta ({best_similarity:.3f}) → procedo con warning"
                )
            logger.debug(f"  📊 Modello candidato: '{rule_name}'")
            logger.debug(f"     Supplier modello: '{supplier_original}' (normalizzato: '{supplier_normalized}')")
            logger.debug(f"     Similarity score: {best_similarity:.3f} ✅")
            logger.debug(f"     Match reason: {match_reason}")
            candidate_rules.append((rule_name, rule, best_similarity, match_reason))
    
    if candidate_rules:
        # Seleziona il modello con similarity più alta
        candidate_rules.sort(key=lambda x: x[2], reverse=True)
        rule_name, rule, best_similarity, match_reason = candidate_rules[0]
        
        logger.info(f"✅ LAYOUT MODEL MATCHED: '{rule_name}'")
        logger.info(f"   Similarity score: {best_similarity:.3f} (threshold: {similarity_threshold:.2f})")
        logger.info(f"   Match reason: {match_reason}")
        logger.info(f"   Supplier modello: '{rule.match.supplier}'")
        logger.info(f"   Supplier normalizzato: '{normalize_sender(rule.match.supplier)}'")
        
        # Log diagnostico: mittente estratto vs modello (se disponibile)
        if extracted_mittenti:
            logger.info(f"   Mittente estratto dal documento: '{extracted_mittenti[0]}'")
            logger.info(f"   Mittente normalizzato: '{normalize_sender(extracted_mittenti[0])}'")
            logger.info(f"   Similarity mittente estratto vs modello: {best_similarity:.3f}")
        
        # Log page count se specificato
        if match_criteria.page_count is not None:
            logger.info(f"   Page count modello: {match_criteria.page_count}, documento: {page_count}")
            if page_count_mismatch:
                logger.warning(f"   ⚠️ Page count mismatch ma similarity alta → procedo")
        
        # Log altri candidati se presenti (top 3)
        if len(candidate_rules) > 1:
            logger.info(f"   Top candidati:")
            for idx, (other_name, _, other_sim, other_reason) in enumerate(candidate_rules[:3], 1):
                logger.info(f"     {idx}. {other_name}: similarity {other_sim:.3f} ({other_reason})")
        
        return (rule_name, rule)
    else:
        # Cambiato da INFO a DEBUG: non è necessario loggare ogni volta che non c'è match
        logger.debug(f"ℹ️ LAYOUT MODEL SKIPPED: nessun match trovato con similarity >= {similarity_threshold:.2f}")
        if extracted_mittenti:
            logger.debug(f"   Mittenti estratti provati: {extracted_mittenti}")
        return None


def save_layout_rule(rule_name: str, supplier: str, page_count: Optional[int], fields: Dict[str, Dict[str, Any]]) -> str:
    """
    Salva una nuova regola di layout o aggiorna una esistente
    
    Args:
        rule_name: Nome della regola (es: "FIORITAL_layout_v1")
        supplier: Nome del fornitore (mittente originale)
        page_count: Numero di pagine (opzionale)
        fields: Dizionario con campo -> {page, box: {x_pct, y_pct, w_pct, h_pct}}
        
    Returns:
        Nome della regola salvata
    """
    if not supplier or not supplier.strip():
        raise ValueError("Il nome del mittente non può essere vuoto")
    
    if not fields:
        raise ValueError("Deve essere definito almeno un campo")
    
    # Normalizza il mittente per logging
    sender_normalized = normalize_sender(supplier)
    
    # Carica regole esistenti
    rules = load_layout_rules()
    
    # Costruisci la regola
    match_criteria = LayoutRuleMatch(
        supplier=supplier.strip(),  # Mantieni originale ma pulito
        page_count=page_count
    )
    
    # Costruisci i campi
    rule_fields = {}
    for field_name, field_data in fields.items():
        box_data = field_data.get('box', {})
        
        # Valida che i dati del box siano presenti
        if not all(k in box_data for k in ['x_pct', 'y_pct', 'w_pct', 'h_pct']):
            logger.warning(f"⚠️ Campo {field_name} ha dati box incompleti, salto")
            continue
        
        box_coords = BoxCoordinates(
            x_pct=box_data['x_pct'],
            y_pct=box_data['y_pct'],
            w_pct=box_data['w_pct'],
            h_pct=box_data['h_pct']
        )
        field_box = FieldBox(
            page=field_data.get('page', 1),
            box=box_coords
        )
        rule_fields[field_name] = field_box
    
    if not rule_fields:
        raise ValueError("Nessun campo valido da salvare")
    
    # Crea la regola
    rule = LayoutRule(
        match=match_criteria,
        fields=rule_fields
    )
    
    # Salva (sovrascrive se esiste già)
    rules[rule_name] = rule
    save_layout_rules(rules)
    
    logger.info(f"💾 Layout model saved for sender: '{supplier}' (normalizzato: '{sender_normalized}')")
    logger.info(f"   Regola: {rule_name}, Campi: {list(rule_fields.keys())}, Pagine: {page_count or 'Tutte'}")
    
    return rule_name


def get_all_layout_rules() -> Dict[str, Dict[str, Any]]:
    """
    Restituisce tutte le regole di layout in formato JSON-serializzabile
    
    Returns:
        Dizionario con tutte le regole
    """
    rules = load_layout_rules()
    return {name: rule.model_dump() for name, rule in rules.items()}


def delete_layout_rule(rule_name: str) -> bool:
    """
    Elimina una regola di layout
    
    Args:
        rule_name: Nome della regola da eliminare
        
    Returns:
        True se eliminata, False se non trovata
    """
    rules = load_layout_rules()
    
    if rule_name not in rules:
        logger.warning(f"Regola {rule_name} non trovata per eliminazione")
        return False
    
    del rules[rule_name]
    save_layout_rules(rules)
    
    logger.info(f"✅ Layout rule eliminata: {rule_name}")
    return True
