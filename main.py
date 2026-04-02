import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from src.api.v1.api import api_router
from src.core.config import Config
from src.core.database import close_mongo_connection, connect_to_mongo
from src.core.exception_handlers import register_exception_handlers

logging.getLogger("surya").setLevel(logging.WARNING)
logging.getLogger("surya.recognition").setLevel(logging.WARNING)
logging.getLogger("surya.detection").setLevel(logging.WARNING)
logging.getLogger("transformers").setLevel(logging.ERROR)


@asynccontextmanager
async def lifespan(app: FastAPI):
    await connect_to_mongo()
    yield
    await close_mongo_connection()


app = FastAPI(title="Shipping Bill OCR", version="1.0.0", lifespan=lifespan)
register_exception_handlers(app)
app.include_router(api_router, prefix="/api/v1")

app.add_middleware(
    CORSMiddleware,
    allow_origins=Config.ALLOW_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=False)
