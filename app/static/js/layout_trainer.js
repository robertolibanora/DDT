/**
 * Layout Trainer - Sistema per insegnare visivamente al sistema dove si trovano i campi DDT
 * Permette di disegnare box grafici sull'anteprima del PDF
 */

class LayoutTrainer {
    constructor() {
        this.canvas = null;
        this.ctx = null;
        this.imgElement = null;
        this.imageLoaded = false;
        this.boxes = []; // Array di {id, field, x, y, width, height, x_pct, y_pct, w_pct, h_pct}
        this.nextBoxId = 1;
        this.isDrawing = false;
        this.startX = 0;
        this.startY = 0;
        this.currentBox = null;
        this.selectedBoxId = null;
        this.isResizing = false;
        this.resizeHandle = null;
        
        this.fieldOptions = [
            { value: 'mittente', label: 'Mittente' },
            { value: 'destinatario', label: 'Destinatario' },
            { value: 'data', label: 'Data' },
            { value: 'numero_documento', label: 'Numero Documento' },
            { value: 'totale_kg', label: 'Totale Kg' }
        ];
        
        this.init();
    }
    
    init() {
        this.canvas = document.getElementById('layout-canvas');
        if (!this.canvas) {
            console.error('Canvas non trovato');
            return;
        }
        
        this.ctx = this.canvas.getContext('2d');
        this.setupEventListeners();
    }
    
    setupEventListeners() {
        // Mouse events per disegnare/spostare box
        this.canvas.addEventListener('mousedown', (e) => this.handleMouseDown(e));
        this.canvas.addEventListener('mousemove', (e) => this.handleMouseMove(e));
        this.canvas.addEventListener('mouseup', (e) => this.handleMouseUp(e));
        this.canvas.addEventListener('mouseleave', () => this.handleMouseLeave());
        
        // Click per selezionare box
        this.canvas.addEventListener('click', (e) => this.handleClick(e));
        
        // Keyboard events
        document.addEventListener('keydown', (e) => {
            if (e.key === 'Delete' && this.selectedBoxId) {
                this.deleteBox(this.selectedBoxId);
            }
        });
    }
    
    loadImage(imageUrl) {
        return new Promise((resolve, reject) => {
            this.imgElement = new Image();
            this.imgElement.onload = () => {
                // Imposta dimensioni canvas
                this.canvas.width = this.imgElement.width;
                this.canvas.height = this.imgElement.height;
                this.imageLoaded = true;
                this.draw();
                resolve();
            };
            this.imgElement.onerror = reject;
            this.imgElement.src = imageUrl;
        });
    }
    
    draw() {
        if (!this.imageLoaded || !this.imgElement) return;
        
        // Pulisci canvas
        this.ctx.clearRect(0, 0, this.canvas.width, this.canvas.height);
        
        // Disegna immagine
        this.ctx.drawImage(this.imgElement, 0, 0);
        
        // Disegna tutti i box
        this.boxes.forEach(box => {
            this.drawBox(box);
        });
        
        // Disegna box corrente se in disegno
        if (this.isDrawing && this.currentBox) {
            this.drawBox(this.currentBox);
        }
    }
    
    drawBox(box) {
        const isSelected = box.id === this.selectedBoxId;
        
        // Disegna rettangolo
        this.ctx.strokeStyle = isSelected ? '#ff0000' : '#ff3333';
        this.ctx.lineWidth = isSelected ? 3 : 2;
        this.ctx.setLineDash([]);
        this.ctx.strokeRect(box.x, box.y, box.width, box.height);
        
        // Disegna etichetta campo
        this.ctx.fillStyle = '#ff0000';
        this.ctx.font = '14px Arial';
        this.ctx.fillText(
            this.getFieldLabel(box.field),
            box.x + 5,
            box.y - 5
        );
        
        // Se selezionato, disegna handle di ridimensionamento
        if (isSelected) {
            this.drawResizeHandles(box);
        }
    }
    
    drawResizeHandles(box) {
        const handleSize = 8;
        const handles = [
            { x: box.x, y: box.y }, // Top-left
            { x: box.x + box.width, y: box.y }, // Top-right
            { x: box.x, y: box.y + box.height }, // Bottom-left
            { x: box.x + box.width, y: box.y + box.height } // Bottom-right
        ];
        
        this.ctx.fillStyle = '#ff0000';
        handles.forEach(handle => {
            this.ctx.fillRect(
                handle.x - handleSize / 2,
                handle.y - handleSize / 2,
                handleSize,
                handleSize
            );
        });
    }
    
