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
 * @param {number} timeout - Timeout in millisecondi (default 5000ms)
 * @returns {Promise<Response>} Response della fetch
 * 
 * @throws {Error} Se la sessione è scaduta (401) o altri errori
 * @throws {Error} Se timeout superato
 */
async function apiFetch(url, options = {}, timeout = 5000) {
    // Crea AbortController per timeout
    const controller = new AbortController();
    const timeoutId = setTimeout(() => controller.abort(), timeout);
    
    // Assicura che credentials sia sempre 'include' per includere cookie di sessione
    const fetchOptions = {
        credentials: 'include',
        signal: controller.signal,
        ...options,
        // Merge headers se presenti
        headers: {
            'Content-Type': 'application/json',
            ...options.headers
        }
    };
    
    try {
        const response = await fetch(url, fetchOptions);
        clearTimeout(timeoutId);
        
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
        clearTimeout(timeoutId);
        
        // Se è già un errore 401 gestito, rilancia così com'è
        if (error.message && error.message.includes('Sessione scaduta')) {
            throw error;
        }
        
        // Gestione timeout
        if (error.name === 'AbortError') {
            const networkError = new Error(`Timeout chiamata API dopo ${timeout}ms: ${url}`);
            networkError.isNetworkError = true;
            throw networkError;
        }
        
        // Altri errori di rete/connessione
        const networkError = new Error(error.message || 'Errore di connessione');
        networkError.isNetworkError = true;
        networkError.originalError = error;
        console.error('Errore chiamata API:', error);
        throw networkError;
    }
}

/**
 * Helper per chiamate GET JSON con retry automatico per endpoint specifici
 */
async function apiGet(url, retryCount = 0) {
    const MAX_RETRIES = 1;
    const RETRY_DELAY = 1000;
    
    // Retry SOLO per GET /data e GET /api/watchdog-queue
    const retryableEndpoints = ['/data', '/api/watchdog-queue'];
    const shouldRetry = retryableEndpoints.some(endpoint => url.includes(endpoint));
    
    try {
        const response = await apiFetch(url, {
            method: 'GET'
        });
        
        // Gestione 5xx come network error
        if (response.status >= 500 && response.status < 600) {
            const networkError = new Error(`Errore server ${response.status}: ${response.statusText}`);
            networkError.isNetworkError = true;
            throw networkError;
        }
        
        if (!response.ok) {
            const error = new Error(`Errore ${response.status}: ${response.statusText}`);
            // Anche 4xx possono essere considerati network error se il server è down
            if (response.status >= 502 && response.status < 600) {
                error.isNetworkError = true;
            }
            throw error;
        }
        
        return await response.json();
    } catch (error) {
        // Retry solo per endpoint specifici e solo se è network error
        if (shouldRetry && retryCount < MAX_RETRIES && error.isNetworkError) {
            await new Promise(resolve => setTimeout(resolve, RETRY_DELAY));
            return apiGet(url, retryCount + 1);
        }
        
        // Marca come network error se non già marcato
        if (!error.isNetworkError && (error.message.includes('Timeout') || error.message.includes('network') || error.message.includes('fetch'))) {
            error.isNetworkError = true;
        }
        
        throw error;
    }
}

/**
 * Helper per chiamate POST JSON
 */
async function apiPost(url, data = {}) {
    const response = await apiFetch(url, {
        method: 'POST',
        body: JSON.stringify(data)
    });
    
    // Gestione 5xx come network error
    if (response.status >= 500 && response.status < 600) {
        const networkError = new Error(`Errore server ${response.status}: ${response.statusText}`);
        networkError.isNetworkError = true;
        throw networkError;
    }
    
    if (!response.ok) {
        const errorData = await response.json().catch(() => ({}));
        const error = new Error(errorData.detail || `Errore ${response.status}: ${response.statusText}`);
        if (response.status >= 502 && response.status < 600) {
            error.isNetworkError = true;
        }
        throw error;
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
    
    // Gestione 5xx come network error
    if (response.status >= 500 && response.status < 600) {
        const networkError = new Error(`Errore server ${response.status}: ${response.statusText}`);
        networkError.isNetworkError = true;
        throw networkError;
    }
    
    if (!response.ok) {
        let errorData = {};
        try {
            errorData = await response.json();
        } catch (parseError) {
            // Se il JSON non può essere parsato, usa statusText
            errorData = {};
        }
        
        // Estrai messaggio con priorità: detail > error > statusText
        // IMPORTANTE: FastAPI può restituire detail come array di oggetti di validazione
        let errorMessage = `Errore ${response.status}: ${response.statusText}`;
        
        if (errorData.detail) {
            if (Array.isArray(errorData.detail)) {
                // Caso validazione FastAPI: detail è un array di oggetti
                // Estrai i messaggi da ogni oggetto di validazione
                const messages = errorData.detail.map(item => {
                    const loc = Array.isArray(item.loc) ? item.loc.join('.') : '';
                    const msg = item.msg || 'Errore di validazione';
                    return loc ? `${loc}: ${msg}` : msg;
                });
                errorMessage = messages.join('; ') || errorMessage;
            } else if (typeof errorData.detail === 'string') {
                // Caso normale: detail è una stringa
                errorMessage = errorData.detail;
            }
        } else if (errorData.error) {
            errorMessage = typeof errorData.error === 'string' ? errorData.error : String(errorData.error);
        }
        
        const error = new Error(errorMessage);
        // Copia eventuali proprietà aggiuntive dall'errore
        if (errorData.detail) error.detail = errorData.detail;
        if (errorData.error) error.error = errorData.error;
        if (response.status >= 502 && response.status < 600) {
            error.isNetworkError = true;
        }
        throw error;
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
    
    // Gestione 5xx come network error
    if (response.status >= 500 && response.status < 600) {
        const networkError = new Error(`Errore server ${response.status}: ${response.statusText}`);
        networkError.isNetworkError = true;
        throw networkError;
    }
    
    if (!response.ok) {
        const errorData = await response.json().catch(() => ({}));
        const error = new Error(errorData.detail || `Errore ${response.status}: ${response.statusText}`);
        if (response.status >= 502 && response.status < 600) {
            error.isNetworkError = true;
        }
        throw error;
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
