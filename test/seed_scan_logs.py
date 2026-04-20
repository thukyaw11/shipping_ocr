"""Seed sample scan logs for a specific user."""

import os
from datetime import datetime, timedelta

from dotenv import load_dotenv
load_dotenv()

from pymongo import MongoClient

MONGODB_URL = os.getenv("MONGODB_URL", "mongodb://localhost:27017")
DATABASE_NAME = os.getenv("DATABASE_NAME", "shipping_ocr")

USER_ID = "69ca0c9e196eb3f20bd2adc5"
PRICE_PER_PAGE = 0.05

SAMPLES = [
    {
        "filename": "MAWB_176-12345678.pdf",
        "content_type": "application/pdf",
        "file_size_bytes": 318420,
        "total_pages": 2,
        "document_type": "MAWB",
        "status": "success",
        "processing_time_ms": 3812,
        "days_ago": 0,
    },
    {
        "filename": "HAWB_TG-98765.pdf",
        "content_type": "application/pdf",
        "file_size_bytes": 204800,
        "total_pages": 1,
        "document_type": "HAWB",
        "status": "success",
        "processing_time_ms": 2145,
        "days_ago": 1,
    },
    {
        "filename": "invoice_ACME_Apr2026.pdf",
        "content_type": "application/pdf",
        "file_size_bytes": 156000,
        "total_pages": 3,
        "document_type": "INVOICE",
        "status": "success",
        "processing_time_ms": 5203,
        "days_ago": 2,
    },
    {
        "filename": "manifest_BKK_YGN_0410.pdf",
        "content_type": "application/pdf",
        "file_size_bytes": 89000,
        "total_pages": 4,
        "document_type": "MANIFEST",
        "status": "success",
        "processing_time_ms": 7640,
        "days_ago": 3,
    },
    {
        "filename": "corrupted_scan.pdf",
        "content_type": "application/pdf",
        "file_size_bytes": 512,
        "total_pages": None,
        "document_type": None,
        "status": "failed",
        "error_message": "Failed to load PDF: file appears to be corrupted or truncated",
        "processing_time_ms": 288,
        "days_ago": 3,
    },
    {
        "filename": "HAWB_TG-11223.pdf",
        "content_type": "application/pdf",
        "file_size_bytes": 198400,
        "total_pages": 1,
        "document_type": "HAWB",
        "status": "success",
        "processing_time_ms": 1980,
        "days_ago": 5,
    },
    {
        "filename": "MAWB_020-44556677.pdf",
        "content_type": "application/pdf",
        "file_size_bytes": 420000,
        "total_pages": 2,
        "document_type": "MAWB",
        "status": "success",
        "processing_time_ms": 4310,
        "days_ago": 6,
    },
    {
        "filename": "unsupported_format.docx",
        "content_type": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        "file_size_bytes": 73000,
        "total_pages": None,
        "document_type": None,
        "status": "failed",
        "error_message": "Unsupported file type: application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        "processing_time_ms": 45,
        "days_ago": 7,
    },
]


def seed():
    client = MongoClient(MONGODB_URL)
    db = client[DATABASE_NAME]

    now = datetime.utcnow()
    docs = []
    for s in SAMPLES:
        pages = s.get("total_pages")
        is_success = s["status"] == "success"
        doc = {
            "user_id": USER_ID,
            "filename": s["filename"],
            "file_size_bytes": s["file_size_bytes"],
            "content_type": s["content_type"],
            "canvas_id": None,
            "ocr_result_id": None,
            "total_pages": pages,
            "document_type": s.get("document_type"),
            "status": s["status"],
            "error_message": s.get("error_message"),
            "processing_time_ms": s["processing_time_ms"],
            "price_per_page": PRICE_PER_PAGE if is_success and pages else None,
            "pages_charged": pages if is_success and pages else None,
            "total_cost": round(pages * PRICE_PER_PAGE, 6) if is_success and pages else None,
            "created_at": now - timedelta(days=s["days_ago"]),
        }
        docs.append(doc)

    result = db["scan_logs"].insert_many(docs)
    print(f"Inserted {len(result.inserted_ids)} scan log(s) for user {USER_ID}")
    for i, oid in enumerate(result.inserted_ids):
        print(f"  [{docs[i]['status']}] {docs[i]['filename']} → {oid}")

    client.close()


if __name__ == "__main__":
    seed()