    getFieldLabel(fieldValue) {
        const field = this.fieldOptions.find(f => f.value === fieldValue);
        return field ? field.label : fieldValue;
    }
    
    getMousePos(e) {
        const rect = this.canvas.getBoundingClientRect();
        // Calcola il rapporto tra dimensioni CSS e dimensioni reali del canvas
        const scaleX = this.canvas.width / rect.width;
        const scaleY = this.canvas.height / rect.height;
        
        return {
            x: (e.clientX - rect.left) * scaleX,
            y: (e.clientY - rect.top) * scaleY
        };
    }
    
    getBoxAt(x, y) {
        // Cerca box dal più piccolo al più grande (per gestire sovrapposizioni)
        const sortedBoxes = [...this.boxes].sort((a, b) => 
            (a.width * a.height) - (b.width * b.height)
        );
        
        for (const box of sortedBoxes) {
            if (x >= box.x && x <= box.x + box.width &&
                y >= box.y && y <= box.y + box.height) {
                return box;
            }
        }
        return null;
    }
    
    getResizeHandleAt(x, y, box) {
        const handleSize = 8;
        const handles = [
            { corner: 'tl', x: box.x, y: box.y },
            { corner: 'tr', x: box.x + box.width, y: box.y },
            { corner: 'bl', x: box.x, y: box.y + box.height },
            { corner: 'br', x: box.x + box.width, y: box.y + box.height }
        ];
        
        for (const handle of handles) {
            if (Math.abs(x - handle.x) <= handleSize && 
                Math.abs(y - handle.y) <= handleSize) {
                return handle.corner;
            }
        }
        return null;
    }
    
    handleMouseDown(e) {
        const pos = this.getMousePos(e);
        
        // Controlla se clic su handle di ridimensionamento
        if (this.selectedBoxId) {
            const selectedBox = this.boxes.find(b => b.id === this.selectedBoxId);
            if (selectedBox) {
                const handle = this.getResizeHandleAt(pos.x, pos.y, selectedBox);
                if (handle) {
                    this.isResizing = true;
                    this.resizeHandle = handle;
                    this.currentBox = selectedBox;
                    return;
                }
            }
        }
        
        // Controlla se clic su box esistente
        const clickedBox = this.getBoxAt(pos.x, pos.y);
        if (clickedBox) {
            this.selectedBoxId = clickedBox.id;
            this.currentBox = { ...clickedBox };
            this.isDrawing = true;
            this.startX = pos.x;
            this.startY = pos.y;
            this.draw();
            this.updateFieldSelect();
            return;
        }
        
        // Inizia nuovo box
        const selectedField = document.getElementById('field-select').value;
        if (!selectedField) {
            alert('Seleziona prima un campo dal menu a tendina!');
            return;
        }
        
        this.selectedBoxId = null;
        this.isDrawing = true;
        this.startX = pos.x;
        this.startY = pos.y;
        this.currentBox = {
            id: this.nextBoxId++,
            field: selectedField,
            x: pos.x,
            y: pos.y,
            width: 0,
            height: 0
        };
    }
    
    handleMouseMove(e) {
        if (!this.isDrawing) return;
        
        const pos = this.getMousePos(e);
        
        if (this.isResizing && this.currentBox) {
            // Ridimensionamento
            const box = this.currentBox;
            switch (this.resizeHandle) {
                case 'tl':
                    box.width = box.width + (box.x - pos.x);
                    box.height = box.height + (box.y - pos.y);
                    box.x = pos.x;
                    box.y = pos.y;
                    break;
                case 'tr':
                    box.width = pos.x - box.x;
                    box.height = box.height + (box.y - pos.y);
                    box.y = pos.y;
                    break;
                case 'bl':
                    box.width = box.width + (box.x - pos.x);
                    box.height = pos.y - box.y;
                    box.x = pos.x;
                    break;
                case 'br':
                    box.width = pos.x - box.x;
                    box.height = pos.y - box.y;
                    break;
            }
            
            // Limiti minimi
            if (box.width < 10) box.width = 10;
            if (box.height < 10) box.height = 10;
            
            // Aggiorna box esistente
            const existingBox = this.boxes.find(b => b.id === box.id);
            if (existingBox) {
                Object.assign(existingBox, box);
                this.calculatePercentages(existingBox);
            }
        } else if (this.currentBox) {
            // Disegno nuovo box o spostamento
            const existingBox = this.boxes.find(b => b.id === this.currentBox.id);
            if (existingBox) {
                // Spostamento box esistente
                const dx = pos.x - this.startX;
                const dy = pos.y - this.startY;
                existingBox.x += dx;
                existingBox.y += dy;
                this.startX = pos.x;
                this.startY = pos.y;
                this.calculatePercentages(existingBox);
            } else {
                // Nuovo box
                this.currentBox.width = pos.x - this.currentBox.x;
                this.currentBox.height = pos.y - this.currentBox.y;
            }
        }
        
        this.draw();
    }
    
