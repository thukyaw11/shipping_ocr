from pydantic import BaseModel
from typing import Generic, TypeVar, Optional, Any

T = TypeVar("T")

class ApiResponse(BaseModel, Generic[T]):
    success: bool
    message: str
    data: Optional[T] = None

    @classmethod
    def ok(cls, data: T = None, message: str = "Success"):
        return cls(success=True, message=message, data=data)

    @classmethod
    def fail(cls, message: str = "Failed", data: Any = None):
        return cls(success=False, message=message, data=data)