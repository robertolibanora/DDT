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
        this.currentModel = null; // Modello riconosciuto o selezionato
        this.availableModels = []; // Lista modelli disponibili
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
                            
                            <!-- Riconoscimento Modello -->
                            <div class="preview-model-detection">
                                <div id="model-detection-status" class="model-detection-status">
                                    <span class="model-detection-spinner">⏳ Rilevamento modello in corso...</span>
                                </div>
                                <div id="model-detected" class="model-detected hidden">
                                    <div class="model-detected-info">
                                        <span class="model-detected-icon">✅</span>
                                        <span class="model-detected-text">
                                            <strong>Modello riconosciuto:</strong> <span id="detected-model-name"></span>
                                        </span>
                                    </div>
                                </div>
                                <div id="model-selection" class="model-selection hidden">
                                    <label for="model-select">📐 Seleziona modello di layout:</label>
                                    <select id="model-select">
                                        <option value="">-- Nessun modello selezionato --</option>
                                    </select>
                                    <button id="apply-model-btn" class="btn-apply-model" disabled>
                                        🔄 Applica Modello
                                    </button>
                                </div>
                                <div id="model-applied" class="model-applied hidden">
                                    <div class="model-applied-info">
                                        <span class="model-applied-icon">✅</span>
                                        <span class="model-applied-text">
                                            Modello applicato: <span id="applied-model-name"></span>
                                        </span>
                                    </div>
                                </div>
                            </div>
                            
                            <div class="preview-pdf-container" id="preview-pdf-container">
                                <div class="preview-placeholder">
                                    <div style="font-size: 3em; margin-bottom: 10px;">📄</div>
                                    <div>Anteprima non disponibile</div>
                                </div>
                                <div class="preview-image-wrapper">
                                    <img id="preview-pdf-image" src="" alt="Anteprima DDT" style="max-width: 100%; height: auto; display: block; margin: 0 auto;">
                                </div>
                            </div>
                            
                            <div class="layout-trainer-link-container hidden">
                                <a id="layout-trainer-link" href="#">
                                    ✏️ Insegna Layout di questo Documento
                                </a>
                            </div>
                            
                            <!-- Banner suggerimento creazione layout model -->
                            <div id="suggest-layout-banner" class="suggest-layout-banner hidden">
                                <div class="suggest-layout-content">
                                    <div class="suggest-layout-icon">💡</div>
                                    <div class="suggest-layout-text">
                                        <div class="suggest-layout-title">Questo documento è stato elaborato senza un layout model</div>
                                        <div class="suggest-layout-subtitle">Puoi crearne uno per automatizzare i prossimi documenti simili</div>
                                    </div>
                                    <button id="create-layout-btn" class="btn-create-layout">
                                        📐 Crea Layout Model
                                    </button>
                                </div>
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
                                    <small class="form-help">Data del documento DDT (estratta dal PDF)</small>
                                </div>

                                <div class="form-group">
                                    <label for="preview-data-inserimento">📆 Data di Inserimento</label>
                                    <input type="date" id="preview-data-inserimento" name="data_inserimento" required>
                                    <small class="form-help">Data di archiviazione (scelta manualmente)</small>
                                </div>

                                <div class="form-group">
                                    <label for="preview-mittente">🏢 Mittente</label>
                                    <input type="text" id="preview-mittente" name="mittente" required>
                                </div>

                                <div class="form-group">
                                    <label for="preview-destinatario">📍 Destinatario</label>
                                    <input type="text" id="preview-destinatario" name="destinatario" required>
                                </div>

                                <div class="form-group form-group-highlight">
                                    <label for="preview-numero-documento">🔢 Numero Documento</label>
                                    <input type="text" id="preview-numero-documento" name="numero_documento" required>
                                </div>

                                <div class="form-group form-group-highlight">
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

        // Setup riconoscimento modello
        this.setupModelDetection();
    }

    /**
     * Funzione centralizzata per gestire la visibilità dei banner UI
     * Basata sullo stato del documento: modello riconosciuto, layout conosciuto, ecc.
     * 
     * @param {Object} options - Opzioni per determinare la visibilità
     * @param {boolean} options.modelRecognized - Se un modello è stato riconosciuto/applicato
     * @param {boolean} options.hasLayoutModel - Se esiste un layout model valido (da backend)
     * @param {boolean} options.hasExtractedData - Se ci sono dati estratti disponibili
     */
    updateBannerVisibility({ modelRecognized = false, hasLayoutModel = null, hasExtractedData = true }) {
        const suggestBanner = document.getElementById('suggest-layout-banner');
        const layoutTrainerLink = document.getElementById('layout-trainer-link');
        const layoutTrainerContainer = document.querySelector('.layout-trainer-link-container');
        
        // Determina se mostrare il banner "suggest-layout-banner"
        // REGOLA: Mostra SOLO se:
        // 1. Ci sono dati estratti
        // 2. NON c'è un modello riconosciuto/applicato
        // 3. NON c'è un layout model valido (hasLayoutModel === false)
        const shouldShowSuggestBanner = hasExtractedData 
            && !modelRecognized 
            && (hasLayoutModel === false);
        
        if (suggestBanner) {
            if (shouldShowSuggestBanner) {
                suggestBanner.classList.remove('hidden');
            } else {
                suggestBanner.classList.add('hidden');
            }
        }
        
        // Determina se mostrare il link "layout-trainer-link"
        // REGOLA: Mostra SOLO se:
        // 1. Ci sono dati estratti
        // 2. NON c'è un modello riconosciuto/applicato (altrimenti è ridondante)
        // Il link può essere utile per creare/modificare layout anche quando non c'è modello
        const shouldShowLayoutTrainerLink = hasExtractedData && !modelRecognized;
        
        if (layoutTrainerContainer) {
            if (shouldShowLayoutTrainerLink) {
                layoutTrainerContainer.classList.remove('hidden');
            } else {
                layoutTrainerContainer.classList.add('hidden');
            }
        }
    }

    setupModelDetection() {
        // Seleziona modello manualmente
        const modelSelect = document.getElementById('model-select');
        const applyModelBtn = document.getElementById('apply-model-btn');
        
        if (modelSelect) {
            modelSelect.addEventListener('change', (e) => {
                const selectedModelId = e.target.value;
                if (applyModelBtn) {
                    applyModelBtn.disabled = !selectedModelId;
                }
                this.currentModel = selectedModelId ? 
                    this.availableModels.find(m => m.id === selectedModelId) : null;
            });
        }
        
        // Applica modello selezionato
        if (applyModelBtn) {
            applyModelBtn.addEventListener('click', () => {
                const selectedModelId = modelSelect?.value;
                if (selectedModelId) {
                    this.applyModel(selectedModelId);
                }
            });
        }
        
        // Setup immagine
        this.imgElement = document.getElementById('preview-pdf-image');
    }

    async detectModel(mittente, pageCount = null) {
        const statusEl = document.getElementById('model-detection-status');
        const detectedEl = document.getElementById('model-detected');
        const selectionEl = document.getElementById('model-selection');
        const appliedEl = document.getElementById('model-applied');
        
        if (!statusEl || !detectedEl || !selectionEl) {
            console.warn('Elementi riconoscimento modello non trovati');
            return;
        }
        
        // Mostra stato di caricamento
        statusEl.classList.remove('hidden');
        detectedEl.classList.add('hidden');
        selectionEl.classList.add('hidden');
        if (appliedEl) appliedEl.classList.add('hidden');
        
        try {
            const params = new URLSearchParams({ mittente });
            if (pageCount) {
                params.append('page_count', pageCount);
            }
            
            const response = await fetch(`/preview/detect-model?${params}`, {
                credentials: 'include'
            });
            
            if (!response.ok) {
                throw new Error(`Errore ${response.status}`);
            }
            
            const data = await response.json();
            
            if (data.success) {
                this.availableModels = data.available_models || [];
                
                // Popola dropdown modelli disponibili
                const modelSelect = document.getElementById('model-select');
                if (modelSelect) {
                    modelSelect.innerHTML = '<option value="">-- Nessun modello selezionato --</option>';
                    this.availableModels.forEach(model => {
                        const option = document.createElement('option');
                        option.value = model.id;
                        option.textContent = `${model.name} (${model.fields_count} campi)`;
                        modelSelect.appendChild(option);
                    });
                }
                
                if (data.matched && data.model) {
                    // Modello riconosciuto automaticamente
                    this.currentModel = data.model;
                    statusEl.classList.add('hidden');
                    detectedEl.classList.remove('hidden');
                    const detectedNameEl = document.getElementById('detected-model-name');
                    if (detectedNameEl) {
                        detectedNameEl.textContent = `${data.model.name} (${data.model.fields_count} campi)`;
                    }
                    
                    // Mostra anche la selezione manuale per permettere cambio
                    selectionEl.classList.remove('hidden');
                    if (modelSelect) {
                        modelSelect.value = data.model.id;
                    }
                    const applyBtn = document.getElementById('apply-model-btn');
                    if (applyBtn) {
                        applyBtn.disabled = false;
                    }
                    
                    // Aggiorna visibilità banner: modello riconosciuto = nascondi banner
                    this.updateBannerVisibility({
                        modelRecognized: true,
                        hasLayoutModel: true, // Se riconosciuto, assumiamo layout valido
                        hasExtractedData: true
                    });
                } else {
                    // Nessun modello riconosciuto, mostra solo selezione manuale
                    statusEl.classList.add('hidden');
                    selectionEl.classList.remove('hidden');
                    
                    // Aggiorna visibilità banner: nessun modello = mostra banner se necessario
                    // hasLayoutModel viene passato dalla funzione show()
                    this.updateBannerVisibility({
                        modelRecognized: false,
                        hasLayoutModel: this._lastHasLayoutModel, // Usa valore salvato
                        hasExtractedData: true
                    });
                }
            }
        } catch (error) {
            console.error('Errore rilevamento modello:', error);
            statusEl.innerHTML = '<span class="model-detection-error">❌ Errore rilevamento modello</span>';
            // Mostra comunque la selezione manuale
            selectionEl.classList.remove('hidden');
            
            // Aggiorna visibilità banner: errore = nessun modello riconosciuto
            this.updateBannerVisibility({
                modelRecognized: false,
                hasLayoutModel: this._lastHasLayoutModel,
                hasExtractedData: true
            });
        }
    }

    async applyModel(modelId) {
        const applyBtn = document.getElementById('apply-model-btn');
        const appliedEl = document.getElementById('model-applied');
        const originalText = applyBtn?.textContent || '🔄 Applica Modello';
        
        if (applyBtn) {
            applyBtn.disabled = true;
            applyBtn.textContent = '⏳ Applicazione in corso...';
        }
        
        try {
            const formData = new FormData();
            formData.append('file_hash', this.currentFileHash);
            formData.append('model_id', modelId);
            
            const response = await fetch('/preview/apply-model', {
                method: 'POST',
                body: formData,
                credentials: 'include'
            });
            
            if (!response.ok) {
                throw new Error(`Errore ${response.status}`);
            }
            
            const data = await response.json();
            
            if (data.success) {
                // Aggiorna i dati estratti con quelli del modello applicato
                if (data.extracted_data) {
                    this.currentData = data.extracted_data;
                    
                    // Aggiorna i campi del form
                    const dataEl = document.getElementById('preview-data');
                    const mittenteEl = document.getElementById('preview-mittente');
                    const destinatarioEl = document.getElementById('preview-destinatario');
                    const numeroEl = document.getElementById('preview-numero-documento');
                    const kgEl = document.getElementById('preview-totale-kg');
                    const originalDataEl = document.getElementById('preview-original-data');
                    
                    if (dataEl) dataEl.value = data.extracted_data.data || '';
                    // data_inserimento non viene aggiornata quando si applica un modello
                    if (mittenteEl) mittenteEl.value = data.extracted_data.mittente || '';
                    if (destinatarioEl) destinatarioEl.value = data.extracted_data.destinatario || '';
                    if (numeroEl) numeroEl.value = data.extracted_data.numero_documento || '';
                    if (kgEl) {
                        const kgValue = parseFloat(data.extracted_data.totale_kg) || 0;
                        kgEl.value = kgValue.toFixed(3);
                    }
                    if (originalDataEl) {
                        originalDataEl.value = JSON.stringify(data.extracted_data);
                    }
                }
                
                // Nascondi tutti gli altri stati prima di mostrare la conferma
                const statusEl = document.getElementById('model-detection-status');
                const detectedEl = document.getElementById('model-detected');
                const selectionEl = document.getElementById('model-selection');
                
                if (statusEl) statusEl.classList.add('hidden');
                if (detectedEl) detectedEl.classList.add('hidden');
                if (selectionEl) selectionEl.classList.add('hidden');
                
                // Mostra conferma applicazione
                if (appliedEl) {
                    appliedEl.classList.remove('hidden');
                    const appliedNameEl = document.getElementById('applied-model-name');
                    if (appliedNameEl) {
                        appliedNameEl.textContent = data.model_applied?.name || modelId;
                    }
                }
                
                // Aggiorna visibilità banner: modello applicato = nascondi banner
                this.updateBannerVisibility({
                    modelRecognized: true,
                    hasLayoutModel: true, // Modello applicato = layout valido
                    hasExtractedData: true
                });
            }
        } catch (error) {
            console.error('Errore applicazione modello:', error);
            alert('Errore durante l\'applicazione del modello: ' + error.message);
        } finally {
            if (applyBtn) {
                applyBtn.disabled = false;
                applyBtn.textContent = originalText;
            }
        }
    }

    show(extractedData, pdfBase64, fileHash, fileName, extractionMode = null, suggestCreateLayout = false, hasLayoutModel = null) {
        this.currentData = extractedData;
        this.currentFileHash = fileHash;
        this.currentFileName = fileName;
        this.currentModel = null;

        // Verifica se extractedData è disponibile (potrebbe essere undefined per QUEUED/PROCESSING)
        const hasExtractedData = extractedData && typeof extractedData === 'object';

        // Reset tutti gli stati di riconoscimento modello
        const statusEl = document.getElementById('model-detection-status');
        const detectedEl = document.getElementById('model-detected');
        const selectionEl = document.getElementById('model-selection');
        const appliedEl = document.getElementById('model-applied');
        
        if (statusEl) {
            statusEl.classList.add('hidden');
            statusEl.innerHTML = '<span class="model-detection-spinner">⏳ Rilevamento modello in corso...</span>';
        }
        if (detectedEl) detectedEl.classList.add('hidden');
        if (selectionEl) selectionEl.classList.add('hidden');
        if (appliedEl) appliedEl.classList.add('hidden');

        // Imposta immagine PNG di anteprima usando l'endpoint dedicato
        const imageUrl = `/preview/image/${fileHash}`;
        const imgElement = document.getElementById('preview-pdf-image');
        const imageWrapper = document.querySelector('.preview-image-wrapper');
        const pdfContainer = document.getElementById('preview-pdf-container');
        
        if (imgElement && pdfContainer) {
            // Reset zoom quando cambia immagine
            if (imageWrapper) {
                imageWrapper.classList.remove('zoomed');
            }
            
            // Reset placeholder state
            pdfContainer.classList.remove('no-image');
            
            imgElement.src = imageUrl;
            imgElement.onload = () => {
                // Immagine caricata con successo
                pdfContainer.classList.remove('no-image');
                if (imageWrapper) {
                    imageWrapper.style.display = 'block';
                }
                
                // Assicura che l'immagine sia sempre visibile completamente
                // Reset eventuali trasformazioni che potrebbero tagliare
                if (imageWrapper) {
                    imageWrapper.classList.remove('zoomed');
                }
                
                // Scrolla in alto per mostrare l'inizio del documento
                pdfContainer.scrollTop = 0;
                pdfContainer.scrollLeft = 0;
            };
            imgElement.onerror = () => {
                console.error('Errore caricamento immagine anteprima');
                imgElement.alt = 'Errore caricamento anteprima';
                imgElement.src = '';
                // Mostra placeholder
                pdfContainer.classList.add('no-image');
                if (imageWrapper) {
                    imageWrapper.style.display = 'none';
                }
            };
            
            // Aggiungi click per zoom toggle (solo se immagine caricata)
            if (imageWrapper) {
                // Rimuovi listener precedenti per evitare duplicati
                const existingWrapper = imageWrapper;
                const clickHandler = (e) => {
                    // Zoom solo se l'immagine è caricata
                    if (imgElement.src && imgElement.complete && imgElement.naturalHeight !== 0) {
                        existingWrapper.classList.toggle('zoomed');
                        // Scrolla in alto dopo zoom per vedere l'inizio
                        if (existingWrapper.classList.contains('zoomed')) {
                            setTimeout(() => {
                                pdfContainer.scrollTop = 0;
                            }, 200);
                        }
                    }
                };
                
                // Rimuovi listener precedenti
                const newWrapper = existingWrapper.cloneNode(true);
                existingWrapper.parentNode.replaceChild(newWrapper, existingWrapper);
                newWrapper.addEventListener('click', clickHandler);
                
                // Aggiorna riferimento
                this.imgElement = document.getElementById('preview-pdf-image');
            }
        }

        // Imposta dati nel form
        const fileHashEl = document.getElementById('preview-file-hash');
        const fileNameEl = document.getElementById('preview-file-name');
        const originalDataEl = document.getElementById('preview-original-data');
        
        if (fileHashEl) fileHashEl.value = fileHash || '';
        if (fileNameEl) fileNameEl.value = fileName || '';
        if (originalDataEl) originalDataEl.value = hasExtractedData ? JSON.stringify(extractedData) : '';
        
        // Salva hasLayoutModel per uso successivo (quando detectModel completa)
        this._lastHasLayoutModel = hasLayoutModel;
        
        // Aggiorna link layout trainer (solo se abbiamo dati estratti)
        const layoutTrainerLink = document.getElementById('layout-trainer-link');
        if (layoutTrainerLink && fileHash && hasExtractedData) {
            const supplier = extractedData?.mittente || '';
            const url = `/layout-trainer?hash=${fileHash}${supplier ? '&supplier=' + encodeURIComponent(supplier) : ''}`;
            layoutTrainerLink.href = url;
        } else if (layoutTrainerLink) {
            layoutTrainerLink.href = '#';
        }

        // Popola i campi (solo se abbiamo dati estratti)
        const dataEl = document.getElementById('preview-data');
        const dataInserimentoEl = document.getElementById('preview-data-inserimento');
        const mittenteEl = document.getElementById('preview-mittente');
        const destinatarioEl = document.getElementById('preview-destinatario');
        const numeroEl = document.getElementById('preview-numero-documento');
        const kgEl = document.getElementById('preview-totale-kg');
        
        if (hasExtractedData) {
            // Popola i campi con i dati estratti
            if (dataEl) dataEl.value = extractedData.data || '';
            
            // Imposta data_inserimento: default a oggi, ma controlla se già salvata
            if (dataInserimentoEl) {
                // Prova a recuperare data_inserimento salvata (se documento già visto)
                // Per ora usa sempre data odierna come default
                const today = new Date();
                const todayStr = today.toISOString().split('T')[0]; // YYYY-MM-DD
                dataInserimentoEl.value = todayStr;
            }
            
            if (mittenteEl) mittenteEl.value = extractedData.mittente || '';
            if (destinatarioEl) destinatarioEl.value = extractedData.destinatario || '';
            if (numeroEl) numeroEl.value = extractedData.numero_documento || '';
            if (kgEl) {
                const kgValue = parseFloat(extractedData.totale_kg) || 0;
                kgEl.value = kgValue.toFixed(3);
            }
            
            // Disabilita i campi se non abbiamo dati estratti
            const formFields = [dataEl, mittenteEl, destinatarioEl, numeroEl, kgEl];
            formFields.forEach(field => {
                if (field) field.disabled = false;
            });
            
            // Rileva modello automaticamente basato sul mittente estratto
            const mittente = extractedData?.mittente || '';
            if (mittente) {
                this.detectModel(mittente);
            } else {
                // Se non c'è mittente, mostra solo selezione manuale
                const statusEl = document.getElementById('model-detection-status');
                const selectionEl = document.getElementById('model-selection');
                if (statusEl) statusEl.classList.add('hidden');
                if (selectionEl) selectionEl.classList.remove('hidden');
                
                // Aggiorna visibilità banner: nessun mittente = nessun modello possibile
                this.updateBannerVisibility({
                    modelRecognized: false,
                    hasLayoutModel: this._lastHasLayoutModel,
                    hasExtractedData: true
                });
            }
        } else {
            // Nessun dato estratto disponibile (QUEUED/PROCESSING)
            // Pulisci i campi e disabilitali
            if (dataEl) {
                dataEl.value = '';
                dataEl.disabled = true;
            }
            if (dataInserimentoEl) {
                const today = new Date();
                const todayStr = today.toISOString().split('T')[0];
                dataInserimentoEl.value = todayStr;
                dataInserimentoEl.disabled = true;
            }
            if (mittenteEl) {
                mittenteEl.value = '';
                mittenteEl.disabled = true;
            }
            if (destinatarioEl) {
                destinatarioEl.value = '';
                destinatarioEl.disabled = true;
            }
            if (numeroEl) {
                numeroEl.value = '';
                numeroEl.disabled = true;
            }
            if (kgEl) {
                kgEl.value = '';
                kgEl.disabled = true;
            }
            
            // Nascondi riconoscimento modello
            const statusEl = document.getElementById('model-detection-status');
            const detectedEl = document.getElementById('model-detected');
            const selectionEl = document.getElementById('model-selection');
            if (statusEl) statusEl.classList.add('hidden');
            if (detectedEl) detectedEl.classList.add('hidden');
            if (selectionEl) selectionEl.classList.add('hidden');
        }

        // Setup CTA button per banner (se verrà mostrato)
        const createLayoutBtn = document.getElementById('create-layout-btn');
        if (createLayoutBtn) {
            createLayoutBtn.onclick = () => {
                const supplier = extractedData?.mittente || '';
                const url = `/layout-trainer?hash=${fileHash}${supplier ? '&supplier=' + encodeURIComponent(supplier) : ''}`;
                window.location.href = url;
            };
        }
        
        // Aggiorna visibilità banner iniziale (prima del riconoscimento modello)
        // Se hasLayoutModel è true, nascondi banner anche prima del riconoscimento
        // Altrimenti aspetta il risultato di detectModel()
        const initialModelRecognized = false; // Non ancora riconosciuto
        this.updateBannerVisibility({
            modelRecognized: initialModelRecognized,
            hasLayoutModel: hasLayoutModel,
            hasExtractedData: hasExtractedData
        });

        // Mostra messaggio di stato se non abbiamo dati estratti
        const formSection = document.querySelector('.preview-form-section');
        if (formSection) {
            // Rimuovi eventuali messaggi di stato precedenti
            const existingStatusMsg = formSection.querySelector('.processing-status-message');
            if (existingStatusMsg) {
                existingStatusMsg.remove();
            }
            
            if (!hasExtractedData) {
                // Crea messaggio di stato
                const statusMsg = document.createElement('div');
                statusMsg.className = 'processing-status-message';
                statusMsg.innerHTML = `
                    <div class="processing-status-content">
                        <div class="processing-status-icon">⏳</div>
                        <div class="processing-status-text">
                            <div class="processing-status-title">Documento in elaborazione</div>
                            <div class="processing-status-subtitle">Il documento è stato caricato e sarà processato dal worker. L'anteprima sarà disponibile a breve.</div>
                        </div>
                    </div>
                `;
                // Inserisci prima del form
                const form = formSection.querySelector('#preview-form');
                if (form) {
                    formSection.insertBefore(statusMsg, form);
                }
                
                // Disabilita il pulsante di conferma
                const confirmBtn = document.getElementById('preview-confirm-btn');
                if (confirmBtn) {
                    confirmBtn.disabled = true;
                    confirmBtn.textContent = '⏳ In attesa di elaborazione...';
                }
            } else {
                // Abilita il pulsante di conferma
                const confirmBtn = document.getElementById('preview-confirm-btn');
                if (confirmBtn) {
                    confirmBtn.disabled = false;
                    confirmBtn.textContent = '✅ Conferma e Salva';
                }
            }
        }

        // Mostra modal
        this.modal.classList.remove('hidden');
        document.body.style.overflow = 'hidden'; // Previeni scroll della pagina
    }

    hide() {
        this.modal.classList.add('hidden');
        document.body.style.overflow = '';
        
        // Pulisci immagine per liberare memoria
        const imgElement = document.getElementById('preview-pdf-image');
        if (imgElement) {
            imgElement.src = '';
        }
        
        // Reset tutti gli stati di riconoscimento modello
        const statusEl = document.getElementById('model-detection-status');
        const detectedEl = document.getElementById('model-detected');
        const selectionEl = document.getElementById('model-selection');
        const appliedEl = document.getElementById('model-applied');
        
        if (statusEl) {
            statusEl.classList.add('hidden');
            statusEl.innerHTML = '<span class="model-detection-spinner">⏳ Rilevamento modello in corso...</span>';
        }
        if (detectedEl) detectedEl.classList.add('hidden');
        if (selectionEl) selectionEl.classList.add('hidden');
        if (appliedEl) appliedEl.classList.add('hidden');
        
        // Nascondi banner suggerimento layout
        const suggestBanner = document.getElementById('suggest-layout-banner');
        if (suggestBanner) {
            suggestBanner.classList.add('hidden');
        }
        
        // Nascondi link layout trainer
        const layoutTrainerContainer = document.querySelector('.layout-trainer-link-container');
        if (layoutTrainerContainer) {
            layoutTrainerContainer.classList.add('hidden');
        }
        
        // Reset stato modello
        this.currentModel = null;
        this.availableModels = [];
        this._lastHasLayoutModel = null;
        
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
        
        // Data di inserimento: converti da YYYY-MM-DD a gg-mm-yyyy
        const dataInserimentoInput = document.getElementById('preview-data-inserimento');
        if (!dataInserimentoInput || !dataInserimentoInput.value) {
            alert('⚠️ La data di inserimento è obbligatoria');
            return;
        }
        const dataInserimentoISO = dataInserimentoInput.value; // YYYY-MM-DD
        const [year, month, day] = dataInserimentoISO.split('-');
        const dataInserimentoFormatted = `${day}-${month}-${year}`; // gg-mm-yyyy
        formData.append('data_inserimento', dataInserimentoFormatted);
        
        formData.append('mittente', document.getElementById('preview-mittente').value);
        formData.append('destinatario', document.getElementById('preview-destinatario').value);
        formData.append('numero_documento', document.getElementById('preview-numero-documento').value);
        // Assicura che il peso abbia sempre 3 decimali
        const kgInput = document.getElementById('preview-totale-kg');
        const kgValue = parseFloat(kgInput.value) || 0;
        formData.append('totale_kg', kgValue.toFixed(3));
        
        // Aggiungi annotazioni se presenti

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

            // Mostra messaggio di successo nel modal
            this.showMessage('✅ DDT salvato con successo! Le correzioni sono state apprese dal sistema.', 'success');
            
            // Mostra toast notification se disponibile (UX migliorata)
            if (typeof showToast === 'function') {
                showToast({
                    type: 'success',
                    message: '✅ Documento salvato con successo',
                    duration: 3000
                });
            }
            
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

