"""
Sistema di gestione correzioni e apprendimento automatico
Salva le correzioni manuali e le usa per migliorare l'estrazione futura
Crea automaticamente regole quando i pattern vengono riconosciuti più volte
"""
import json
import logging
import hashlib
import os
from typing import Dict, Any, Optional, List
from pathlib import Path
from datetime import datetime

logger = logging.getLogger(__name__)

CORRECTIONS_FILE = Path("app/corrections/corrections.json")
CORRECTIONS_DIR = Path("app/corrections")

# Soglia per creazione automatica regole (numero di correzioni simili)
AUTO_RULE_THRESHOLD = 5

# Cache delle correzioni
_corrections_cache: Optional[Dict[str, Any]] = None


def _ensure_corrections_dir():
    """Assicura che la directory delle correzioni esista"""
    CORRECTIONS_DIR.mkdir(parents=True, exist_ok=True)


def _load_corrections() -> Dict[str, Any]:
    """
    Carica le correzioni dal file JSON
    
    Returns:
        Dizionario con tutte le correzioni
    """
    global _corrections_cache
    
    if _corrections_cache is not None:
        return _corrections_cache
    
    _ensure_corrections_dir()
    
    if not CORRECTIONS_FILE.exists():
        logger.info(f"File correzioni non trovato, creo {CORRECTIONS_FILE} vuoto")
        _corrections_cache = {
            "corrections": {},
            "learning_patterns": {},
            "auto_rules_created": []  # Traccia le regole create automaticamente
        }
        _save_corrections(_corrections_cache)
        return _corrections_cache
    
    try:
        with open(CORRECTIONS_FILE, 'r', encoding='utf-8') as f:
            _corrections_cache = json.load(f)
        
        # Assicura che la struttura sia corretta
        if "corrections" not in _corrections_cache:
            _corrections_cache["corrections"] = {}
        if "learning_patterns" not in _corrections_cache:
            _corrections_cache["learning_patterns"] = {}
        if "auto_rules_created" not in _corrections_cache:
            _corrections_cache["auto_rules_created"] = []
        
        logger.info(f"Caricate {len(_corrections_cache.get('corrections', {}))} correzioni")
        return _corrections_cache
    except json.JSONDecodeError as e:
        logger.error(f"Errore parsing JSON correzioni: {e}")
        _corrections_cache = {"corrections": {}, "learning_patterns": {}, "auto_rules_created": []}
        return _corrections_cache
    except Exception as e:
        logger.error(f"Errore caricamento correzioni: {e}", exc_info=True)
        _corrections_cache = {"corrections": {}, "learning_patterns": {}, "auto_rules_created": []}
        return _corrections_cache


def _save_corrections(corrections: Dict[str, Any]) -> None:
    """
    Salva le correzioni nel file JSON
    
    Args:
        corrections: Dizionario con tutte le correzioni
    """
    global _corrections_cache
    _corrections_cache = corrections
    
    _ensure_corrections_dir()
    
    try:
        with open(CORRECTIONS_FILE, 'w', encoding='utf-8') as f:
            json.dump(corrections, f, indent=2, ensure_ascii=False)
        logger.info(f"Correzioni salvate in {CORRECTIONS_FILE}")
    except Exception as e:
        logger.error(f"Errore salvataggio correzioni: {e}", exc_info=True)
        raise


def get_file_hash(file_path: str) -> str:
    """
    Calcola l'hash MD5 di un file per identificarlo univocamente
    
    Args:
        file_path: Percorso del file
        
    Returns:
        Hash MD5 del file
    """
    try:
        with open(file_path, 'rb') as f:
            file_hash = hashlib.md5(f.read()).hexdigest()
        return file_hash
    except Exception as e:
        logger.warning(f"Errore calcolo hash file {file_path}: {e}")
        # Fallback: usa il nome del file
        return hashlib.md5(file_path.encode()).hexdigest()


