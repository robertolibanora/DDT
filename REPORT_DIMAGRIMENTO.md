# Report Diagnosi Dimagrimento DDT

## Problemi Identificati

### 1. STARTUP PESANTE

#### 1.1 Import pesanti a livello modulo (RISOLTO - già lazy)
- ✅ `fitz` (PyMuPDF) importato dentro funzioni in `extract.py`
- ✅ `pdfplumber` importato dentro funzioni in `layout_rules/manager.py`
- ✅ `openpyxl` importato a livello modulo in `excel.py` (necessario per Workbook/load_workbook)

#### 1.2 Caricamento all'avvio
- ⚠️ **Layout rules caricati all'avvio** (`main.py:533`, `worker.py:605`)
  - File: `app/layout_rules/manager.py:149`
  - Impatto: Carica JSON e crea oggetti LayoutRule per tutti i modelli
  - Soluzione: Lazy loading con cache (già presente ma viene chiamato all'avvio)

- ⚠️ **Watchdog queue caricata all'avvio** (`main.py:569`, `worker.py:638`)
  - File: `app/watchdog_queue.py`
  - Impatto: Carica JSON completo in memoria
  - Soluzione: Lazy loading

- ⚠️ **Global config caricato all'avvio** (implicito)
  - File: `app/global_config.py:31`
  - Impatto: Legge JSON e crea cache
  - Soluzione: Lazy loading (già presente ma cache inizializzata)

#### 1.3 Operazioni I/O all'avvio
- ⚠️ **Migrazione stati all'avvio** (`main.py:517`, `worker.py:593`)
  - File: `app/processed_documents.py`
  - Impatto: Legge e scrive JSON completo
  - Soluzione: Eseguire solo se necessario (controllo timestamp)

### 2. RUNTIME PESANTE

#### 2.1 Operazioni I/O ripetute senza cache
- 🔴 **`read_excel_as_dict()` chiamato ripetutamente**
  - File: `app/excel.py:309`
  - Impatto: Legge tutto il file Excel ogni volta (può essere grande)
  - Chiamato da:
    - `main.py:344` (controllo duplicati durante processing)
    - `main.py:1043` (endpoint `/data`)
    - `worker.py:137` (controllo duplicati)
  - Soluzione: Cache con invalidazione su mtime

- ⚠️ **`load_layout_rules()` chiamato più volte**
  - File: `app/layout_rules/manager.py:149`
  - Impatto: Rilegge JSON se cache invalidata
  - Soluzione: Cache già presente, migliorare invalidazione

#### 2.2 Operazioni sincrone in request handler
- ✅ Operazioni pesanti già in thread separati (watchdog)
- ✅ Excel operations già thread-safe con lock

#### 2.3 Logging eccessivo
- ⚠️ **Troppi log INFO per operazioni normali**
  - File: `app/extract.py`, `app/layout_rules/manager.py`, `main.py`
  - Impatto: I/O continuo su disco/stdout
  - Soluzione: Ridurre a DEBUG per operazioni frequenti

### 3. MEMORIA

#### 3.1 Caricamento completo file in memoria
- ⚠️ **PDF caricato completamente in memoria** (`main.py:323`, `worker.py:114`)
  - File: `main.py:323`, `worker.py:114`
  - Impatto: File grandi occupano molta RAM
  - Soluzione: Streaming dove possibile (limitato da API OpenAI che richiede base64)

- ⚠️ **Excel caricato completamente** (`app/excel.py:309`)
  - Impatto: File Excel grandi occupano RAM
  - Soluzione: Cache con TTL, lettura incrementale se possibile

#### 3.2 Cache non invalidate
- ⚠️ **Layout rules cache** (già presente ma può essere migliorata)
- ❌ **Excel cache** (non presente)

### 4. CONCORRENZA

#### 4.1 Thread multipli senza limiti
- ✅ Watchdog usa thread daemon (OK)
- ⚠️ Processing PDF in thread separati senza semaforo
  - File: `main.py:422`, `worker.py:209`
  - Impatto: Troppi PDF processati simultaneamente possono saturare CPU/RAM
  - Soluzione: Limite concorrenza con semaforo

### 5. ERRORI DI DESIGN

#### 5.1 Web server che fa lavoro da worker
- ✅ Già separato: web non processa PDF (solo upload)

#### 5.2 Operazioni bloccanti
- ✅ Già in thread separati

## Piano di Dimagrimento

### A. Quick Wins (0-2 ore)

1. **Ridurre logging verbosity**
   - Cambiare log INFO → DEBUG per operazioni frequenti
   - File: `app/extract.py`, `app/layout_rules/manager.py`, `main.py`

2. **Cache Excel con invalidazione**
   - Aggiungere cache con TTL per `read_excel_as_dict()`
   - File: `app/excel.py`

3. **Lazy loading layout rules**
   - Non caricare all'avvio, solo quando necessario
   - File: `main.py`, `worker.py`

4. **Lazy loading watchdog queue**
   - Non caricare all'avvio, solo quando necessario
   - File: `main.py`, `worker.py`

### B. Refactor Medio (1-2 giorni)

1. **Limite concorrenza processing PDF**
   - Semaforo per limitare PDF processati simultaneamente
   - File: `main.py`, `worker.py`

2. **Cache intelligente Excel**
   - Cache con invalidazione su mtime
   - File: `app/excel.py`

3. **Ottimizzazione migrazione stati**
   - Eseguire solo se necessario (controllo timestamp)
   - File: `app/processed_documents.py`

4. **Health/Ready endpoints**
   - Endpoint `/health` (solo check cheap)
   - Endpoint `/ready` (dipendenze pronte)
   - File: `main.py`

### C. Hardening (3-7 giorni)

1. **Metriche e monitoring**
   - Tempo per file, memoria stimata, code length
   - File: nuovo modulo `app/metrics.py`

2. **Retry e circuit breaker**
   - Per dipendenze instabili (OpenAI API)
   - File: `app/extract.py`

3. **Backpressure**
   - Limite code processing, rifiuta nuovi file se saturo
   - File: `main.py`, `worker.py`

4. **Profiling hook**
   - cProfile/py-spy attivabile via env var
   - File: nuovo modulo `app/profiling.py`

## Checklist Deploy

### Systemd (web)
```ini
[Service]
Type=notify
ExecStart=/usr/bin/uvicorn main:app --host 0.0.0.0 --port 8000 --workers 2 --timeout-keep-alive 30 --timeout-graceful-shutdown 10
Restart=on-failure
RestartSec=5
MemoryLimit=512M
CPUQuota=50%
```

### Systemd (worker)
```ini
[Service]
Type=simple
ExecStart=/usr/bin/python3 worker.py
Restart=on-failure
RestartSec=5
MemoryLimit=1G
CPUQuota=75%
```

### Uvicorn/Gunicorn
- Workers: 2 (web), 1 (worker)
- Timeout: 30s keep-alive, 10s graceful shutdown
- Memory limit: 512M (web), 1G (worker)