    handleMouseUp(e) {
        if (!this.isDrawing) return;
        
        if (this.currentBox && !this.boxes.find(b => b.id === this.currentBox.id)) {
            // Nuovo box completato
            if (Math.abs(this.currentBox.width) > 10 && Math.abs(this.currentBox.height) > 10) {
                // Normalizza coordinate (width/height possono essere negativi)
                if (this.currentBox.width < 0) {
                    this.currentBox.x += this.currentBox.width;
                    this.currentBox.width = Math.abs(this.currentBox.width);
                }
                if (this.currentBox.height < 0) {
                    this.currentBox.y += this.currentBox.height;
                    this.currentBox.height = Math.abs(this.currentBox.height);
                }
                
                this.calculatePercentages(this.currentBox);
                this.boxes.push(this.currentBox);
                this.selectedBoxId = this.currentBox.id;
                this.updateFieldSelect();
                this.updateBoxesList();
            }
        }
        
        this.isDrawing = false;
        this.isResizing = false;
        this.resizeHandle = null;
        this.currentBox = null;
        this.draw();
    }
    
    handleMouseLeave() {
        this.isDrawing = false;
        this.isResizing = false;
    }
    
    handleClick(e) {
        // Se non stiamo disegnando, seleziona box
        if (!this.isDrawing) {
            const pos = this.getMousePos(e);
            const clickedBox = this.getBoxAt(pos.x, pos.y);
            if (clickedBox) {
                this.selectedBoxId = clickedBox.id;
                this.updateFieldSelect();
                this.draw();
            } else {
                this.selectedBoxId = null;
                this.updateFieldSelect();
                this.draw();
            }
        }
    }
    
    calculatePercentages(box) {
        box.x_pct = box.x / this.canvas.width;
        box.y_pct = box.y / this.canvas.height;
        box.w_pct = box.width / this.canvas.width;
        box.h_pct = box.height / this.canvas.height;
    }
    
    deleteBox(boxId) {
        this.boxes = this.boxes.filter(b => b.id !== boxId);
        if (this.selectedBoxId === boxId) {
            this.selectedBoxId = null;
            this.updateFieldSelect();
        }
        this.updateBoxesList();
        this.draw();
    }
    
    updateFieldSelect() {
        const select = document.getElementById('field-select');
        if (!select) return;
        
        if (this.selectedBoxId) {
            const selectedBox = this.boxes.find(b => b.id === this.selectedBoxId);
            if (selectedBox) {
                select.value = selectedBox.field;
            }
        }
    }
    
    updateBoxesList() {
        const list = document.getElementById('boxes-list');
        if (!list) return;
        
        list.innerHTML = '';
        this.boxes.forEach(box => {
            const li = document.createElement('li');
            li.className = box.id === this.selectedBoxId ? 'selected' : '';
            li.innerHTML = `
                <span>${this.getFieldLabel(box.field)}</span>
                <button onclick="layoutTrainer.selectBox(${box.id})">Seleziona</button>
                <button onclick="layoutTrainer.deleteBox(${box.id})">Elimina</button>
            `;
            list.appendChild(li);
        });
    }
    
    selectBox(boxId) {
        this.selectedBoxId = boxId;
        this.updateFieldSelect();
        this.draw();
        this.updateBoxesList();
    }
    
    clearAll() {
        if (confirm('Eliminare tutti i box?')) {
            this.boxes = [];
            this.selectedBoxId = null;
            this.updateFieldSelect();
            this.updateBoxesList();
            this.draw();
        }
    }
    
    getFieldsData() {
        // Converte i box in formato per il backend
        const fields = {};
        this.boxes.forEach(box => {
            fields[box.field] = {
                page: 1, // Per ora solo pagina 1
                box: {
                    x_pct: box.x_pct,
                    y_pct: box.y_pct,
                    w_pct: box.w_pct,
                    h_pct: box.h_pct
                }
            };
        });
        return fields;
    }
}

// Inizializza quando il DOM è pronto
let layoutTrainer;
document.addEventListener('DOMContentLoaded', () => {
    layoutTrainer = new LayoutTrainer();
});
