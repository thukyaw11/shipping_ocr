"""
MCP server for Shipping Bill OCR backend.

Exposes MongoDB query tools so LM Studio (or any MCP-compatible client)
can read OCR results, checklists, and cross-validation data without going
through HTTP auth.

Usage:
    python mcp_server.py

Or via npx/uvx after adding to mcp.json:
    uv run mcp_server.py
"""

import os
from datetime import datetime
from typing import Optional

from bson import ObjectId
from mcp.server.fastmcp import FastMCP
from pymongo import MongoClient
from pymongo.collection import Collection

MONGODB_URL = os.getenv("MONGODB_URL", "mongodb://localhost:27017")
DATABASE_NAME = os.getenv("DATABASE_NAME", "shipping_ocr")

_port = int(os.getenv("MCP_PORT", "8001"))
_host = os.getenv("MCP_HOST", "0.0.0.0")

mcp = FastMCP("Shipping Bill OCR", host=_host, port=_port)

_client: Optional[MongoClient] = None


def _get_collection(name: str) -> Collection:
    global _client
    if _client is None:
        _client = MongoClient(MONGODB_URL)
    return _client[DATABASE_NAME][name]


def _serialize(doc: dict) -> dict:
    """Convert ObjectId and datetime fields to strings."""
    if "_id" in doc:
        doc["id"] = str(doc.pop("_id"))
    for key, value in list(doc.items()):
        if isinstance(value, ObjectId):
            doc[key] = str(value)
        elif isinstance(value, datetime):
            doc[key] = value.isoformat()
        elif isinstance(value, dict):
            doc[key] = _serialize(value)
        elif isinstance(value, list):
            doc[key] = [
                _serialize(item) if isinstance(item, dict) else item
                for item in value
            ]
    return doc


def _strip_page_text(doc: dict) -> dict:
    """Remove raw text_lines from pages to keep responses concise."""
    for page in doc.get("data") or []:
        page.pop("text_lines", None)
        page.pop("text_blocks", None)
    return doc


# ---------------------------------------------------------------------------
# Tools
# ---------------------------------------------------------------------------


@mcp.tool()
def list_results(
    limit: int = 20,
    document_type: Optional[str] = None,
    filename: Optional[str] = None,
    user_id: Optional[str] = None,
) -> list[dict]:
    """
    List OCR results from the database, newest first.

    Args:
        limit: Maximum number of results to return (default 20, max 100).
        document_type: Filter by document type, e.g. 'MAWB', 'HAWB', 'Invoice', 'Manifest'.
        filename: Filter by partial filename match (case-insensitive).
        user_id: Filter by user ID (optional).
    """
    limit = min(limit, 100)
    query: dict = {}
    if document_type:
        query["document_type"] = {"$regex": document_type, "$options": "i"}
    if filename:
        query["filename"] = {"$regex": filename, "$options": "i"}
    if user_id:
        query["user_id"] = user_id

    col = _get_collection("ocr_results")
    cursor = col.find(query, {"data": 0}).sort("created_at", -1).limit(limit)

    return [_serialize(doc) for doc in cursor]


@mcp.tool()
def get_result(doc_id: str) -> dict:
    """
    Get the full OCR result for a specific document, including checklists
    and cross-validation results.

    Args:
        doc_id: The MongoDB document ID (24-character hex string).
    """
    try:
        oid = ObjectId(doc_id)
    except Exception:
        return {"error": f"Invalid document ID: {doc_id}"}

    col = _get_collection("ocr_results")
    doc = col.find_one({"_id": oid})
    if not doc:
        return {"error": f"Document not found: {doc_id}"}

    doc = _serialize(doc)
    doc = _strip_page_text(doc)
    return doc