def _create_auto_rule_from_pattern(pattern_data: Dict[str, Any], corrected_data: Dict[str, Any]) -> Optional[str]:
    """
    Crea automaticamente una regola quando un pattern viene riconosciuto più volte
    
    Args:
        pattern_data: Dati del pattern riconosciuto
        corrected_data: Dati corretti dell'ultima correzione
        
    Returns:
        Nome della regola creata o None se non creata
    """
    try:
        from app.rules.rules import add_rule, get_rule, reload_rules
        
        corrections_data = _load_corrections()
        auto_rules = corrections_data.setdefault("auto_rules_created", [])
        
        # Estrai informazioni per creare la regola
        field = pattern_data.get("field")
        original_pattern = pattern_data.get("original_pattern", "")
        corrected_value = pattern_data.get("corrected_value", "")
        mittente_pattern = pattern_data.get("mittente_pattern", corrected_data.get("mittente", ""))
        
        # Crea un nome regola basato sul mittente o sul pattern
        if mittente_pattern:
            # Usa il mittente come nome regola (primi 30 caratteri)
            rule_name = mittente_pattern[:30].strip()
            # Pulisci caratteri speciali
            rule_name = "".join(c if c.isalnum() or c in " .-_" else "_" for c in rule_name)
            rule_name = rule_name.strip() or "Regola_Auto"
        else:
            rule_name = f"Regola_Auto_{field}_{original_pattern[:20]}"
        
        # Verifica se la regola esiste già
        existing_rule = get_rule(rule_name)
        if existing_rule:
            logger.debug(f"Regola '{rule_name}' già esistente, non creo duplicato")
            return None
        
        # Verifica se questa regola è già stata creata automaticamente
        if rule_name in auto_rules:
            logger.debug(f"Regola automatica '{rule_name}' già creata precedentemente")
            return None
        
        # Crea le keyword per il rilevamento basate sul mittente
        detect_keywords = []
        if mittente_pattern:
            # Estrai parole chiave dal nome mittente (prime 2-3 parole significative)
            words = mittente_pattern.split()
            if len(words) >= 2:
                detect_keywords.append(" ".join(words[:2]))  # Prime 2 parole
                if len(words) >= 3:
                    detect_keywords.append(words[0])  # Prima parola sola
            else:
                detect_keywords.append(mittente_pattern)
        
        # Se non abbiamo keyword dal mittente, usa il pattern originale
        if not detect_keywords and original_pattern:
            detect_keywords.append(original_pattern[:30])
        
        if not detect_keywords:
            logger.warning(f"Impossibile creare regola automatica: nessuna keyword disponibile")
            return None
        
        # Crea le istruzioni basate sul pattern di correzione
        instructions_parts = []
        
        if field == "mittente":
            instructions_parts.append(f"Il campo mittente viene spesso estratto come '{original_pattern}' ma deve essere '{corrected_value}'.")
        elif field == "destinatario":
            instructions_parts.append(f"Il campo destinatario viene spesso estratto come '{original_pattern}' ma deve essere '{corrected_value}'.")
        elif field == "numero_documento":
            instructions_parts.append(f"Il numero documento viene spesso estratto come '{original_pattern}' ma deve essere '{corrected_value}'.")
        
        instructions_parts.append(f"Assicurati di usare il formato corretto: '{corrected_value}'.")
        
        instructions = " ".join(instructions_parts)
        
        # Crea la regola
        rule_data = {
            "detect": detect_keywords,
            "instructions": instructions,
            "overrides": {}
        }
        
        # Salva la regola
        add_rule(rule_name, rule_data)
        reload_rules()
        
        # Marca come creata automaticamente
        auto_rules.append(rule_name)
        _save_corrections(corrections_data)
        
        logger.info(f"✅ Regola automatica creata: '{rule_name}' (pattern riconosciuto {pattern_data.get('count')} volte)")
        return rule_name
        
    except Exception as e:
        logger.error(f"Errore creazione regola automatica: {e}", exc_info=True)
        return None


def save_correction(file_path: str, original_data: Dict[str, Any], corrected_data: Dict[str, Any]) -> str:
    """
    Salva una correzione manuale e crea regole automatiche se necessario
    
    Args:
        file_path: Percorso del file PDF originale
        original_data: Dati estratti originalmente dall'AI
        corrected_data: Dati corretti manualmente dall'utente
        
    Returns:
        ID della correzione salvata
    """
    corrections_data = _load_corrections()
    
    file_hash = get_file_hash(file_path) if os.path.exists(file_path) else hashlib.md5(file_path.encode()).hexdigest()
    file_name = os.path.basename(file_path)
    
    correction_id = f"{file_hash}_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
    
    correction_entry = {
        "file_path": file_path,
        "file_name": file_name,
        "file_hash": file_hash,
        "original_data": original_data,
        "corrected_data": corrected_data,
        "timestamp": datetime.now().isoformat(),
        "fields_changed": []
    }
    
    # Identifica quali campi sono stati modificati
    for key in original_data.keys():
        if original_data.get(key) != corrected_data.get(key):
            correction_entry["fields_changed"].append(key)
    
    corrections_data["corrections"][correction_id] = correction_entry
    
    # Aggiorna i pattern di apprendimento (questo può creare regole automatiche)
    _update_learning_patterns(corrections_data, original_data, corrected_data, file_hash)
    
    _save_corrections(corrections_data)
    logger.info(f"Correzione salvata: {correction_id} ({len(correction_entry['fields_changed'])} campi modificati)")
    
    return correction_id


