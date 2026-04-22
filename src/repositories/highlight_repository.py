from typing import Optional

from src.core.database import db


class HighlightRepository:
    _col = "highlights"

    async def get(self, user_id: str, canvas_id: str, ocr_result_id: str) -> Optional[dict]:
        return await db.db[self._col].find_one(
            {"user_id": user_id, "canvas_id": canvas_id, "ocr_result_id": ocr_result_id}
        )

    async def upsert(
        self,
        user_id: str,
        canvas_id: str,
        ocr_result_id: str,
        highlights: list,
        now: str,
    ) -> None:
        await db.db[self._col].update_one(
            {"user_id": user_id, "canvas_id": canvas_id, "ocr_result_id": ocr_result_id},
            {
                "$set": {
                    "user_id": user_id,
                    "canvas_id": canvas_id,
                    "ocr_result_id": ocr_result_id,
                    "highlights": highlights,
                    "updated_at": now,
                },
                "$setOnInsert": {"created_at": now},
            },
            upsert=True,
        )


highlight_repo = HighlightRepository()
