/**
 * Wrapper centralizzato per chiamate API con gestione credenziali e errori
 * 
 * IMPORTANTE: Tutte le chiamate API devono usare questa funzione per:
 * - Includere automaticamente le credenziali di sessione (cookie)
 * - Gestire correttamente gli errori 401 (sessione scaduta)
 * - Fornire una UX migliore in caso di errori
 */

/**
 * Esegue una chiamata fetch con credenziali incluse e gestione errori migliorata
 * 
 * @param {string} url - URL della richiesta
 * @param {object} options - Opzioni fetch standard (method, headers, body, ecc.)
 * @returns {Promise<Response>} Response della fetch
 * 
 * @throws {Error} Se la sessione è scaduta (401) o altri errori
 */
async function apiFetch(url, options = {}) {
    // Assicura che credentials sia sempre 'include' per includere cookie di sessione
    const fetchOptions = {
        credentials: 'include',
        ...options,
        // Merge headers se presenti
        headers: {
            'Content-Type': 'application/json',
            ...options.headers
        }
    };
    
    try {
        const response = await fetch(url, fetchOptions);
        
        // Gestione speciale per 401 (sessione scaduta)
        // IMPORTANTE: NON fare redirect se siamo già sulla pagina di login
        // (evita loop infinito quando il login stesso restituisce 401)
        if (response.status === 401) {
            // Escludi /login dal redirect automatico per evitare loop infiniti
            const isLoginPage = url.includes('/login') || window.location.pathname === '/login';
            
            if (!isLoginPage) {
                // Mostra messaggio all'utente
                const errorMessage = 'Sessione scaduta. Effettua nuovamente il login.';
                
                // Cerca di mostrare il messaggio nella pagina corrente
                const errorContainer = document.getElementById('error-message') || 
                                     document.querySelector('.error-message') ||
                                     document.body;
                
                if (errorContainer) {
                    const errorDiv = document.createElement('div');
                    errorDiv.className = 'error-message';
                    errorDiv.style.cssText = 'background-color: #f44336; color: white; padding: 15px; margin: 10px; border-radius: 4px; text-align: center;';
                    errorDiv.textContent = errorMessage;
                    errorContainer.insertBefore(errorDiv, errorContainer.firstChild);
                    
                    // Reindirizza al login dopo 2 secondi
                    setTimeout(() => {
                        window.location.href = '/login';
                    }, 2000);
                } else {
                    // Fallback: reindirizza immediatamente
                    window.location.href = '/login';
                }
                
                // Solleva errore per interrompere l'esecuzione
                throw new Error(errorMessage);
            }
            // Se siamo su /login, lascia che il codice chiamante gestisca l'errore 401
            // (non fare redirect per evitare loop)
        }
        
        return response;
        
    } catch (error) {
        // Se è già un errore 401 gestito, rilancia così com'è
        if (error.message && error.message.includes('Sessione scaduta')) {
            throw error;
        }
        
        // Altri errori di rete/connessione
        console.error('Errore chiamata API:', error);
        throw error;
    }
}

/**
 * Helper per chiamate GET JSON
 */
async function apiGet(url) {
    const response = await apiFetch(url, {
        method: 'GET'
    });
    
    if (!response.ok) {
        throw new Error(`Errore ${response.status}: ${response.statusText}`);
    }
    
    return await response.json();
}

/**
 * Helper per chiamate POST JSON
 */
async function apiPost(url, data = {}) {
    const response = await apiFetch(url, {
        method: 'POST',
        body: JSON.stringify(data)
    });
    
    if (!response.ok) {
        const errorData = await response.json().catch(() => ({}));
        throw new Error(errorData.detail || `Errore ${response.status}: ${response.statusText}`);
    }
    
    return await response.json();
}

/**
 * Helper per chiamate POST FormData (per upload file)
 */
async function apiPostForm(url, formData) {
    const response = await apiFetch(url, {
        method: 'POST',
        body: formData,
        // Rimuovi Content-Type per permettere al browser di impostare il boundary
        headers: {}
    });
    
    if (!response.ok) {
        const errorData = await response.json().catch(() => ({}));
        throw new Error(errorData.detail || `Errore ${response.status}: ${response.statusText}`);
    }
    
    return await response.json();
}

/**
 * Helper per chiamate PUT JSON
 */
async function apiPut(url, data = {}) {
    const response = await apiFetch(url, {
        method: 'PUT',
        body: JSON.stringify(data)
    });
    
    if (!response.ok) {
        const errorData = await response.json().catch(() => ({}));
        throw new Error(errorData.detail || `Errore ${response.status}: ${response.statusText}`);
    }
    
    return await response.json();
}

/**
 * Helper per chiamate DELETE
 */
async function apiDelete(url) {
    const response = await apiFetch(url, {
        method: 'DELETE'
    });
    
    if (!response.ok) {
        const errorData = await response.json().catch(() => ({}));
        throw new Error(errorData.detail || `Errore ${response.status}: ${response.statusText}`);
    }
    
    return await response.json();
}
