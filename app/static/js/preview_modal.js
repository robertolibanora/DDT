/**
 * Sistema globale di anteprima DDT con modal
 * Gestisce l'anteprima per upload manuale e automatico (watchdog)
 */

class PreviewModal {
    constructor() {
        this.modal = null;
        this.currentData = null;
        this.currentFileHash = null;
        this.currentFileName = null;
        this.init();
    }

    init() {
        // Crea il modal se non esiste
        if (!document.getElementById('preview-modal')) {
            this.createModal();
        }
        this.modal = document.getElementById('preview-modal');
        if (!this.modal) {
            console.error('Errore: modal non trovato dopo la creazione');
            return;
        }
        this.setupEventListeners();
        console.log('PreviewModal inizializzato correttamente');
    }

    createModal() {
        const modalHTML = `
            <div id="preview-modal" class="preview-modal-overlay hidden">
                <div class="preview-modal-container">
                    <div class="preview-modal-header">
                        <h2>👁️ Anteprima DDT</h2>
                        <button class="preview-modal-close" id="preview-modal-close">✕</button>
                    </div>
                    
                    <div class="preview-modal-content">
                        <!-- PDF Preview Image -->
                        <div class="preview-pdf-section">
                            <h3>📄 Documento Scannerizzato</h3>
                            <div class="preview-pdf-container">
                                <img id="preview-pdf-image" src="" alt="Anteprima DDT" style="max-width: 100%; height: auto; display: block; margin: 0 auto;">
                            </div>
                        </div>

                        <!-- Form Dati -->
                        <div class="preview-form-section">
                            <h3>✏️ Verifica e Modifica Dati Estratti</h3>
                            <form id="preview-form">
                                <input type="hidden" id="preview-file-hash" name="file_hash">
                                <input type="hidden" id="preview-file-name" name="file_name">
                                <input type="hidden" id="preview-original-data" name="original_data">

                                <div class="form-group">
                                    <label for="preview-data">📅 Data DDT</label>
                                    <input type="date" id="preview-data" name="data" required>
                                </div>

                                <div class="form-group">
                                    <label for="preview-mittente">🏢 Mittente</label>
                                    <input type="text" id="preview-mittente" name="mittente" required>
                                </div>

                                <div class="form-group">
                                    <label for="preview-destinatario">📍 Destinatario</label>
                                    <input type="text" id="preview-destinatario" name="destinatario" required>
                                </div>

                                <div class="form-group">
                                    <label for="preview-numero-documento">🔢 Numero Documento</label>
                                    <input type="text" id="preview-numero-documento" name="numero_documento" required>
                                </div>

                                <div class="form-group">
                                    <label for="preview-totale-kg">⚖️ Totale Kg</label>
                                    <input type="number" id="preview-totale-kg" name="totale_kg" step="0.001" min="0" required>
                                </div>

                                <div class="preview-form-actions">
                                    <button type="submit" class="btn btn-success" id="preview-confirm-btn">
                                        ✅ Conferma e Salva
                                    </button>
                                    <button type="button" class="btn btn-secondary" id="preview-cancel-btn">
                                        ❌ Annulla
                                    </button>
                                </div>
                            </form>
                        </div>
                    </div>
                </div>
            </div>
        `;
        document.body.insertAdjacentHTML('beforeend', modalHTML);
        console.log('Modal HTML creato nel DOM');
        const createdModal = document.getElementById('preview-modal');
        if (createdModal) {
            console.log('Modal trovato nel DOM dopo creazione');
        } else {
            console.error('ERRORE: Modal NON trovato nel DOM dopo creazione!');
        }
    }

    setupEventListeners() {
        // Chiudi modal
        document.getElementById('preview-modal-close').addEventListener('click', () => this.hide());
        document.getElementById('preview-cancel-btn').addEventListener('click', () => this.hide());
        
        // Click fuori dal modal per chiudere
        this.modal.addEventListener('click', (e) => {
            if (e.target === this.modal) {
                this.hide();
            }
        });

        // Submit form
        document.getElementById('preview-form').addEventListener('submit', (e) => {
            e.preventDefault();
            this.saveData();
        });
    }

