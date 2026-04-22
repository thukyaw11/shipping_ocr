from datetime import datetime
from typing import Optional

from bson import ObjectId

from src.core.database import db


class OCRResultRepository:
    _col = "ocr_results"

    async def create(self, doc: dict) -> str:
        result = await db.db[self._col].insert_one(doc)
        return str(result.inserted_id)

    async def get_by_id(self, ocr_result_id: str, user_id: str) -> Optional[dict]:
        try:
            oid = ObjectId(ocr_result_id)
        except Exception:
            return None
        return await db.db[self._col].find_one({"_id": oid, "user_id": user_id})

    async def find_by_id_in_canvas(
        self, ocr_result_id: str, canvas_id: str, user_id: str
    ) -> Optional[dict]:
        """Used for ownership checks; returns {"_id": ...} projection only."""
        try:
            oid = ObjectId(ocr_result_id)
        except Exception:
            return None
        return await db.db[self._col].find_one(
            {"_id": oid, "canvas_id": canvas_id, "user_id": user_id},
            {"_id": 1},
        )

    async def find_in_canvas(self, canvas_id: str, user_id: str) -> list[dict]:
        cursor = db.db[self._col].find(
            {"canvas_id": canvas_id, "user_id": user_id, "is_deleted": {"$ne": True}}
        ).sort("sort_order", 1)
        return [doc async for doc in cursor]

    async def count_in_canvas(self, canvas_id: str) -> int:
        """Count non-deleted docs — used for pdf_count display."""
        return await db.db[self._col].count_documents(
            {"canvas_id": canvas_id, "is_deleted": {"$ne": True}}
        )

    async def count_all_in_canvas(self, canvas_id: str) -> int:
        """Count all docs including deleted — used to derive sort_order for new inserts."""
        return await db.db[self._col].count_documents({"canvas_id": canvas_id})

    async def distinct_canvas_ids_by_sub_page_type(
        self, user_id: str, sub_page_type: str
    ) -> list[str]:
        return await db.db[self._col].distinct(
            "canvas_id",
            {"user_id": user_id, "data.sub_page_type": sub_page_type},
        )

    async def update_page_field(
        self,
        ocr_result_id: str,
        user_id: str,
        array_idx: int,
        field: str,
        value: str,
    ) -> Optional[dict]:
        """Set data[array_idx].<field> = value and bump edited_at."""
        try:
            oid = ObjectId(ocr_result_id)
        except Exception:
            return None
        now = datetime.utcnow()
        await db.db[self._col].update_one(
            {"_id": oid, "user_id": user_id},
            {"$set": {f"data.{array_idx}.{field}": value, "edited_at": now}},
        )
        return await db.db[self._col].find_one({"_id": oid, "user_id": user_id})

    async def soft_delete(self, ocr_result_id: str, user_id: str) -> None:
        try:
            oid = ObjectId(ocr_result_id)
        except Exception:
            return
        now = datetime.utcnow()
        await db.db[self._col].update_one(
            {"_id": oid, "user_id": user_id},
            {"$set": {"is_deleted": True, "deleted_at": now, "edited_at": now}},
        )


ocr_result_repo = OCRResultRepository()
