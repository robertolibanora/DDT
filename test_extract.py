"""
Script di test per testare l'estrazione DDT
Uso: python test_extract.py <percorso_file.pdf>
"""
import sys
import os

# Aggiungi la directory root al path Python
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from app.extract import extract_from_pdf

if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Uso: python test_extract.py <percorso_file.pdf>")
        sys.exit(1)
    
    pdf_path = sys.argv[1]
    
    if not os.path.exists(pdf_path):
        print(f"Errore: File non trovato: {pdf_path}")
        sys.exit(1)
    
    if not pdf_path.lower().endswith('.pdf'):
        print("Errore: Il file deve essere un PDF")
        sys.exit(1)
    
    print(f"📄 Estrazione dati da: {pdf_path}")
    print("⏳ Elaborazione in corso...\n")
    
    try:
        data = extract_from_pdf(pdf_path)
        print("✅ Estrazione completata con successo!\n")
        print("📋 Dati estratti:")
        print(f"  Data: {data.get('data', 'N/A')}")
        print(f"  Mittente: {data.get('mittente', 'N/A')}")
        print(f"  Destinatario: {data.get('destinatario', 'N/A')}")
        print(f"  Numero Documento: {data.get('numero_documento', 'N/A')}")
        print(f"  Totale Kg: {data.get('totale_kg', 'N/A')}")
    except Exception as e:
        print(f"❌ Errore durante l'estrazione: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)

