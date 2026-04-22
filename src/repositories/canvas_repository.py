from datetime import datetime
from typing import Optional

from bson import ObjectId

from src.core.database import db


class CanvasRepository:
    _col = "canvases"

    async def create(self, doc: dict) -> str:
        result = await db.db[self._col].insert_one(doc)
        return str(result.inserted_id)

    async def get_by_id(self, canvas_id: str, user_id: str) -> Optional[dict]:
        try:
            oid = ObjectId(canvas_id)
        except Exception:
            return None
        return await db.db[self._col].find_one(
            {"_id": oid, "user_id": user_id, "is_deleted": {"$ne": True}}
        )

    async def list_paginated(
        self,
        user_id: str,
        skip: int,
        limit: int,
        filter_ids: Optional[list] = None,
    ) -> list[dict]:
        query: dict = {"user_id": user_id, "is_deleted": {"$ne": True}}
        if filter_ids is not None:
            query["_id"] = {"$in": filter_ids}
        cursor = db.db[self._col].find(query).sort("edited_at", -1).skip(skip).limit(limit)
        return [doc async for doc in cursor]

    async def touch(self, canvas_id: str) -> None:
        try:
            oid = ObjectId(canvas_id)
        except Exception:
            return
        await db.db[self._col].update_one(
            {"_id": oid},
            {"$set": {"edited_at": datetime.utcnow()}},
        )

    async def rename(self, canvas_id: str, user_id: str, name: str) -> Optional[dict]:
        try:
            oid = ObjectId(canvas_id)
        except Exception:
            return None
        now = datetime.utcnow()
        await db.db[self._col].update_one(
            {"_id": oid, "user_id": user_id},
            {"$set": {"name": name, "edited_at": now}},
        )
        return await db.db[self._col].find_one({"_id": oid, "user_id": user_id})

    async def soft_delete(self, canvas_id: str, user_id: str) -> None:
        try:
            oid = ObjectId(canvas_id)
        except Exception:
            return
        now = datetime.utcnow()
        await db.db[self._col].update_one(
            {"_id": oid, "user_id": user_id},
            {"$set": {"is_deleted": True, "deleted_at": now, "edited_at": now}},
        )


canvas_repo = CanvasRepository()
