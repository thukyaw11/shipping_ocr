from src.core.database import db


class ScanLogRepository:
    _col = "scan_logs"

    async def create(self, doc: dict) -> None:
        await db.db[self._col].insert_one(doc)


scan_log_repo = ScanLogRepository()
