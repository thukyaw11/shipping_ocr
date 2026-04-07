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
    # canvases: fast listing per user
    await db.db["canvases"].create_index([("user_id", 1), ("edited_at", -1)])
    # ocr_results: lookup by canvas (ordered) and by user
    await db.db["ocr_results"].create_index([("canvas_id", 1), ("sort_order", 1)])
    await db.db["ocr_results"].create_index([("user_id", 1), ("edited_at", -1)])
    # drop legacy highlights index (user_id + project_id) if it still exists
    try:
        await db.db["highlights"].drop_index("user_id_1_project_id_1")
        print("Dropped legacy highlights index user_id_1_project_id_1")
    except Exception:
        pass
    # highlights: unique per PDF within a canvas
    await db.db["highlights"].create_index(
        [("user_id", 1), ("canvas_id", 1), ("ocr_result_id", 1)],
        unique=True,
    )
    print("Connected to MongoDB")

async def close_mongo_connection():
    db.client.close()
    print("MongoDB connection closed")