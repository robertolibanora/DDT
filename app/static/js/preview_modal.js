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
        this.annotations = {}; // {field: {x, y, width, height}}
        this.isDrawing = false;
        this.startX = 0;
        this.startY = 0;
        this.currentField = null;
        this.canvas = null;
        this.ctx = null;
        this.imgElement = null;
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
                            <div class="preview-annotation-controls">
                                <label for="annotation-field-select">🎯 Seleziona campo da annotare:</label>
                                <select id="annotation-field-select">
                                    <option value="">-- Nessun campo selezionato --</option>
                                    <option value="data">📅 Data DDT</option>
                                    <option value="mittente">🏢 Mittente</option>
                                    <option value="destinatario">📍 Destinatario</option>
                                    <option value="numero_documento">🔢 Numero Documento</option>
                                    <option value="totale_kg">⚖️ Totale Kg</option>
                                </select>
                                <button id="clear-annotations-btn" class="btn-clear-annotations" title="Cancella tutte le annotazioni">🗑️ Cancella Annotazioni</button>
                            </div>
                            <div class="preview-pdf-container" id="preview-pdf-container">
                                <div class="preview-image-wrapper">
                                    <img id="preview-pdf-image" src="" alt="Anteprima DDT" style="max-width: 100%; height: auto; display: block; margin: 0 auto;">
                                    <canvas id="preview-annotation-canvas"></canvas>
                                </div>
                            </div>
                            <div class="annotation-hint">
                                💡 <strong>Suggerimento:</strong> Seleziona un campo e disegna un riquadro sull'immagine per indicare dove si trova il dato. Questo aiuterà il modello a estrarre i dati con maggiore precisione.
                            </div>
                            <div class="layout-trainer-link-container">
                                <a id="layout-trainer-link" href="#">
                                    ✏️ Insegna Layout di questo Documento
                                </a>
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

        // Setup annotazioni
        this.setupAnnotationListeners();
    }

    setupAnnotationListeners() {
        // Seleziona campo da annotare
        const fieldSelect = document.getElementById('annotation-field-select');
        fieldSelect.addEventListener('change', (e) => {
            this.currentField = e.target.value || null;
            if (this.currentField) {
                this.updateCanvasCursor('crosshair');
            } else {
                this.updateCanvasCursor('default');
            }
        });

        // Cancella annotazioni
        const clearBtn = document.getElementById('clear-annotations-btn');
        clearBtn.addEventListener('click', () => {
            if (confirm('Vuoi cancellare tutte le annotazioni?')) {
                this.annotations = {};
                this.redrawAnnotations();
            }
        });

        // Setup canvas per disegno
        this.canvas = document.getElementById('preview-annotation-canvas');
        this.ctx = this.canvas.getContext('2d');
        this.imgElement = document.getElementById('preview-pdf-image');

        // Eventi mouse per disegno
        this.canvas.addEventListener('mousedown', (e) => this.startDrawing(e));
        this.canvas.addEventListener('mousemove', (e) => this.draw(e));
        this.canvas.addEventListener('mouseup', (e) => this.stopDrawing(e));
        this.canvas.addEventListener('mouseleave', () => this.stopDrawing(null));

        // Eventi touch per mobile
        this.canvas.addEventListener('touchstart', (e) => {
            e.preventDefault();
            const touch = e.touches[0];
            const mouseEvent = new MouseEvent('mousedown', {
                clientX: touch.clientX,
                clientY: touch.clientY
            });
            this.canvas.dispatchEvent(mouseEvent);
        });
        this.canvas.addEventListener('touchmove', (e) => {
            e.preventDefault();
            const touch = e.touches[0];
            const mouseEvent = new MouseEvent('mousemove', {
                clientX: touch.clientX,
                clientY: touch.clientY
            });
            this.canvas.dispatchEvent(mouseEvent);
        });
        this.canvas.addEventListener('touchend', (e) => {
            e.preventDefault();
            const mouseEvent = new MouseEvent('mouseup', {});
            this.canvas.dispatchEvent(mouseEvent);
        });
    }

    updateCanvasCursor(cursor) {
        if (this.canvas) {
            this.canvas.style.cursor = cursor;
        }
    }

    getCanvasCoordinates(e) {
        const rect = this.canvas.getBoundingClientRect();
        const scaleX = this.canvas.width / rect.width;
        const scaleY = this.canvas.height / rect.height;
        return {
            x: (e.clientX - rect.left) * scaleX,
            y: (e.clientY - rect.top) * scaleY
        };
    }

    startDrawing(e) {
        if (!this.currentField) return;
        
        this.isDrawing = true;
        const coords = this.getCanvasCoordinates(e);
        this.startX = coords.x;
        this.startY = coords.y;
    }

    draw(e) {
        if (!this.isDrawing || !this.currentField) return;

        const coords = this.getCanvasCoordinates(e);
        this.redrawAnnotations();
        
        // Disegna il riquadro temporaneo
        this.ctx.strokeStyle = this.getFieldColor(this.currentField);
        this.ctx.lineWidth = 3;
        this.ctx.setLineDash([5, 5]);
        this.ctx.strokeRect(
            this.startX,
            this.startY,
            coords.x - this.startX,
            coords.y - this.startY
        );
        this.ctx.setLineDash([]);
    }

    stopDrawing(e) {
        if (!this.isDrawing || !this.currentField) return;
        
        if (e) {
            const coords = this.getCanvasCoordinates(e);
            const width = coords.x - this.startX;
            const height = coords.y - this.startY;
            
            // Salva solo se il riquadro ha una dimensione minima
            if (Math.abs(width) > 10 && Math.abs(height) > 10) {
                this.annotations[this.currentField] = {
                    x: Math.min(this.startX, coords.x),
                    y: Math.min(this.startY, coords.y),
                    width: Math.abs(width),
                    height: Math.abs(height)
                };
            }
        }
        
        this.isDrawing = false;
        this.redrawAnnotations();
    }

    getFieldColor(field) {
        const colors = {
            'data': '#FF6B6B',
            'mittente': '#4ECDC4',
            'destinatario': '#45B7D1',
            'numero_documento': '#FFA07A',
            'totale_kg': '#98D8C8'
        };
        return colors[field] || '#000000';
    }

    getFieldLabel(field) {
        const labels = {
            'data': '📅 Data DDT',
            'mittente': '🏢 Mittente',
            'destinatario': '📍 Destinatario',
            'numero_documento': '🔢 Numero Documento',
            'totale_kg': '⚖️ Totale Kg'
        };
        return labels[field] || field;
    }

    redrawAnnotations() {
        if (!this.ctx || !this.imgElement) return;
        
        // Pulisci il canvas
        this.ctx.clearRect(0, 0, this.canvas.width, this.canvas.height);
        
        // Ridisegna tutte le annotazioni
        for (const [field, rect] of Object.entries(this.annotations)) {
            this.ctx.strokeStyle = this.getFieldColor(field);
            this.ctx.fillStyle = this.getFieldColor(field);
            this.ctx.lineWidth = 3;
            this.ctx.strokeRect(rect.x, rect.y, rect.width, rect.height);
            
            // Aggiungi etichetta
            this.ctx.fillStyle = 'rgba(255, 255, 255, 0.9)';
            this.ctx.fillRect(rect.x, rect.y - 25, 150, 20);
            this.ctx.fillStyle = this.getFieldColor(field);
            this.ctx.font = '12px Arial';
            this.ctx.fillText(this.getFieldLabel(field), rect.x + 5, rect.y - 8);
        }
    }

    resizeCanvas() {
        if (!this.canvas || !this.imgElement) return;
        
        // Aspetta che l'immagine sia caricata
        if (this.imgElement.complete && this.imgElement.naturalWidth > 0) {
            // Usa le dimensioni naturali dell'immagine per il canvas
            const naturalWidth = this.imgElement.naturalWidth;
            const naturalHeight = this.imgElement.naturalHeight;
            
            // Imposta le dimensioni del canvas alle dimensioni naturali dell'immagine
            this.canvas.width = naturalWidth;
            this.canvas.height = naturalHeight;
            
            // Imposta le dimensioni visualizzate del canvas per corrispondere all'immagine
            const rect = this.imgElement.getBoundingClientRect();
            this.canvas.style.width = rect.width + 'px';
            this.canvas.style.height = rect.height + 'px';
            this.canvas.style.position = 'absolute';
            this.canvas.style.top = '0';
            this.canvas.style.left = '0';
            
            // Ridisegna le annotazioni con le nuove dimensioni
            this.redrawAnnotations();
        } else {
            // Se l'immagine non è ancora caricata, riprova dopo un breve delay
            setTimeout(() => this.resizeCanvas(), 100);
        }
    }

    show(extractedData, pdfBase64, fileHash, fileName) {
        this.currentData = extractedData;
        this.currentFileHash = fileHash;
        this.currentFileName = fileName;
        this.annotations = {}; // Reset annotazioni

        // Imposta immagine PNG di anteprima usando l'endpoint dedicato
        const imageUrl = `/preview/image/${fileHash}`;
        const imgElement = document.getElementById('preview-pdf-image');
        imgElement.src = imageUrl;
        imgElement.onerror = () => {
            console.error('Errore caricamento immagine anteprima');
            imgElement.alt = 'Errore caricamento anteprima';
        };

        // Quando l'immagine è caricata, ridimensiona il canvas
        imgElement.onload = () => {
            setTimeout(() => {
                this.resizeCanvas();
                // Aggiungi listener per resize finestra
                window.addEventListener('resize', () => {
                    if (!this.modal.classList.contains('hidden')) {
                        this.resizeCanvas();
                    }
                });
            }, 100);
        };
        
        // Se l'immagine è già caricata, ridimensiona immediatamente
        if (imgElement.complete && imgElement.naturalWidth > 0) {
            setTimeout(() => this.resizeCanvas(), 100);
        }

        // Imposta dati nel form
        document.getElementById('preview-file-hash').value = fileHash || '';
        document.getElementById('preview-file-name').value = fileName || '';
        document.getElementById('preview-original-data').value = JSON.stringify(extractedData);
        
        // Aggiorna link layout trainer
        const layoutTrainerLink = document.getElementById('layout-trainer-link');
        if (layoutTrainerLink && fileHash) {
            const supplier = extractedData?.mittente || '';
            const url = `/layout-trainer?hash=${fileHash}${supplier ? '&supplier=' + encodeURIComponent(supplier) : ''}`;
            layoutTrainerLink.href = url;
        }

        // Popola i campi
        document.getElementById('preview-data').value = extractedData.data || '';
        document.getElementById('preview-mittente').value = extractedData.mittente || '';
        document.getElementById('preview-destinatario').value = extractedData.destinatario || '';
        document.getElementById('preview-numero-documento').value = extractedData.numero_documento || '';
        // Formatta il peso con 3 decimali
        const kgValue = parseFloat(extractedData.totale_kg) || 0;
        document.getElementById('preview-totale-kg').value = kgValue.toFixed(3);

        // Reset selezione campo
        document.getElementById('annotation-field-select').value = '';
        this.currentField = null;

        // Mostra modal
        this.modal.classList.remove('hidden');
        document.body.style.overflow = 'hidden'; // Previeni scroll della pagina
        
        // Ridimensiona canvas dopo un breve delay per assicurarsi che il layout sia pronto
        setTimeout(() => this.resizeCanvas(), 200);
        
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
        
        // Pulisci annotazioni
        this.annotations = {};
        this.currentField = null;
        if (this.ctx) {
            this.ctx.clearRect(0, 0, this.canvas.width, this.canvas.height);
        }
        
        this.currentData = null;
        this.currentFileHash = null;
        this.currentFileName = null;
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
        
        // Aggiungi annotazioni se presenti
        if (Object.keys(this.annotations).length > 0) {
            formData.append('annotations', JSON.stringify(this.annotations));
        }

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

