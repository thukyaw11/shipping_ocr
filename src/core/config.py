import os
from dotenv import load_dotenv

load_dotenv()

class Config:
    QWEN3_API_URL = os.getenv('QWEN3_API_URL')
    QWEN3_API_TOKEN = os.getenv('QWEN3_API_TOKEN')
    GEMINI_API_KEY = os.getenv('GEMINI_API_KEY')
    GEMINI_MODEL = os.getenv('GEMINI_MODEL', 'gemini-2.5-flash')
    # Lighter model used only for page/doc classification (not checklist extraction)
    GEMINI_CLASSIFICATION_MODEL = os.getenv('GEMINI_CLASSIFICATION_MODEL', 'gemini-2.5-flash-lite')
    # Document/page classification: auto | gemini | ollama
    CLASSIFICATION_PROVIDER = os.getenv('CLASSIFICATION_PROVIDER', 'auto')
    OLLAMA_CLASSIFICATION_MODEL = os.getenv(
        'OLLAMA_CLASSIFICATION_MODEL',
    ) or os.getenv('DOC_TYPE_MODEL', 'qwen3.5:397b-cloud')
    DEBUG_CLASSIFICATION = os.getenv(
        'DEBUG_CLASSIFICATION', 'false').lower() == 'true'
    origin_raw = os.getenv("ALLOW_ORIGINS")
    ALLOW_ORIGINS = [origin.strip() for origin in origin_raw.split(",")]
    MONGODB_URL = os.getenv("MONGODB_URL", "mongodb://localhost:27017")
    DATABASE_NAME = os.getenv("DATABASE_NAME", "shipping_ocr")
    R2_ACCESS_KEY_ID: str = os.getenv("R2_ACCESS_KEY_ID")
    R2_SECRET_ACCESS_KEY: str = os.getenv("R2_SECRET_ACCESS_KEY")
    R2_ENDPOINT_URL: str = os.getenv("R2_ENDPOINT_URL")
    R2_BUCKET_NAME: str = os.getenv("R2_BUCKET_NAME")

    JWT_SECRET_KEY: str = os.getenv("JWT_SECRET_KEY", "")
    JWT_ALGORITHM: str = os.getenv("JWT_ALGORITHM", "HS256")
    JWT_ACCESS_TOKEN_EXPIRE_MINUTES: int = int(
        os.getenv("JWT_ACCESS_TOKEN_EXPIRE_MINUTES", "1440")
    )

    AUTH_DEV_USERNAME: str = os.getenv("AUTH_DEV_USERNAME", "")
    AUTH_DEV_PASSWORD: str = os.getenv("AUTH_DEV_PASSWORD", "")

    AUTH0_DOMAIN: str = os.getenv("AUTH0_DOMAIN", "")
    AUTH0_AUDIENCE: str = os.getenv("AUTH0_AUDIENCE", "")
    AUTH0_PUBLIC_KEY: str = os.getenv("AUTH0_PUBLIC_KEY", "")

    GOOGLE_CLIENT_ID: str = os.getenv("GOOGLE_CLIENT_ID", "")
    # Tolerates small clock drift between server and Google (iat/nbf checks). Set 0 to disable.
    GOOGLE_ID_TOKEN_CLOCK_SKEW_SECONDS: int = int(
        os.getenv("GOOGLE_ID_TOKEN_CLOCK_SKEW_SECONDS", "120"),
    )

    # Billing — default price charged per scanned page (USD). Overridable via DB settings.
    PRICE_PER_PAGE: float = float(os.getenv("PRICE_PER_PAGE", "0.05"))
    # When true, /auth/google 401 responses include google-auth verify error text (dev only).
    DEBUG_GOOGLE_AUTH: bool = os.getenv(
        "DEBUG_GOOGLE_AUTH", "false").lower() == "true"

