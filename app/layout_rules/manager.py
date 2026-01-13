"""
Manager per la gestione delle regole di layout DDT
Gestisce il salvataggio, caricamento e matching delle regole
"""
import json
import logging
from pathlib import Path
from typing import Optional, Dict, Any, List
from app.layout_rules.models import LayoutRule, LayoutRulesFile, BoxCoordinates, FieldBox, LayoutRuleMatch

logger = logging.getLogger(__name__)

# Percorso del file delle regole
LAYOUT_RULES_FILE = Path(__file__).parent / "layout_rules.json"


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


def load_layout_rules() -> Dict[str, LayoutRule]:
    """
    Carica tutte le regole di layout dal file JSON
    
    Returns:
        Dizionario con nome_regola -> LayoutRule
    """
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
    except Exception as e:
        logger.error(f"❌ Errore salvataggio layout rules: {e}", exc_info=True)
        raise


def match_layout_rule(supplier: str, page_count: Optional[int] = None) -> Optional[LayoutRule]:
    """
    Trova una regola di layout che corrisponde ai criteri forniti
    
    Args:
        supplier: Nome del fornitore (mittente)
        page_count: Numero di pagine del documento (opzionale)
        
    Returns:
        LayoutRule se trovata, None altrimenti
    """
    if not supplier or not supplier.strip():
        logger.debug("⚠️ Supplier vuoto, nessun matching possibile")
        return None
    
    rules = load_layout_rules()
    
    if not rules:
        logger.debug("⚠️ Nessuna regola di layout disponibile")
        return None
    
    normalized_supplier = normalize_sender(supplier)
    
    logger.info(f"🔍 Ricerca layout rule per sender: '{supplier}' (normalizzato: '{normalized_supplier}'), pagine: {page_count}")
    
    matched_rules = []
    
    for rule_name, rule in rules.items():
        match_criteria = rule.match
        normalized_rule_supplier = normalize_sender(match_criteria.supplier)
        
        # Match supplier (deve corrispondere esattamente dopo normalizzazione)
        if normalized_rule_supplier != normalized_supplier:
            continue
        
        # Match page_count (se specificato nella regola)
        if match_criteria.page_count is not None:
            if page_count != match_criteria.page_count:
                logger.debug(f"  ⏭️ Regola {rule_name}: page_count mismatch ({match_criteria.page_count} vs {page_count})")
                continue
        
        matched_rules.append((rule_name, rule))
    
    if matched_rules:
        # Prendi la prima regola matchata (potremmo migliorare con priorità)
        rule_name, rule = matched_rules[0]
        logger.info(f"📐 Layout rule APPLIED for mittente: '{supplier}' (normalizzato: '{normalized_supplier}')")
        logger.info(f"   Rule name: {rule_name}")
        logger.debug(f"   Rule details - Fields: {list(rule.fields.keys())}")
        logger.debug(f"   Rule details - Box coordinates:")
        for field_name, field_box in rule.fields.items():
            logger.debug(f"     {field_name}: page={field_box.page}, x_pct={field_box.box.x_pct:.4f}, y_pct={field_box.box.y_pct:.4f}, w_pct={field_box.box.w_pct:.4f}, h_pct={field_box.box.h_pct:.4f}")
        return rule
    else:
        logger.warning(f"❌ No layout rule match for mittente: '{supplier}' (normalizzato: '{normalized_supplier}')")
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
