import re
from datetime import datetime
from typing import Optional

from pydantic import BaseModel, Field, field_validator, model_validator


_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


class RegisterRequest(BaseModel):
    email: str = Field(..., min_length=3, max_length=254)
    password: str = Field(..., min_length=8, max_length=128)

    @field_validator("email")
    @classmethod
    def email_format(cls, v: str) -> str:
        e = v.strip().lower()
        if not _EMAIL_RE.match(e):
            raise ValueError("Invalid email address")
        return e


class LoginRequest(BaseModel):
    email: str = Field(..., min_length=3, max_length=254)
    password: str = Field(..., min_length=1, max_length=128)

    @field_validator("email")
    @classmethod
    def email_format(cls, v: str) -> str:
        e = v.strip().lower()
        if not _EMAIL_RE.match(e):
            raise ValueError("Invalid email address")
        return e


class UserPublic(BaseModel):
    id: str
    email: str
    created_at: datetime


class RegisterResponse(BaseModel):
    user: UserPublic
    access_token: str
    token_type: str = "bearer"


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"


class GoogleLoginRequest(BaseModel):
    id_token: Optional[str] = None
    credential: Optional[str] = None

    @model_validator(mode="after")
    def require_google_token(self):
        t = (self.id_token or self.credential or "").strip()
        if len(t) < 10:
            raise ValueError("Provide id_token or credential (Google ID token)")
        self.id_token = t
        return self
