import time
from watchdog.observers import Observer
from watchdog.events import FileSystemEventHandler
from app.extract import extract_from_pdf
from app.excel import append_to_excel
from app.config import INBOX_DIR

class DDTHandler(FileSystemEventHandler):
    def on_created(self, event):
        if event.src_path.lower().endswith(".pdf"):
            print(f"📄 Nuovo DDT rilevato: {event.src_path}")
            try:
                data = extract_from_pdf(event.src_path)
                append_to_excel(data)
                print("✅ Inserito in Excel:", data)
            except Exception as e:
                print("❌ Errore nel parsing:", e)

def start_watcher():
    observer = Observer()
    observer.schedule(DDTHandler(), INBOX_DIR, recursive=False)
    observer.start()
    print("👀 Watchdog attivo su /inbox")

    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        observer.stop()
    observer.join()

if __name__ == "__main__":
    start_watcher()

