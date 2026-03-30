from datetime import datetime
from typing import Any, Dict, Optional

import bcrypt
from pymongo.errors import DuplicateKeyError

from src.core.database import db


def hash_password(plain: str) -> str:
    return bcrypt.hashpw(plain.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")


def verify_password(plain: str, password_hash: str) -> bool:
    try:
        return bcrypt.checkpw(
            plain.encode("utf-8"),
            password_hash.encode("utf-8"),
        )
    except (ValueError, TypeError):
        return False


async def create_user(email: str, password: str) -> Dict[str, Any]:
    doc = {
        "email": email,
        "password_hash": hash_password(password),
        "created_at": datetime.utcnow(),
    }
    try:
        result = await db.db["users"].insert_one(doc)
    except DuplicateKeyError:
        raise
    doc["_id"] = result.inserted_id
    return doc


async def get_user_by_email(email: str) -> Optional[Dict[str, Any]]:
    return await db.db["users"].find_one({"email": email})


async def get_user_by_google_sub(google_sub: str) -> Optional[Dict[str, Any]]:
    return await db.db["users"].find_one({"google_sub": google_sub})


async def find_or_create_google_user(email: str, google_sub: str) -> Dict[str, Any]:
    existing = await get_user_by_google_sub(google_sub)
    if existing:
        return existing

    by_email = await get_user_by_email(email)
    if by_email:
        if by_email.get("google_sub") and by_email["google_sub"] != google_sub:
            raise ValueError("google_sub_conflict")
        await db.db["users"].update_one(
            {"_id": by_email["_id"]},
            {"$set": {"google_sub": google_sub}},
        )
        updated = await db.db["users"].find_one({"_id": by_email["_id"]})
        if updated:
            return updated
        return by_email

    doc = {
        "email": email,
        "google_sub": google_sub,
        "created_at": datetime.utcnow(),
    }
    result = await db.db["users"].insert_one(doc)
    doc["_id"] = result.inserted_id
    return doc


def user_public(doc: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "id": str(doc["_id"]),
        "email": doc["email"],
        "created_at": doc["created_at"],
    }
