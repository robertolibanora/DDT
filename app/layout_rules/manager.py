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


def normalize_supplier_name(supplier: str) -> str:
    """
    Normalizza il nome del fornitore per il matching
    - Upper case
    - Strip spazi
    - Rimuove caratteri speciali comuni
    """
    if not supplier:
        return ""
    normalized = supplier.upper().strip()
    # Rimuovi caratteri comuni che possono variare
    normalized = normalized.replace(".", "").replace(",", "").replace("-", "").replace("_", "")
    return normalized


def load_layout_rules() -> Dict[str, LayoutRule]:
    """
    Carica tutte le regole di layout dal file JSON
    
    Returns:
        Dizionario con nome_regola -> LayoutRule
    """
    if not LAYOUT_RULES_FILE.exists():
        logger.info(f"File layout rules non trovato, creo file vuoto: {LAYOUT_RULES_FILE}")
        save_layout_rules({})
        return {}
    
    try:
        with open(LAYOUT_RULES_FILE, 'r', encoding='utf-8') as f:
            data = json.load(f)
        
        rules = {}
        for rule_name, rule_data in data.items():
            try:
                rule = LayoutRule(**rule_data)
                rules[rule_name] = rule
            except Exception as e:
                logger.warning(f"Errore caricamento regola {rule_name}: {e}")
                continue
        
        logger.info(f"✅ Caricate {len(rules)} regole di layout")
        return rules
    except json.JSONDecodeError as e:
        logger.error(f"Errore parsing JSON layout rules: {e}")
        return {}
    except Exception as e:
        logger.error(f"Errore caricamento layout rules: {e}", exc_info=True)
        return {}


def save_layout_rules(rules: Dict[str, LayoutRule]):
    """
    Salva le regole di layout nel file JSON
    
    Args:
        rules: Dizionario con nome_regola -> LayoutRule
    """
    try:
        # Converti le regole in dizionario JSON-serializzabile
        data = {}
        for rule_name, rule in rules.items():
            data[rule_name] = rule.model_dump()
        
        # Salva nel file
        with open(LAYOUT_RULES_FILE, 'w', encoding='utf-8') as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
        
        logger.info(f"✅ Salvate {len(rules)} regole di layout in {LAYOUT_RULES_FILE}")
    except Exception as e:
        logger.error(f"Errore salvataggio layout rules: {e}", exc_info=True)
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
    rules = load_layout_rules()
    normalized_supplier = normalize_supplier_name(supplier)
    
    logger.debug(f"🔍 Ricerca layout rule per supplier: {supplier} (normalizzato: {normalized_supplier}), pagine: {page_count}")
    
    for rule_name, rule in rules.items():
        match_criteria = rule.match
        normalized_rule_supplier = normalize_supplier_name(match_criteria.supplier)
        
        # Match supplier (deve corrispondere esattamente dopo normalizzazione)
        if normalized_rule_supplier != normalized_supplier:
            continue
        
        # Match page_count (se specificato nella regola)
        if match_criteria.page_count is not None:
            if page_count != match_criteria.page_count:
                continue
        
        logger.info(f"🎯 Layout rule applicata: {rule_name}")
        return rule
    
    logger.debug(f"⚠️ Nessuna layout rule trovata per supplier: {supplier}")
    return None


def save_layout_rule(rule_name: str, supplier: str, page_count: Optional[int], fields: Dict[str, Dict[str, Any]]) -> str:
    """
    Salva una nuova regola di layout o aggiorna una esistente
    
    Args:
        rule_name: Nome della regola (es: "FIORITAL_layout_v1")
        supplier: Nome del fornitore
        page_count: Numero di pagine (opzionale)
        fields: Dizionario con campo -> {page, box: {x_pct, y_pct, w_pct, h_pct}}
        
    Returns:
        Nome della regola salvata
    """
    # Carica regole esistenti
    rules = load_layout_rules()
    
    # Costruisci la regola
    match_criteria = LayoutRuleMatch(
        supplier=supplier,
        page_count=page_count
    )
    
    # Costruisci i campi
    rule_fields = {}
    for field_name, field_data in fields.items():
        box_data = field_data.get('box', {})
        box_coords = BoxCoordinates(
            x_pct=box_data['x_pct'],
            y_pct=box_data['y_pct'],
            w_pct=box_data['w_pct'],
            h_pct=box_data['h_pct']
        )
        field_box = FieldBox(
            page=field_data['page'],
            box=box_coords
        )
        rule_fields[field_name] = field_box
    
    # Crea la regola
    rule = LayoutRule(
        match=match_criteria,
        fields=rule_fields
    )
    
    # Salva
    rules[rule_name] = rule
    save_layout_rules(rules)
    
    logger.info(f"✅ Layout rule salvata: {rule_name} per supplier: {supplier}")
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
