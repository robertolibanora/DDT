# 📦 DDT Extractor

Sistema avanzato per l'estrazione automatica e la gestione intelligente dei DDT (Documenti di Trasporto) da file PDF, con supporto per regole dinamiche personalizzabili.

## ✨ Funzionalità

- 📄 **Upload Manuale**: Carica DDT PDF tramite interfaccia web intuitiva
- 🤖 **Estrazione AI**: Estrazione automatica dei dati tramite OpenAI Vision
- 📊 **Dashboard in Tempo Reale**: Visualizzazione con aggiornamento automatico ogni 3 secondi
- 👀 **Monitoraggio Automatico**: Watchdog integrato che monitora la cartella `inbox/` e processa i PDF automaticamente
- 💾 **Salvataggio Excel**: Dati salvati automaticamente in file Excel
- ⚙️ **Sistema di Regole Dinamiche**: Crea e gestisci regole personalizzate per migliorare l'estrazione per fornitori specifici
- 🔄 **Riprocessing**: Riprocessa DDT esistenti con regole aggiornate
- 🎨 **Interfaccia Moderna**: UI/UX con tema mare, animazioni fluide e design responsive

## 🏗️ Struttura del Progetto

```
DDT/
├── app/
│   ├── __init__.py
│   ├── config.py              # Configurazioni e variabili d'ambiente
│   ├── excel.py               # Gestione file Excel
│   ├── extract.py             # Estrazione dati da PDF con OpenAI
│   ├── models.py              # Modelli Pydantic per validazione
│   ├── utils.py               # Utility per normalizzazione dati
│   ├── logging_config.py      # Configurazione logging
│   ├── rules/                 # Sistema di regole dinamiche
│   │   ├── rules.json         # File JSON con le regole
│   │   └── rules.py           # Gestione regole
│   ├── routers/               # Router FastAPI
│   │   ├── rules_router.py    # API per gestione regole
│   │   └── reprocess_router.py # API per reprocessing
│   ├── static/
│   │   └── css/
│   │       └── main.css       # CSS unificato con tema mare
│   └── templates/
│       ├── base.html          # Template base
│       ├── dashboard.html     # Dashboard DDT
│       ├── upload.html        # Pagina upload
│       └── rules.html         # Gestione regole
├── inbox/                     # Cartella per i PDF in ingresso (monitorata automaticamente)
├── main.py                    # Applicazione FastAPI principale
├── requirements.txt           # Dipendenze Python
├── ddt.xlsx                   # File Excel con i dati estratti (generato automaticamente)
└── .env                       # Variabili d'ambiente (creare manualmente)
```

## 🚀 Installazione

### Prerequisiti

- Python 3.8 o superiore
- Chiave API OpenAI valida

### Passo 1: Clonare il repository

```bash
git clone <repository-url>
cd DDT
```

### Passo 2: Creare un virtual environment

```bash
python3 -m venv venv
```

Attivare il virtual environment:

- **Linux/Mac:**
  ```bash
  source venv/bin/activate
  ```

- **Windows:**
  ```bash
  venv\Scripts\activate
  ```

### Passo 3: Installare le dipendenze

```bash
pip install -r requirements.txt
```

**Nota**: Se usi `pdf2image`, potrebbe essere necessario installare anche `poppler`:

- **Linux (Ubuntu/Debian):**
  ```bash
  sudo apt-get install poppler-utils
  ```

- **Mac:**
  ```bash
  brew install poppler
  ```