    show(extractedData, pdfBase64, fileHash, fileName) {
        this.currentData = extractedData;
        this.currentFileHash = fileHash;
        this.currentFileName = fileName;

        // Imposta immagine PNG di anteprima usando l'endpoint dedicato
        const imageUrl = `/preview/image/${fileHash}`;
        const imgElement = document.getElementById('preview-pdf-image');
        imgElement.src = imageUrl;
        imgElement.onerror = () => {
            console.error('Errore caricamento immagine anteprima');
            imgElement.alt = 'Errore caricamento anteprima';
        };

        // Imposta dati nel form
        document.getElementById('preview-file-hash').value = fileHash || '';
        document.getElementById('preview-file-name').value = fileName || '';
        document.getElementById('preview-original-data').value = JSON.stringify(extractedData);

        // Popola i campi
        document.getElementById('preview-data').value = extractedData.data || '';
        document.getElementById('preview-mittente').value = extractedData.mittente || '';
        document.getElementById('preview-destinatario').value = extractedData.destinatario || '';
        document.getElementById('preview-numero-documento').value = extractedData.numero_documento || '';
        // Formatta il peso con 3 decimali
        const kgValue = parseFloat(extractedData.totale_kg) || 0;
        document.getElementById('preview-totale-kg').value = kgValue.toFixed(3);

        // Mostra modal
        this.modal.classList.remove('hidden');
        document.body.style.overflow = 'hidden'; // Previeni scroll della pagina
        
        // Verifica che il modal sia effettivamente visibile
        console.log('Modal mostrato, classe hidden:', this.modal.classList.contains('hidden'));
        console.log('Modal display:', window.getComputedStyle(this.modal).display);
    }

    hide() {
        this.modal.classList.add('hidden');
        document.body.style.overflow = '';
        
        // Pulisci immagine per liberare memoria
        const imgElement = document.getElementById('preview-pdf-image');
        imgElement.src = '';
        
        this.currentData = null;
        this.currentFileHash = null;
        this.currentFileName = null;
    }

    base64ToBlob(base64, mimeType) {
        const byteCharacters = atob(base64);
        const byteNumbers = new Array(byteCharacters.length);
        for (let i = 0; i < byteCharacters.length; i++) {
            byteNumbers[i] = byteCharacters.charCodeAt(i);
        }
        const byteArray = new Uint8Array(byteNumbers);
        return new Blob([byteArray], { type: mimeType });
    }

    async saveData() {
        const formData = new FormData();
        formData.append('file_hash', document.getElementById('preview-file-hash').value);
        formData.append('file_name', document.getElementById('preview-file-name').value);
        formData.append('original_data', document.getElementById('preview-original-data').value);
        formData.append('data', document.getElementById('preview-data').value);
        formData.append('mittente', document.getElementById('preview-mittente').value);
        formData.append('destinatario', document.getElementById('preview-destinatario').value);
        formData.append('numero_documento', document.getElementById('preview-numero-documento').value);
        // Assicura che il peso abbia sempre 3 decimali
        const kgInput = document.getElementById('preview-totale-kg');
        const kgValue = parseFloat(kgInput.value) || 0;
        formData.append('totale_kg', kgValue.toFixed(3));

        const saveBtn = document.getElementById('preview-confirm-btn');
        const originalText = saveBtn.textContent;
        
        try {
            saveBtn.disabled = true;
            saveBtn.textContent = '⏳ Salvataggio...';

            const response = await fetch('/preview/save', {
                method: 'POST',
                body: formData,
                credentials: 'include'
            });

            const result = await response.json();

            if (!response.ok) {
                throw new Error(result.detail || 'Errore durante il salvataggio');
            }

            // Mostra messaggio di successo
            this.showMessage('✅ DDT salvato con successo! Le correzioni sono state apprese dal sistema.', 'success');
            
            // Chiudi modal dopo 1.5 secondi e aggiorna la pagina
            setTimeout(() => {
                this.hide();
                // Se siamo nella dashboard, ricarica i dati senza refresh completo
                if (window.location.pathname === '/dashboard' && typeof refreshData === 'function') {
                    refreshData();
                } else if (window.location.pathname === '/upload') {
                    // Se siamo nella pagina upload, mostra messaggio di successo
                    const messageDiv = document.getElementById('message');
                    if (messageDiv) {
                        messageDiv.textContent = '✅ DDT salvato con successo!';
                        messageDiv.className = 'message success';
                        messageDiv.classList.remove('hidden');
                        setTimeout(() => messageDiv.classList.add('hidden'), 5000);
                    }
                } else {
                    // Altrimenti ricarica la pagina
                    window.location.reload();
                }
            }, 1500);

        } catch (error) {
            console.error('Errore salvataggio:', error);
            this.showMessage(`❌ Errore: ${error.message}`, 'error');
        } finally {
            saveBtn.disabled = false;
            saveBtn.textContent = originalText;
        }
    }

    showMessage(message, type) {
        // Crea o aggiorna messaggio nel modal
        let messageDiv = document.getElementById('preview-modal-message');
        if (!messageDiv) {
            messageDiv = document.createElement('div');
            messageDiv.id = 'preview-modal-message';
            messageDiv.className = `preview-message ${type}`;
            const header = document.querySelector('.preview-modal-header');
            header.insertAdjacentElement('afterend', messageDiv);
        }
        messageDiv.textContent = message;
        messageDiv.className = `preview-message ${type}`;
        messageDiv.style.display = 'block';
        
        setTimeout(() => {
            messageDiv.style.display = 'none';
        }, 5000);
    }
}

// Istanza globale
window.previewModal = new PreviewModal();

