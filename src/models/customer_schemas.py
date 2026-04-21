from typing import List, Literal, Optional
from datetime import datetime

from pydantic import BaseModel, Field


Priority = Literal['high', 'medium', 'low']


class CustomerBase(BaseModel):
    name: str = Field(..., min_length=1, max_length=255,
                      description='Customer company name')
    priority: Priority = Field(...,
                               description='Priority level: high, medium, or low')
    location: str = Field('', max_length=255,
                          description='Physical location or office')
    address: str = Field('', max_length=512, description='Customer address')
    emails: List[str] = Field(default_factory=list,
                              description='Customer contact emails')
    profile_url: Optional[str] = Field(None, description='Profile picture URL')


class HSCodeData(BaseModel):
    product: str = Field('', max_length=128)
    definition: str = Field('', max_length=2000)
    code: str = Field('', max_length=128)
    duty: str = Field('', max_length=255)
    license: str = Field('', max_length=255)
    remark: str = Field('', max_length=2000)


class Customer(CustomerBase):
    id: str = Field(..., description='Unique customer identifier')
    hs_code_data: List[HSCodeData] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)


class CustomerCreate(CustomerBase):
    hs_code_data: List[HSCodeData] = Field(default_factory=list)


class CustomerUpdate(BaseModel):
    name: str = Field(None, min_length=1, max_length=255)
    priority: Priority = Field(None)
    location: str = Field(None, max_length=255)
    address: str = Field(None, max_length=512)
    emails: List[str] = Field(None)
    hs_code_data: List[HSCodeData] = Field(None)

    class Config:
        extra = 'forbid'


class PrioritySection(BaseModel):
    key: Priority
    label: str


class CustomersGroupedByPriority(BaseModel):
    high: List[Customer]
    medium: List[Customer]
    low: List[Customer]
