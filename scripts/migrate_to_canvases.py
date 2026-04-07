"""
Migration: wrap each legacy ocr_results document in its own canvas.

Run once against the database after deploying the canvas restructure:

    python scripts/migrate_to_canvases.py

What it does
------------
1. For every ocr_results doc that has no canvas_id:
   - Create a canvases doc (name = filename, timestamps from the ocr_result).
   - Set canvas_id and sort_order = 0 on the ocr_result.

2. For every highlights doc that has no canvas_id:
   - Look up the corresponding ocr_result to get its canvas_id.
   - Set canvas_id and ocr_result_id on the highlights doc.
   - Rename projectId -> canvasId inside each highlight object.
   - Remove the old project_id field from the highlights doc.

The script is idempotent: re-running it is safe because it only touches
documents that still lack a canvas_id.
"""

import asyncio
import os
import sys
from datetime import datetime

from bson import ObjectId
from dotenv import load_dotenv
from motor.motor_asyncio import AsyncIOMotorClient

load_dotenv()

MONGODB_URL = os.getenv("MONGODB_URL", "mongodb://localhost:27017")
DATABASE_NAME = os.getenv("DATABASE_NAME", "shipping_ocr")


async def migrate() -> None:
    client = AsyncIOMotorClient(MONGODB_URL)
    db = client[DATABASE_NAME]

    # ------------------------------------------------------------------
    # Phase 1: ocr_results without canvas_id
    # ------------------------------------------------------------------
    total_ocr = await db["ocr_results"].count_documents({"canvas_id": {"$exists": False}})
    print(f"[phase 1] {total_ocr} ocr_result(s) need a canvas")

    processed = 0
    async for doc in db["ocr_results"].find({"canvas_id": {"$exists": False}}):
        canvas_doc = {
            "user_id": doc.get("user_id", ""),
            "name": doc.get("filename", "Untitled"),
            "status": "active",
            "created_at": doc.get("created_at", datetime.utcnow()),
            "edited_at": doc.get("edited_at", datetime.utcnow()),
        }
        result = await db["canvases"].insert_one(canvas_doc)
        canvas_id_str = str(result.inserted_id)

        await db["ocr_results"].update_one(
            {"_id": doc["_id"]},
            {"$set": {"canvas_id": canvas_id_str, "sort_order": 0}},
        )
        processed += 1
        if processed % 50 == 0:
            print(f"  ... {processed}/{total_ocr} done")

    print(f"[phase 1] complete: {processed} ocr_result(s) migrated")

    # ------------------------------------------------------------------
    # Phase 2: highlights without canvas_id
    # ------------------------------------------------------------------
    total_hl = await db["highlights"].count_documents({"canvas_id": {"$exists": False}})
    print(f"[phase 2] {total_hl} highlights doc(s) need canvas_id")

    processed_hl = 0
    skipped_hl = 0
    async for hl_doc in db["highlights"].find({"canvas_id": {"$exists": False}}):
        old_project_id = hl_doc.get("project_id")
        if not old_project_id:
            print(f"  [skip] highlights {hl_doc['_id']}: no project_id field")
            skipped_hl += 1
            continue

        try:
            pdf_oid = ObjectId(old_project_id)
        except Exception:
            print(f"  [skip] highlights {hl_doc['_id']}: invalid project_id '{old_project_id}'")
            skipped_hl += 1
            continue

        pdf_doc = await db["ocr_results"].find_one(
            {"_id": pdf_oid}, {"canvas_id": 1}
        )
        if not pdf_doc or not pdf_doc.get("canvas_id"):
            print(
                f"  [skip] highlights {hl_doc['_id']}: "
                f"ocr_result {old_project_id} not found or has no canvas_id"
            )
            skipped_hl += 1
            continue

        canvas_id_str = pdf_doc["canvas_id"]
        ocr_result_id_str = old_project_id

        # Rename projectId -> canvasId inside each highlight object
        updated_highlights = []
        for h in hl_doc.get("highlights", []):
            h_copy = dict(h)
            if "projectId" in h_copy:
                h_copy["canvasId"] = canvas_id_str
                h_copy["ocrResultId"] = ocr_result_id_str
                del h_copy["projectId"]
            updated_highlights.append(h_copy)

        await db["highlights"].update_one(
            {"_id": hl_doc["_id"]},
            {
                "$set": {
                    "canvas_id": canvas_id_str,
                    "ocr_result_id": ocr_result_id_str,
                    "highlights": updated_highlights,
                },
                "$unset": {"project_id": ""},
            },
        )
        processed_hl += 1

    print(
        f"[phase 2] complete: {processed_hl} highlights doc(s) migrated, "
        f"{skipped_hl} skipped"
    )

    # ------------------------------------------------------------------
    # Phase 3: create indexes
    # ------------------------------------------------------------------
    print("[phase 3] ensuring indexes...")
    await db["canvases"].create_index([("user_id", 1), ("edited_at", -1)])
    await db["ocr_results"].create_index([("canvas_id", 1), ("sort_order", 1)])
    await db["ocr_results"].create_index([("user_id", 1), ("edited_at", -1)])
    await db["highlights"].create_index(
        [("user_id", 1), ("canvas_id", 1), ("ocr_result_id", 1)],
        unique=True,
    )
    print("[phase 3] indexes ready")

    client.close()
    print("Migration complete.")


if __name__ == "__main__":
    asyncio.run(migrate())