@mcp.tool()
def get_checklist(doc_id: str) -> dict:
    """
    Get only the checklist fields extracted from a document.
    Returns one checklist entry per page.

    Args:
        doc_id: The MongoDB document ID.
    """
    try:
        oid = ObjectId(doc_id)
    except Exception:
        return {"error": f"Invalid document ID: {doc_id}"}

    col = _get_collection("ocr_results")
    doc = col.find_one({"_id": oid}, {"data.checklist": 1, "document_type": 1, "filename": 1})
    if not doc:
        return {"error": f"Document not found: {doc_id}"}

    pages = doc.get("data") or []
    checklists = [
        {"page": i + 1, "checklist": p.get("checklist")}
        for i, p in enumerate(pages)
    ]
    return {
        "id": str(doc["_id"]),
        "filename": doc.get("filename"),
        "document_type": doc.get("document_type"),
        "checklists": checklists,
    }


@mcp.tool()
def search_results(
    query: str,
    limit: int = 10,
) -> list[dict]:
    """
    Search OCR results by filename or document type using a text query.

    Args:
        query: Search term matched against filename and document_type fields.
        limit: Maximum number of results to return (default 10, max 50).
    """
    limit = min(limit, 50)
    col = _get_collection("ocr_results")
    cursor = col.find(
        {
            "$or": [
                {"filename": {"$regex": query, "$options": "i"}},
                {"document_type": {"$regex": query, "$options": "i"}},
            ]
        },
        {"data": 0},
    ).sort("created_at", -1).limit(limit)

    return [_serialize(doc) for doc in cursor]


@mcp.tool()
def get_stats() -> dict:
    """
    Return aggregate statistics: total documents, counts by document type,
    counts by status, and date of most recent upload.
    """
    col = _get_collection("ocr_results")

    total = col.count_documents({})

    by_type_pipeline = [
        {"$group": {"_id": "$document_type", "count": {"$sum": 1}}},
        {"$sort": {"count": -1}},
    ]
    by_type = {
        (row["_id"] or "unknown"): row["count"]
        for row in col.aggregate(by_type_pipeline)
    }

    by_status_pipeline = [
        {"$group": {"_id": "$status", "count": {"$sum": 1}}},
    ]
    by_status = {
        (row["_id"] or "unknown"): row["count"]
        for row in col.aggregate(by_status_pipeline)
    }

    latest = col.find_one({}, {"created_at": 1}, sort=[("created_at", -1)])
    latest_at = None
    if latest and latest.get("created_at"):
        val = latest["created_at"]
        latest_at = val.isoformat() if isinstance(val, datetime) else str(val)

    return {
        "total_documents": total,
        "by_document_type": by_type,
        "by_status": by_status,
        "latest_upload_at": latest_at,
    }


@mcp.tool()
def get_page_text(doc_id: str, page_number: int = 1) -> dict:
    """
    Get the raw OCR text lines for a specific page of a document.

    Args:
        doc_id: The MongoDB document ID.
        page_number: 1-based page number.
    """
    try:
        oid = ObjectId(doc_id)
    except Exception:
        return {"error": f"Invalid document ID: {doc_id}"}

    col = _get_collection("ocr_results")
    doc = col.find_one({"_id": oid}, {"data": 1, "filename": 1})
    if not doc:
        return {"error": f"Document not found: {doc_id}"}

    pages = doc.get("data") or []
    if page_number < 1 or page_number > len(pages):
        return {"error": f"Page {page_number} not found. Document has {len(pages)} page(s)."}

    page = pages[page_number - 1]
    lines = page.get("text_lines") or []
    text = "\n".join(
        line.get("text", "") for line in lines if line.get("text")
    )
    return {
        "id": str(doc["_id"]),
        "filename": doc.get("filename"),
        "page": page_number,
        "total_pages": len(pages),
        "page_type": page.get("page_type"),
        "text": text,
    }


if __name__ == "__main__":
    import sys

    # Default: SSE over HTTP so LM Studio (and other apps) can connect via URL.
    # Pass --stdio as the first argument to use stdio transport instead
    # (needed for Cursor / Claude Desktop subprocess mode).
    transport = "stdio" if "--stdio" in sys.argv else "sse"

    if transport == "sse":
        print(f"Starting MCP server (SSE) at http://{_host}:{_port}/sse", flush=True)

    mcp.run(transport=transport)
