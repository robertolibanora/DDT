# DDT Processor

Sistema automatico per l'estrazione e la gestione dei DDT (Documenti di Trasporto) da file PDF.

## Funzionalità

- 📄 Upload manuale di DDT PDF tramite interfaccia web
- 🤖 Estrazione automatica dei dati tramite OpenAI
- 📊 Dashboard in tempo reale con aggiornamento automatico
- 👀 Watchdog per monitorare automaticamente la cartella inbox
- 💾 Salvataggio dei dati in file Excel

## Struttura del Progetto

```
DDT/
├── app/
│   ├── __init__.py
│   ├── config.py          # Configurazioni
│   ├── excel.py           # Gestione file Excel
│   ├── extract.py         # Estrazione dati da PDF
│   ├── watcher.py         # Watchdog per file inbox
│   └── templates/
│       ├── dashboard.html # Dashboard DDT
│       └── upload.html    # Pagina upload
├── inbox/                 # Cartella per i PDF in ingresso
├── main.py                # Applicazione FastAPI
├── requirements.txt       # Dipendenze Python
├── ddt.xlsx              # File Excel con i dati
└── .env                  # Variabili d'ambiente (creare)
```

## Installazione

1. Clonare il repository
2. Creare un virtual environment:
   ```bash
   python -m venv venv
   source venv/bin/activate  # Su Windows: venv\Scripts\activate
   ```
3. Installare le dipendenze:
   ```bash
   pip install -r requirements.txt
   ```
4. Creare il file `.env` con:
   ```
   OPENAI_API_KEY=your_api_key_here
   MODEL=gpt-4o-mini
   ```

## Utilizzo

### Avviare il server web
```bash
uvicorn main:app --reload
```

Poi aprire il browser su:
- http://127.0.0.1:8000 - Upload manuale DDT
- http://127.0.0.1:8000/dashboard - Dashboard con tutti i DDT

### Avviare il watchdog (monitoraggio automatico)
```bash
python -m app.watcher
```

Il watchdog monitora la cartella `inbox/` e processa automaticamente tutti i PDF che vengono aggiunti.

## Endpoint API

- `GET /` - Pagina upload DDT
- `POST /upload` - Upload di un file DDT PDF
- `GET /dashboard` - Dashboard con tutti i DDT
- `GET /data` - API JSON con tutti i dati dei DDT

## Dati estratti

Ogni DDT viene processato per estrarre:
- Data
- Mittente
- Destinatario
- Numero documento
- Totale kg

I dati vengono salvati automaticamente in `ddt.xlsx`.