- **Windows:**
  Scarica e installa da [poppler-windows](https://github.com/oschwartz10612/poppler-windows/releases)

### Passo 4: Configurare le variabili d'ambiente

Crea un file `.env` nella root del progetto con il seguente contenuto:

```env
OPENAI_API_KEY=your_openai_api_key_here
MODEL=gpt-4o-mini
UVICORN_PORT=8000
```

**Importante**: Sostituisci `your_openai_api_key_here` con la tua chiave API OpenAI valida.

## 🎯 Utilizzo

### Avvio dell'applicazione

Una volta completata l'installazione, avvia il server con:

```bash
python3 main.py
```

Il server si avvierà e mostrerà le informazioni di connessione:

```
============================================================
🚀 Server FastAPI avviato
============================================================
📍 Host: 0.0.0.0 (tutte le interfacce)
🌐 IP Locale: 192.168.x.x
🔌 Porta: 8000
🔗 URL Locale: http://127.0.0.1:8000
🔗 URL Rete: http://192.168.x.x:8000
============================================================
```

### Accesso all'interfaccia web

Apri il browser e naviga a:

- **Dashboard**: http://127.0.0.1:8000 o http://localhost:8000
- **Upload DDT**: http://127.0.0.1:8000/upload
- **Gestione Regole**: http://127.0.0.1:8000/rules

### Monitoraggio automatico

Il sistema include un **watchdog integrato** che monitora automaticamente la cartella `inbox/`. 

1. Copia o sposta i file PDF nella cartella `inbox/`
2. I file vengono processati automaticamente
3. I dati estratti vengono salvati in `ddt.xlsx`
4. La dashboard si aggiorna automaticamente ogni 3 secondi

## 📋 Dati Estratti

Ogni DDT viene processato per estrarre:

- **Data**: Data del documento (formato YYYY-MM-DD)
- **Mittente**: Nome dell'azienda mittente
- **Destinatario**: Nome dell'azienda destinataria
- **Numero Documento**: Numero identificativo del DDT
- **Totale KG**: Peso totale in chilogrammi

I dati vengono validati e normalizzati prima di essere salvati in Excel.

## ⚙️ Sistema di Regole Dinamiche

Il sistema supporta regole personalizzate per migliorare l'estrazione per fornitori specifici.

### Come funziona

1. **Rilevamento automatico**: Quando un PDF viene processato, il sistema estrae il testo e cerca keyword definite nelle regole
2. **Applicazione regole**: Se trova una corrispondenza, applica le istruzioni personalizzate all'AI
3. **Miglioramento continuo**: Puoi aggiungere/modificare regole direttamente dal frontend

### Creare una regola

1. Vai alla pagina **Regole** (http://127.0.0.1:8000/rules)
2. Clicca su **"➕ Aggiungi Nuova Regola"**
3. Inserisci:
   - **Nome regola**: Nome identificativo (es: "Fornitore XYZ")
   - **Keyword**: Parole chiave per il rilevamento, separate da virgola (es: "XYZ, Società ABC")
   - **Istruzioni AI**: Istruzioni testuali personalizzate per l'estrazione
   - **Override**: Opzioni speciali (multipagina, calcolo somma righe, ecc.)
4. Salva la regola

### Esempio di regola

```
Nome: DEVA
Keyword: DEVA, Armanini, Società Semplice Agricola
Istruzioni: Il totale non è presente. Calcola somma dei KG delle righe.
Override: 
  - Calcola totale_kg come somma delle righe ✓
  - Documento multipagina ✓
```

## 🔄 Riprocessing DDT

Puoi riprocessare un DDT esistente usando le regole aggiornate:

1. Vai alla pagina **Regole**
2. Inserisci il numero documento del DDT da riprocessare
3. Clicca su **"Riprocessa"**
4. Il sistema riestrae i dati con le regole aggiornate e aggiorna il file Excel

## 📡 Endpoint API

### Interfaccia Web

- `GET /` - Dashboard principale
- `GET /dashboard` - Dashboard con tutti i DDT
- `GET /upload` - Pagina upload DDT
- `GET /rules` - Pagina gestione regole

### API JSON

- `POST /upload` - Upload di un file DDT PDF
  ```json
  {
    "status": "ok",
    "estratti": {
      "data": "2024-11-27",
      "mittente": "ACME S.r.l.",
      "destinatario": "Mario Rossi & C.",
      "numero_documento": "DDT-12345",
      "totale_kg": 1250.5
    }
  }
  ```

- `GET /data` - Ottieni tutti i DDT in formato JSON
  ```json
  {
    "rows": [
      {
        "data": "2024-11-27",
        "mittente": "ACME S.r.l.",
        "destinatario": "Mario Rossi & C.",
        "numero_documento": "DDT-12345",
        "totale_kg": "1250.5"
      }
    ]
  }
  ```

- `POST /data/clear` - Cancella tutti i DDT dal database

### API Regole

- `GET /api/rules` - Lista tutte le regole
- `POST /api/rules/add` - Aggiungi una nuova regola
- `PUT /api/rules/{name}` - Aggiorna una regola esistente
- `DELETE /api/rules/{name}` - Elimina una regola
- `POST /api/rules/reload` - Ricarica le regole dal file

### API Reprocessing

- `POST /reprocess/{numero_documento}` - Riprocessa un DDT specifico
- `POST /reprocess/by-file` - Riprocessa un DDT da percorso file

## 🛠️ Configurazione Avanzata

### Variabili d'ambiente

Nel file `.env` puoi configurare:

- `OPENAI_API_KEY` - **Obbligatorio**: La tua chiave API OpenAI
- `MODEL` - Modello OpenAI da usare (default: `gpt-4o-mini`)
- `UVICORN_PORT` - Porta del server (default: `8000`)

### Personalizzazione

- **Modificare regole**: File `app/rules/rules.json`
- **Modificare stili**: File `app/static/css/main.css`
- **Modificare template**: Cartella `app/templates/`

## 🐛 Risoluzione Problemi

### Il server non si avvia

- Verifica che tutte le dipendenze siano installate: `pip install -r requirements.txt`
- Controlla che il file `.env` esista e contenga `OPENAI_API_KEY`
- Assicurati che la porta 8000 non sia già in uso

### Errore durante l'estrazione

- Verifica che la chiave API OpenAI sia valida
- Controlla i log per dettagli sull'errore
- Assicurati che il PDF sia leggibile e non corrotto

### PDF non vengono processati automaticamente

- Verifica che la cartella `inbox/` esista nella root del progetto
- Controlla i permessi della cartella
- Verifica i log per eventuali errori

### Problemi con pdf2image

- Installa `poppler` (vedi sezione Installazione)
- Su Linux, potresti aver bisogno di: `sudo apt-get install poppler-utils`
- Su Mac: `brew install poppler`

## 📝 Note

- Il file Excel `ddt.xlsx` viene creato automaticamente al primo utilizzo
- La cartella `inbox/` viene creata automaticamente se non esiste
- Il sistema supporta solo file PDF
- I dati vengono validati con Pydantic prima del salvataggio
- Le regole vengono ricaricate automaticamente quando modificate

## 📄 Licenza

[Specificare la licenza del progetto]

## 👥 Contributi

[Eventuali informazioni su come contribuire al progetto]

## 🔗 Link Utili

- [OpenAI API Documentation](https://platform.openai.com/docs)
- [FastAPI Documentation](https://fastapi.tiangolo.com/)
- [Pydantic Documentation](https://docs.pydantic.dev/)

---

**Buon utilizzo! 🌊**
