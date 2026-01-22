/**
 * Gestione pagina Modelli Layout
 * Carica e visualizza tutti i modelli salvati
 */

const ModelsPage = {
    models: [],
    
    init() {
        this.loadModels();
        this.setupEventListeners();
    },
    
    setupEventListeners() {
        // Close preview modal
        const closeBtn = document.getElementById('preview-close-btn');
        const previewModal = document.getElementById('preview-modal');
        const overlay = previewModal?.querySelector('.model-preview-overlay');
        
        if (closeBtn) {
            closeBtn.addEventListener('click', () => this.hidePreview());
        }
        
        if (overlay) {
            overlay.addEventListener('click', () => this.hidePreview());
        }
        
        // ESC per chiudere modal
        document.addEventListener('keydown', (e) => {
            if (e.key === 'Escape' && !previewModal?.classList.contains('hidden')) {
                this.hidePreview();
            }
        });
    },
    
    async loadModels() {
        const loading = document.getElementById('loading');
        const container = document.getElementById('models-container');
        const emptyState = document.getElementById('empty-state');
        
        try {
            loading.classList.remove('hidden');
            container.classList.add('hidden');
            emptyState.classList.add('hidden');
            
            const data = await apiGet('/api/models');
            
            if (data.success) {
                this.models = data.models || [];
                this.renderModels();
            } else {
                throw new Error(data.detail || 'Errore caricamento modelli');
            }
        } catch (error) {
            console.error('Errore caricamento modelli:', error);
            this.showError('Errore durante il caricamento dei modelli: ' + error.message);
        } finally {
            loading.classList.add('hidden');
        }
    },
    
    renderModels() {
        const container = document.getElementById('models-container');
        const emptyState = document.getElementById('empty-state');
        
        if (this.models.length === 0) {
            container.classList.add('hidden');
            emptyState.classList.remove('hidden');
            return;
        }
        
        container.classList.remove('hidden');
        emptyState.classList.add('hidden');
        
        container.innerHTML = this.models.map(model => this.renderModelCard(model)).join('');
        
        // Aggiungi event listeners ai pulsanti
        this.models.forEach(model => {
            const card = document.querySelector(`[data-model-id="${model.id}"]`);
            if (card) {
                const previewBtn = card.querySelector('.btn-preview');
                const editBtn = card.querySelector('.btn-edit');
                const deleteBtn = card.querySelector('.btn-delete');
                
                if (previewBtn) {
                    previewBtn.addEventListener('click', () => this.showPreview(model));
                }
                
                if (editBtn) {
                    editBtn.addEventListener('click', () => this.editModel(model));
                }
                
                if (deleteBtn) {
                    deleteBtn.addEventListener('click', () => this.deleteModel(model));
                }
            }
        });
    },
    
    renderModelCard(model) {
        const fieldsLabels = {
            'mittente': 'Mittente',
            'destinatario': 'Destinatario',
            'data': 'Data',
            'numero_documento': 'Numero Documento',
            'totale_kg': 'Totale Kg'
        };
        
        const fieldsBadges = model.fields.map(field => 
            `<span class="field-badge">${fieldsLabels[field] || field}</span>`
        ).join('');
        
        return `
            <div class="model-card" data-model-id="${model.id}">
                <div class="model-header">
                    <div class="model-name">${this.escapeHtml(model.name)}</div>
                    <div class="model-rule-name">${this.escapeHtml(model.rule_name)}</div>
                </div>
                
                <div class="model-info">
                    <div class="model-info-item">
                        <span class="model-info-label">Campi definiti:</span>
                        <span class="model-info-value">${model.fields_count}</span>
                    </div>
                    <div class="model-info-item">
                        <span class="model-info-label">Pagine:</span>
                        <span class="model-info-value">${model.page_count || 'Tutte'}</span>
                    </div>
                    <div class="model-fields-list">
                        ${fieldsBadges}
                    </div>
                    <div class="model-status">${model.status}</div>
                </div>
                
                <div class="model-actions">
                    <button class="btn-preview" title="Anteprima modello">
                        👁 Anteprima
                    </button>
                    <button class="btn-edit" title="Modifica modello">
                        ✏️ Modifica
                    </button>
                    <button class="btn-delete" title="Elimina modello">
                        🗑 Elimina
                    </button>
                </div>
            </div>
        `;
    },
    
    showPreview(model) {
        const modal = document.getElementById('preview-modal');
        const nameEl = document.getElementById('preview-model-name');
        const supplierEl = document.getElementById('preview-supplier');
        const fieldsCountEl = document.getElementById('preview-fields-count');
        const statusEl = document.getElementById('preview-status');
        const canvas = document.getElementById('preview-canvas');
        
        if (!modal || !nameEl || !supplierEl || !fieldsCountEl || !statusEl || !canvas) {
            console.error('Elementi modal non trovati');
            return;
        }
        
        // Popola info
        nameEl.textContent = `Modello: ${model.name}`;
        supplierEl.textContent = model.name;
        fieldsCountEl.textContent = `${model.fields_count} campi`;
        statusEl.textContent = model.status;
        
        // Disegna i box sul canvas
        this.drawPreviewBoxes(canvas, model.fields_data);
        
        // Mostra modal
        modal.classList.remove('hidden');
        document.body.style.overflow = 'hidden';
    },
    
    drawPreviewBoxes(canvas, fieldsData) {
        if (!fieldsData || Object.keys(fieldsData).length === 0) {
            canvas.width = 800;
            canvas.height = 600;
            const ctx = canvas.getContext('2d');
            ctx.fillStyle = '#f5f5f5';
            ctx.fillRect(0, 0, canvas.width, canvas.height);
            ctx.fillStyle = '#666';
            ctx.font = '20px Arial';
            ctx.textAlign = 'center';
            ctx.fillText('Nessun box definito', canvas.width / 2, canvas.height / 2);
            return;
        }
        
        // Dimensioni canvas (simula un documento A4)
        const width = 800;
        const height = 1131; // A4 ratio
        canvas.width = width;
        canvas.height = height;
        
        const ctx = canvas.getContext('2d');
        
        // Sfondo bianco
        ctx.fillStyle = '#ffffff';
        ctx.fillRect(0, 0, width, height);
        
        // Griglia leggera
        ctx.strokeStyle = '#e0e0e0';
        ctx.lineWidth = 1;
        for (let i = 0; i < width; i += 50) {
            ctx.beginPath();
            ctx.moveTo(i, 0);
            ctx.lineTo(i, height);
            ctx.stroke();
        }
        for (let i = 0; i < height; i += 50) {
            ctx.beginPath();
            ctx.moveTo(0, i);
            ctx.lineTo(width, i);
            ctx.stroke();
        }
        
        // Disegna i box
        const fieldColors = {
            'mittente': '#2196F3',
            'destinatario': '#4CAF50',
            'data': '#FF9800',
            'numero_documento': '#9C27B0',
            'totale_kg': '#F44336'
        };
        
        const fieldLabels = {
            'mittente': 'Mittente',
            'destinatario': 'Destinatario',
            'data': 'Data',
            'numero_documento': 'Numero Documento',
            'totale_kg': 'Totale Kg'
        };
        
        Object.entries(fieldsData).forEach(([fieldName, fieldData]) => {
            const box = fieldData.box;
            const x = box.x_pct * width;
            const y = box.y_pct * height;
            const w = box.w_pct * width;
            const h = box.h_pct * height;
            
            const color = fieldColors[fieldName] || '#E63946';
            
            // Disegna rettangolo
            ctx.strokeStyle = color;
            ctx.lineWidth = 3;
            ctx.setLineDash([]);
            ctx.strokeRect(x, y, w, h);
            
            // Sfondo semi-trasparente
            ctx.fillStyle = color + '20';
            ctx.fillRect(x, y, w, h);
            
            // Etichetta
            ctx.fillStyle = color;
            ctx.font = 'bold 14px Arial';
            ctx.fillText(fieldLabels[fieldName] || fieldName, x + 5, y - 5);
        });
    },
    
    hidePreview() {
        const modal = document.getElementById('preview-modal');
        if (modal) {
            modal.classList.add('hidden');
            document.body.style.overflow = '';
        }
    },
    
    editModel(model) {
        // Reindirizza al layout trainer con i parametri del modello
        const supplier = encodeURIComponent(model.name);
        window.location.href = `/layout-trainer?supplier=${supplier}&edit=${encodeURIComponent(model.id)}`;
    },
    
    async deleteModel(model) {
        if (!confirm(`Sei sicuro di voler eliminare il modello "${model.name}"?\n\nQuesta azione non può essere annullata.`)) {
            return;
        }
        
        try {
            const data = await apiDelete(`/api/models/${encodeURIComponent(model.id)}`);
            
            if (data.success) {
                this.showSuccess(`Modello "${model.name}" eliminato con successo`);
                // Ricarica la lista
                this.loadModels();
            } else {
                throw new Error(data.detail || 'Errore durante l\'eliminazione');
            }
        } catch (error) {
            console.error('Errore eliminazione modello:', error);
            this.showError('Errore durante l\'eliminazione: ' + error.message);
        }
    },
    
    showSuccess(message) {
        this.showMessage(message, 'success');
    },
    
    showError(message) {
        this.showMessage(message, 'error');
    },
    
    showMessage(text, type) {
        // Crea o aggiorna il messaggio
        let messageEl = document.querySelector('.page-message');
        if (!messageEl) {
            messageEl = document.createElement('div');
            messageEl.className = 'page-message message';
            const card = document.querySelector('.card');
            if (card) {
                card.insertBefore(messageEl, card.firstChild);
            }
        }
        
        messageEl.textContent = text;
        messageEl.className = `page-message message ${type}`;
        messageEl.classList.remove('hidden');
        
        // Auto-hide dopo 5 secondi
        setTimeout(() => {
            messageEl.classList.add('hidden');
        }, 5000);
    },
    
    escapeHtml(text) {
        const div = document.createElement('div');
        div.textContent = text;
        return div.innerHTML;
    }
};

// Inizializza quando il DOM è pronto
document.addEventListener('DOMContentLoaded', () => {
    ModelsPage.init();
});
