from datetime import datetime
from typing import Optional

from bson import ObjectId

from src.core.database import db


class ImportEntryRepository:
    _col = "import_entries"

    async def create(self, doc: dict) -> str:
        result = await db.db[self._col].insert_one(doc)
        return str(result.inserted_id)

    async def next_version(self, canvas_id: str) -> int:
        """Count all entries including deleted to derive next version number."""
        count = await db.db[self._col].count_documents({"canvas_id": canvas_id})
        return count + 1

    async def get_active(self, canvas_id: str, user_id: str) -> Optional[dict]:
        """Return the most recently created non-deleted entry for the canvas."""
        return await db.db[self._col].find_one(
            {"canvas_id": canvas_id, "user_id": user_id, "is_deleted": {"$ne": True}},
            sort=[("created_at", -1)],
        )

    async def list_all(self, canvas_id: str, user_id: str) -> list[dict]:
        """Return all non-deleted entries newest first."""
        cursor = db.db[self._col].find(
            {"canvas_id": canvas_id, "user_id": user_id, "is_deleted": {"$ne": True}}
        ).sort("created_at", -1)
        return [doc async for doc in cursor]

    async def get_by_id(self, entry_id: str, user_id: str) -> Optional[dict]:
        try:
            oid = ObjectId(entry_id)
        except Exception:
            return None
        return await db.db[self._col].find_one(
            {"_id": oid, "user_id": user_id, "is_deleted": {"$ne": True}}
        )

    async def soft_delete(self, entry_id: str, user_id: str) -> None:
        try:
            oid = ObjectId(entry_id)
        except Exception:
            return
        now = datetime.utcnow()
        await db.db[self._col].update_one(
            {"_id": oid, "user_id": user_id},
            {"$set": {"is_deleted": True, "deleted_at": now, "edited_at": now}},
        )


import_entry_repo = ImportEntryRepository()
