from typing import Any, Dict

from fastapi import HTTPException

from src.core.config import Config


def verify_google_id_token(id_token_str: str) -> Dict[str, Any]:
    if not Config.GOOGLE_CLIENT_ID:
        raise HTTPException(
            status_code=503,
            detail="Google sign-in is not configured (GOOGLE_CLIENT_ID missing).",
        )
    try:
        from google.oauth2 import id_token as google_id_token
        from google.auth.transport import requests as google_requests
    except ImportError as e:
        raise HTTPException(
            status_code=503,
            detail="google-auth package is required for Google sign-in. pip install google-auth",
        ) from e

    request = google_requests.Request()
    try:
        idinfo = google_id_token.verify_oauth2_token(
            id_token_str,
            request,
            Config.GOOGLE_CLIENT_ID,
        )
    except ValueError as e:
        raise HTTPException(status_code=401, detail="Invalid Google ID token") from e

    email = idinfo.get("email")
    if not email:
        raise HTTPException(status_code=401, detail="Google token has no email claim")

    if not idinfo.get("email_verified", False):
        raise HTTPException(
            status_code=401,
            detail="Google email must be verified before sign-in",
        )

    sub = idinfo.get("sub")
    if not sub:
        raise HTTPException(status_code=401, detail="Google token has no subject")

    return {
        "email": str(email).strip().lower(),
        "google_sub": str(sub),
    }
