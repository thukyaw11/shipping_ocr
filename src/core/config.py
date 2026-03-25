import os
from dotenv import load_dotenv

load_dotenv()

class Config:
    QWEN3_API_URL = os.getenv('QWEN3_API_URL')
    QWEN3_API_TOKEN = os.getenv('QWEN3_API_TOKEN')
    GEMINI_API_KEY = os.getenv('GEMINI_API_KEY')
    origin_raw = os.getenv("ALLOW_ORIGINS")
    ALLOW_ORIGINS = [origin.strip() for origin in origin_raw.split(",")]
    MONGODB_URL = os.getenv("MONGODB_URL", "mongodb://localhost:27017")
    DATABASE_NAME = os.getenv("DATABASE_NAME", "shipping_ocr")
    
