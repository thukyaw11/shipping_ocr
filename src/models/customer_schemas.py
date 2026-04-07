from typing import List, Literal
from datetime import datetime

from pydantic import BaseModel, Field


Priority = Literal['high', 'medium', 'low']


class CustomerBase(BaseModel):
    name: str = Field(..., min_length=1, max_length=255, description='Customer company name')
    priority: Priority = Field(..., description='Priority level: high, medium, or low')
    location: str = Field('', max_length=255, description='Physical location or office')


class Customer(CustomerBase):
    id: str = Field(..., description='Unique customer identifier')
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)


class CustomerCreate(CustomerBase):
    pass


class CustomerUpdate(BaseModel):
    name: str = Field(None, min_length=1, max_length=255)
    priority: Priority = Field(None)
    location: str = Field(None, max_length=255)

    class Config:
        extra = 'forbid'


class PrioritySection(BaseModel):
    key: Priority
    label: str


class CustomersGroupedByPriority(BaseModel):
    high: List[Customer]
    medium: List[Customer]
    low: List[Customer]
