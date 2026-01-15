"""
Gestione dinamica delle regole per l'estrazione DDT
Carica, salva e applica regole personalizzate per fornitori specifici
"""
import json
import logging
import os
import threading
from typing import Dict, Any, Optional, List
from pathlib import Path

logger = logging.getLogger(__name__)

RULES_FILE = Path(__file__).parent / "rules.json"

# Cache delle regole per performance (thread-safe)
_rules_cache: Optional[Dict[str, Any]] = None
_rules_lock = threading.Lock()


def _load_rules() -> Dict[str, Any]:
    """
    Carica le regole dal file JSON (thread-safe)
    
    Returns:
        Dizionario con tutte le regole
    """
    global _rules_cache
    
    # Double-check locking pattern per thread-safety
    if _rules_cache is not None:
        return _rules_cache
    
    with _rules_lock:
        # Verifica di nuovo dentro il lock (double-check)
        if _rules_cache is not None:
            return _rules_cache
        
        if not RULES_FILE.exists():
            logger.info("File regole non trovato, creo %s vuoto", str(RULES_FILE))
            _rules_cache = {}
            try:
                _save_rules(_rules_cache)
            except Exception as e:
                logger.warning("Errore salvataggio file regole vuoto: %s - continuo senza blocchi", str(e))
            return _rules_cache
        
        try:
            with open(RULES_FILE, 'r', encoding='utf-8') as f:
                file_content = f.read()
                if not file_content.strip():
                    logger.warning("❌ [ANTI-CRASH] File regole è vuoto: %s - uso valori safe di default", str(RULES_FILE))
                    _rules_cache = {}
                    return _rules_cache
                _rules_cache = json.loads(file_content)
            
            # Validazione struttura: assicura che sia un dict
            if not isinstance(_rules_cache, dict):
                logger.error("❌ [ANTI-CRASH] File regole non contiene un dict valido: %s - uso valori safe di default", str(RULES_FILE))
                _rules_cache = {}
                return _rules_cache
            
            logger.info("Caricate %d regole da %s", len(_rules_cache), str(RULES_FILE))
            return _rules_cache
        except json.JSONDecodeError as e:
            logger.error("❌ [ANTI-CRASH] Errore parsing JSON regole: %s - uso valori safe di default", str(e))
            _rules_cache = {}
            return _rules_cache
        except Exception as e:
            logger.error("❌ [ANTI-CRASH] Errore caricamento regole: %s - uso valori safe di default", str(e), exc_info=True)
            _rules_cache = {}
            return _rules_cache


def _save_rules(rules: Dict[str, Any]) -> None:
    """
    Salva le regole nel file JSON (thread-safe)
    
    Args:
        rules: Dizionario con tutte le regole
    """
    global _rules_cache
    
    with _rules_lock:
        try:
            # Crea la directory se non esiste
            RULES_FILE.parent.mkdir(parents=True, exist_ok=True)
            
            with open(RULES_FILE, 'w', encoding='utf-8') as f:
                json.dump(rules, f, indent=2, ensure_ascii=False)
            
            # Aggiorna la cache
            _rules_cache = rules.copy()
            logger.info(f"Regole salvate in {RULES_FILE}")
        except Exception as e:
            logger.error(f"Errore salvataggio regole: {e}", exc_info=True)
            raise


def reload_rules() -> None:
    """Ricarica le regole dal file (forza refresh cache, thread-safe)"""
    global _rules_cache
    with _rules_lock:
        _rules_cache = None
    _load_rules()


def get_all_rules() -> Dict[str, Any]:
    """
    Ottiene tutte le regole
    
    Returns:
        Dizionario con tutte le regole
    """
    return _load_rules()


def get_rule(name: str) -> Optional[Dict[str, Any]]:
    """
    Ottiene una regola specifica
    
    Args:
        name: Nome della regola
        
    Returns:
        Dizionario con la regola o None se non esiste
    """
    rules = _load_rules()
    return rules.get(name)


def add_rule(name: str, rule_data: Dict[str, Any]) -> None:
    """
    Aggiunge o aggiorna una regola
    
    Args:
        name: Nome della regola
        rule_data: Dati della regola (deve contenere 'detect', 'instructions', 'overrides')
    """
    rules = _load_rules()
    rules[name] = rule_data
    _save_rules(rules)
    logger.info(f"Regola '{name}' aggiunta/aggiornata")


def delete_rule(name: str) -> bool:
    """
    Elimina una regola
    
    Args:
        name: Nome della regola
        
    Returns:
        True se eliminata, False se non esisteva
    """
    rules = _load_rules()
    if name in rules:
        del rules[name]
        _save_rules(rules)
        logger.info(f"Regola '{name}' eliminata")
        return True
    return False


def detect_rule(text: str) -> Optional[str]:
    """
    Rileva quale regola applicare basandosi sul testo del documento
    
    Args:
        text: Testo estratto dal PDF
        
    Returns:
        Nome della regola applicabile o None
    """
    if not text:
        return None
    
    text_upper = text.upper()
    rules = _load_rules()
    
    # Per ogni regola, controlla se i keyword sono presenti nel testo
    for rule_name, rule_data in rules.items():
        detect_keywords = rule_data.get("detect", [])
        if not detect_keywords:
            continue
        
        # Controlla se almeno uno dei keyword è presente
        for keyword in detect_keywords:
            if keyword.upper() in text_upper:
                logger.info(f"Regola '{rule_name}' rilevata per keyword '{keyword}'")
                return rule_name
    
    return None


def build_prompt_additions(rule_name: str) -> str:
    """
    Costruisce le aggiunte al prompt basate sulla regola
    
    Args:
        rule_name: Nome della regola
        
    Returns:
        Stringa con le istruzioni aggiuntive da aggiungere al prompt
    """
    rule = get_rule(rule_name)
    if not rule:
        return ""
    
    additions = []
    
    # Aggiungi istruzioni specifiche
    instructions = rule.get("instructions", "")
    if instructions:
        additions.append(f"\n\n⚠️ REGOLE SPECIALI FORNITORE '{rule_name}':")
        additions.append(instructions)
    
    # Aggiungi override specifici
    overrides = rule.get("overrides", {})
    if overrides.get("totale_kg_mode") == "sum_rows":
        additions.append("\n⚠️ OVERRIDE: Il totale_kg NON è presente nel documento. DEVI calcolarlo come SOMMA dei KG di tutte le righe presenti nel DDT.")
    
    if overrides.get("multipage"):
        additions.append("\n⚠️ OVERRIDE: Questo documento può essere multipagina. Assicurati di estrarre dati da tutte le pagine se necessario.")
    
    return "\n".join(additions)

