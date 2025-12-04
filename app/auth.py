"""
Sistema di autenticazione per DDT Extractor
"""
import logging
from typing import Optional
from fastapi import Request, HTTPException, status
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from starlette.middleware.sessions import SessionMiddleware

from app.config import ADMIN_USERNAME, ADMIN_PASSWORD, SESSION_SECRET_KEY

logger = logging.getLogger(__name__)

# Security scheme per token (opzionale, usiamo sessioni)
security = HTTPBearer(auto_error=False)


def get_session_middleware() -> SessionMiddleware:
    """
    Crea il middleware per la gestione delle sessioni
    
    Returns:
        SessionMiddleware configurato
    """
    return SessionMiddleware(
        secret_key=SESSION_SECRET_KEY,
        max_age=7200,  # 2 ore (7200 secondi)
        same_site="lax",
        https_only=False  # True in produzione con HTTPS
    )


def verify_credentials(username: str, password: str) -> bool:
    """
    Verifica le credenziali di login
    
    Args:
        username: Username inserito
        password: Password inserita
        
    Returns:
        True se le credenziali sono corrette, False altrimenti
    """
    return username == ADMIN_USERNAME and password == ADMIN_PASSWORD


def is_authenticated(request: Request) -> bool:
    """
    Verifica se l'utente è autenticato
    
    Args:
        request: Request FastAPI
        
    Returns:
        True se l'utente è autenticato, False altrimenti
    """
    session = request.session
    return session.get("authenticated", False)


def require_auth(request: Request):
    """
    Verifica che l'utente sia autenticato, altrimenti solleva HTTPException
    
    Args:
        request: Request FastAPI
        
    Raises:
        HTTPException: Se l'utente non è autenticato
    """
    if not is_authenticated(request):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Autenticazione richiesta"
        )


def login_user(request: Request, username: str, password: str) -> bool:
    """
    Effettua il login dell'utente
    
    Args:
        request: Request FastAPI
        username: Username
        password: Password
        
    Returns:
        True se il login è riuscito, False altrimenti
    """
    if verify_credentials(username, password):
        request.session["authenticated"] = True
        request.session["username"] = username
        logger.info(f"✅ Login riuscito per utente: {username}")
        return True
    else:
        logger.warning(f"❌ Tentativo di login fallito per username: {username}")
        return False


def logout_user(request: Request):
    """
    Effettua il logout dell'utente
    
    Args:
        request: Request FastAPI
    """
    username = request.session.get("username", "Unknown")
    request.session.clear()
    logger.info(f"👋 Logout effettuato per utente: {username}")