def _update_learning_patterns(
    corrections_data: Dict[str, Any],
    original_data: Dict[str, Any],
    corrected_data: Dict[str, Any],
    file_hash: str
) -> None:
    """
    Aggiorna i pattern di apprendimento basandosi sulle correzioni
    e crea automaticamente regole quando i pattern sono riconosciuti più volte
    
    Args:
        corrections_data: Dizionario delle correzioni
        original_data: Dati originali estratti
        corrected_data: Dati corretti
        file_hash: Hash del file
    """
    patterns = corrections_data.setdefault("learning_patterns", {})
    
    # Per ogni campo modificato, salva il pattern di correzione
    for field in ["mittente", "destinatario", "numero_documento"]:
        if original_data.get(field) != corrected_data.get(field):
            original_value = original_data.get(field, "").lower().strip()
            corrected_value = corrected_data.get(field, "").strip()
            
            if original_value and corrected_value:
                pattern_key = f"{field}_{original_value}"
                if pattern_key not in patterns:
                    patterns[pattern_key] = {
                        "field": field,
                        "original_pattern": original_value,
                        "corrected_value": corrected_value,
                        "count": 0,
                        "files": [],
                        "mittente_pattern": corrected_data.get("mittente", "").strip()  # Traccia il mittente per creare regole
                    }
                
                patterns[pattern_key]["count"] += 1
                if file_hash not in patterns[pattern_key]["files"]:
                    patterns[pattern_key]["files"].append(file_hash)
                
                # Se il pattern è stato riconosciuto abbastanza volte, crea una regola automatica
                if patterns[pattern_key]["count"] >= AUTO_RULE_THRESHOLD:
                    rule_name = _create_auto_rule_from_pattern(patterns[pattern_key], corrected_data)
                    if rule_name:
                        # Marca il pattern come utilizzato per creare una regola
                        patterns[pattern_key]["rule_created"] = rule_name


def get_learning_suggestions(extracted_data: Dict[str, Any]) -> Dict[str, str]:
    """
    Ottiene suggerimenti di correzione basati su correzioni precedenti
    
    Args:
        extracted_data: Dati estratti dall'AI
        
    Returns:
        Dizionario con suggerimenti per ogni campo (se disponibili)
    """
    corrections_data = _load_corrections()
    patterns = corrections_data.get("learning_patterns", {})
    suggestions = {}
    
    # Controlla ogni campo per pattern simili
    for field in ["mittente", "destinatario", "numero_documento"]:
        field_value = extracted_data.get(field, "").lower().strip()
        if not field_value:
            continue
        
        # Cerca pattern che corrispondono
        for pattern_key, pattern_data in patterns.items():
            if pattern_data["field"] == field:
                original_pattern = pattern_data.get("original_pattern", "").lower().strip()
                
                # Se il valore estratto corrisponde al pattern originale
                if field_value == original_pattern or field_value in original_pattern or original_pattern in field_value:
                    corrected_value = pattern_data.get("corrected_value", "")
                    count = pattern_data.get("count", 0)
                    
                    # Usa il suggerimento se è stato applicato almeno 2 volte
                    if count >= 2 and corrected_value:
                        suggestions[field] = corrected_value
                        logger.info(f"Suggerimento apprendimento per {field}: '{field_value}' -> '{corrected_value}' (usato {count} volte)")
                        break
    
    return suggestions


def apply_learning_suggestions(extracted_data: Dict[str, Any]) -> Dict[str, Any]:
    """
    Applica automaticamente i suggerimenti di apprendimento ai dati estratti
    
    Args:
        extracted_data: Dati estratti dall'AI
        
    Returns:
        Dati con suggerimenti applicati
    """
    suggestions = get_learning_suggestions(extracted_data)
    corrected_data = extracted_data.copy()
    
    for field, suggested_value in suggestions.items():
        if field in corrected_data:
            logger.info(f"Applicato suggerimento automatico per {field}: '{corrected_data[field]}' -> '{suggested_value}'")
            corrected_data[field] = suggested_value
    
    return corrected_data


def get_correction_history(file_hash: Optional[str] = None, limit: int = 50) -> List[Dict[str, Any]]:
    """
    Ottiene la cronologia delle correzioni
    
    Args:
        file_hash: Hash del file per filtrare (opzionale)
        limit: Numero massimo di correzioni da restituire
        
    Returns:
        Lista di correzioni ordinate per timestamp (più recenti prima)
    """
    corrections_data = _load_corrections()
    corrections = corrections_data.get("corrections", {})
    
    history = []
    for correction_id, correction in corrections.items():
        if file_hash and correction.get("file_hash") != file_hash:
            continue
        
        history.append({
            "id": correction_id,
            **correction
        })
    
    # Ordina per timestamp (più recenti prima)
    history.sort(key=lambda x: x.get("timestamp", ""), reverse=True)
    
    return history[:limit]


def get_auto_rules_created() -> List[str]:
    """
    Ottiene la lista delle regole create automaticamente
    
    Returns:
        Lista dei nomi delle regole create automaticamente
    """
    corrections_data = _load_corrections()
    return corrections_data.get("auto_rules_created", [])


def reload_corrections_cache():
    """Ricarica la cache delle correzioni"""
    global _corrections_cache
    _corrections_cache = None
    _load_corrections()
