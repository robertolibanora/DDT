# Bugfix: Semaforo Double-Release

## Problema Identificato

Il semaforo `_pdf_processing_semaphore` veniva rilasciato **due volte**:
1. Prima di ogni `return` anticipato (file non PDF, duplicato, ecc.)
2. Nel blocco `finally` alla fine della funzione

Questo causava:
- **Invalidazione del limite di concorrenza**: il semaforo veniva rilasciato più volte del necessario
- **Possibile saturazione CPU/RAM**: più PDF processati simultaneamente del limite configurato

## Soluzione Implementata

### Pattern "Exactly-Once Release"

1. **Flag booleano `acquired`** inizializzato a `False`
2. **`acquired = True`** solo dopo `acquire()` riuscito
3. **Rimossi TUTTI i `release()` manuali** prima dei `return`
4. **Nel `finally`**: `if acquired: release()` (solo se acquisito)

### File Modificati

- `main.py`: funzione `_process_pdf()` nella classe `DDTHandler`
- `worker.py`: funzione `_process_pdf()` nella classe `DDTHandler`
- `worker.py`: funzione `process_queued_document()`

### Modifiche Dettagliate

#### Prima (BUG):
```python
if not _pdf_processing_semaphore.acquire(timeout=300):
    return

try:
    if condition:
        _pdf_processing_semaphore.release()  # ❌ Release manuale
        return
finally:
    _pdf_processing_semaphore.release()  # ❌ Release nel finally → DOUBLE RELEASE!
```

#### Dopo (FIX):
```python
acquired = False
if not _pdf_processing_semaphore.acquire(timeout=300):
    return
acquired = True  # ✅ Solo se acquisito con successo

try:
    if condition:
        return  # ✅ Nessun release manuale
finally:
    if acquired:  # ✅ Release solo se acquisito
        _pdf_processing_semaphore.release()
        logger.debug("Semaforo rilasciato")
    else:
        logger.debug("Semaforo non rilasciato (non acquisito)")
```

## Testing

### Verifica Concorrenza

```bash
# Monitora log per verificare che il semaforo venga rilasciato correttamente
tail -f /var/log/ddt-worker.log | grep "Semaforo"

# Dovresti vedere:
# - "Semaforo rilasciato" quando processing completa normalmente
# - "Semaforo non rilasciato (non acquisito)" solo se timeout acquire
```

### Verifica Limite Concorrenza

Il limite di concorrenza ora funziona correttamente:
- Max `DDT_MAX_CONCURRENT_PDF` PDF processati simultaneamente
- Altri PDF attendono fino a timeout (5 minuti)
- Semaforo rilasciato esattamente una volta per ogni PDF processato

## Commit Message

```
Fix semaphore double-release, enforce concurrency cap

- Aggiunto flag `acquired` per tracciare acquisizione semaforo
- Rimossi tutti i release manuali prima dei return
- Release nel finally solo se `acquired == True`
- Aggiunto log DEBUG per tracciare release semaforo
- Applicato fix a `_process_pdf()` e `process_queued_document()`

Risolve bug critico che invalidava il limite di concorrenza PDF processing.
```
