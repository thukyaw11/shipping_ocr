from motor.motor_asyncio import AsyncIOMotorClient
from .config import Config

class Database:
    client: AsyncIOMotorClient = None
    db = None

db = Database()

async def connect_to_mongo():
    db.client = AsyncIOMotorClient(Config.MONGODB_URL)
    db.db = db.client[Config.DATABASE_NAME]
    await db.db["users"].create_index("email", unique=True)
    await db.db["users"].create_index("google_sub", unique=True, sparse=True)
    await db.db["ocr_results"].create_index([("user_id", 1), ("edited_at", -1)])
    print("Connected to MongoDB")

async def close_mongo_connection():
    db.client.close()
    print("MongoDB connection closed")