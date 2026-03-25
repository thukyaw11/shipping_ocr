from motor.motor_asyncio import AsyncIOMotorClient
from .config import Config

class Database:
    client: AsyncIOMotorClient = None
    db = None

db = Database()

async def connect_to_mongo():
    db.client = AsyncIOMotorClient(Config.MONGODB_URL)
    db.db = db.client[Config.DATABASE_NAME]
    print("Connected to MongoDB")

async def close_mongo_connection():
    db.client.close()
    print("MongoDB connection closed")