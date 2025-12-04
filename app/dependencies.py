"""
Dipendenze comuni per FastAPI
"""
from fastapi import Request, HTTPException, status
from app.auth import is_authenticated


async def require_authentication(request: Request):
    """
    Dependency per verificare che l'utente sia autenticato
    Usa questa dependency nei router per proteggere gli endpoint
    """
    if not is_authenticated(request):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Autenticazione richiesta"
        )
    return True

