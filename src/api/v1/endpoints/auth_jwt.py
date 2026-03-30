import secrets

from fastapi import APIRouter, Depends, HTTPException
from fastapi.concurrency import run_in_threadpool
from fastapi.security import OAuth2PasswordRequestForm
from pymongo.errors import DuplicateKeyError

from src.core.auth import create_access_token
from src.core.config import Config
from src.models.auth_schemas import (
    GoogleLoginRequest,
    LoginRequest,
    RegisterRequest,
    RegisterResponse,
    TokenResponse,
    UserPublic,
)
from src.services import user_service
from src.services import google_id_token

router = APIRouter()


def _token_for_user(doc: dict) -> TokenResponse:
    token = create_access_token(
        subject=str(doc["_id"]),
        extra_claims={"email": doc["email"]},
    )
    return TokenResponse(access_token=token)


@router.post("/register", response_model=RegisterResponse, status_code=201)
async def register(body: RegisterRequest):
    try:
        user_doc = await user_service.create_user(body.email, body.password)
    except DuplicateKeyError:
        raise HTTPException(status_code=409, detail="Email already registered")
    return RegisterResponse(
        user=UserPublic(**user_service.user_public(user_doc)),
        access_token=_token_for_user(user_doc).access_token,
    )


@router.post("/login", response_model=TokenResponse)
async def login_json(body: LoginRequest):
    user_doc = await user_service.get_user_by_email(body.email)
    if not user_doc:
        raise HTTPException(status_code=401, detail="Incorrect email or password")
    if not user_doc.get("password_hash"):
        raise HTTPException(
            status_code=401,
            detail="This account uses Google sign-in",
        )
    if not user_service.verify_password(body.password, user_doc["password_hash"]):
        raise HTTPException(status_code=401, detail="Incorrect email or password")
    return _token_for_user(user_doc)


@router.post("/token", response_model=TokenResponse)
async def issue_token_oauth2(form_data: OAuth2PasswordRequestForm = Depends()):
    email = (form_data.username or "").strip().lower()
    user_doc = await user_service.get_user_by_email(email)
    if user_doc and user_doc.get("password_hash") and user_service.verify_password(
        form_data.password, user_doc["password_hash"]
    ):
        return _token_for_user(user_doc)

    if Config.AUTH_DEV_USERNAME and Config.AUTH_DEV_PASSWORD:
        if (
            secrets.compare_digest(form_data.username, Config.AUTH_DEV_USERNAME)
            and secrets.compare_digest(form_data.password, Config.AUTH_DEV_PASSWORD)
        ):
            token = create_access_token(subject=form_data.username)
            return TokenResponse(access_token=token)

    raise HTTPException(status_code=401, detail="Incorrect email or password")


@router.post("/google", response_model=RegisterResponse)
async def google_login_or_register(body: GoogleLoginRequest):
    claims = await run_in_threadpool(
        google_id_token.verify_google_id_token,
        body.id_token,
    )
    try:
        user_doc = await user_service.find_or_create_google_user(
            claims["email"],
            claims["google_sub"],
        )
    except ValueError as e:
        if str(e) == "google_sub_conflict":
            raise HTTPException(
                status_code=409,
                detail="Email is already linked to another Google account",
            ) from e
        raise
    return RegisterResponse(
        user=UserPublic(**user_service.user_public(user_doc)),
        access_token=_token_for_user(user_doc).access_token,
    )
