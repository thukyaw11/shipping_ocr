from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Optional

from fastapi import Depends, HTTPException
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from jose import JWTError, jwt

from src.core.config import Config

security = HTTPBearer()


def _require_jwt_secret() -> str:
    if not Config.JWT_SECRET_KEY:
        raise HTTPException(
            status_code=503,
            detail="JWT is not configured: set JWT_SECRET_KEY in the environment.",
        )
    return Config.JWT_SECRET_KEY


def create_access_token(
    subject: str,
    extra_claims: Optional[Dict[str, Any]] = None,
) -> str:
    secret = _require_jwt_secret()
    now = datetime.now(timezone.utc)
    expire = now + timedelta(minutes=Config.JWT_ACCESS_TOKEN_EXPIRE_MINUTES)
    payload: Dict[str, Any] = {
        "sub": subject,
        "iat": int(now.timestamp()),
        "exp": int(expire.timestamp()),
    }
    if extra_claims:
        payload.update(extra_claims)
    return jwt.encode(payload, secret, algorithm=Config.JWT_ALGORITHM)


def verify_jwt(
    res: HTTPAuthorizationCredentials = Depends(security),
) -> Dict[str, Any]:
    secret = _require_jwt_secret()
    token = res.credentials
    try:
        payload = jwt.decode(
            token,
            secret,
            algorithms=[Config.JWT_ALGORITHM],
            options={"verify_aud": False},
        )
        return payload
    except JWTError:
        raise HTTPException(status_code=401, detail="Invalid or expired token")


verify_token = verify_jwt
