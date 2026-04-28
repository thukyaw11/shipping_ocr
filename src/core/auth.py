from typing import Optional

from fastapi import Header, HTTPException

from src.core.config import Config


def verify_service_key(
    x_service_key: Optional[str] = Header(None),
) -> None:
    """Validate the X-Service-Key header for internal service-to-service endpoints."""
    expected = Config.SERVICE_API_KEY
    if not expected:
        raise HTTPException(status_code=503, detail="Service API key not configured.")
    if x_service_key != expected:
        raise HTTPException(status_code=401, detail="Invalid service key.")
