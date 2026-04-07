import logging
from typing import List, Optional
from uuid import uuid4
from datetime import datetime

from fastapi import APIRouter, Body, Depends, HTTPException, Path, Query

from src.core.auth import verify_jwt
from src.core.database import db
from src.core.response_wrapper import ApiResponse
from src.models.customer_schemas import (
    Customer,
    CustomerCreate,
    CustomerUpdate,
    Priority,
    CustomersGroupedByPriority,
    PrioritySection,
)

logger = logging.getLogger('shipping_bill_ocr')

router = APIRouter()

PRIORITY_SECTIONS: List[PrioritySection] = [
    PrioritySection(key='high', label='High priority'),
    PrioritySection(key='medium', label='Medium priority'),
    PrioritySection(key='low', label='Low priority'),
]

DUMMY_CUSTOMERS = [
    Customer(id='1', name='SYMRISE', priority='high', location='Sathorn'),
    Customer(id='2', name='TAKASAGO', priority='high', location='chongnonsi'),
    Customer(id='3', name='GIVAUDAN', priority='medium', location='Bangplee'),
    Customer(id='4', name='IFF', priority='medium', location='Patthumwan'),
    Customer(id='5', name='FLAVOR FORCE', priority='medium', location='Sereethai'),
    Customer(id='6', name='SILESIA', priority='medium', location='Asoke'),
    Customer(id='7', name='SHERWIN', priority='low', location='Bangna'),
    Customer(id='8', name='ALLNEX', priority='low', location='Thepalax'),
    Customer(id='9', name='KH ROBERT', priority='low', location=''),
    Customer(id='10', name='THAI SPECIALTY', priority='low', location=''),
    Customer(id='11', name='PERSPECES', priority='low', location=''),
    Customer(id='12', name='NOURYON', priority='low', location=''),
    Customer(id='13', name='Colossal International', priority='low', location=''),
]


@router.get('/priority-sections', response_model=ApiResponse[List[PrioritySection]])
async def get_priority_sections(
    payload: dict = Depends(verify_jwt),
):
    """Get available priority section definitions."""
    return ApiResponse.ok(data=PRIORITY_SECTIONS)


@router.get('', response_model=ApiResponse[List[Customer]])
async def list_customers(
    page: int = Query(1, ge=1),
    limit: int = Query(50, ge=1, le=100),
    priority: Optional[Priority] = Query(None, description='Filter by priority level'),
    payload: dict = Depends(verify_jwt),
):
    """List all customers with optional priority filter."""
    user_id = payload.get('sub')
    if not user_id:
        raise HTTPException(status_code=401, detail='Invalid token: missing subject')

    skip = (page - 1) * limit
    query = {'user_id': user_id}

    if priority:
        query['priority'] = priority

    cursor = db.db['customers'].find(query).sort('created_at', -1).skip(skip).limit(limit)

    customers = []
    async for doc in cursor:
        doc['id'] = str(doc.pop('_id', ''))
        customers.append(Customer(**doc))

    return ApiResponse.ok(data=customers)


@router.get('/grouped', response_model=ApiResponse[CustomersGroupedByPriority])
async def list_customers_grouped(
    payload: dict = Depends(verify_jwt),
):
    """List all customers grouped by priority level."""
    user_id = payload.get('sub')
    if not user_id:
        raise HTTPException(status_code=401, detail='Invalid token: missing subject')

    cursor = db.db['customers'].find({'user_id': user_id}).sort('created_at', -1)

    customers_by_priority = {
        'high': [],
        'medium': [],
        'low': [],
    }

    async for doc in cursor:
        doc['id'] = str(doc.pop('_id', ''))
        customer = Customer(**doc)
        priority = customer.priority
        if priority in customers_by_priority:
            customers_by_priority[priority].append(customer)

    return ApiResponse.ok(
        data=CustomersGroupedByPriority(**customers_by_priority),
        message='Customers grouped by priority',
    )


@router.post('', response_model=ApiResponse[Customer])
async def create_customer(
    body: CustomerCreate,
    payload: dict = Depends(verify_jwt),
):
    """Create a new customer."""
    user_id = payload.get('sub')
    if not user_id:
        raise HTTPException(status_code=401, detail='Invalid token: missing subject')

    now = datetime.utcnow()
    customer_doc = {
        '_id': str(uuid4()),
        'user_id': user_id,
        'name': body.name,
        'priority': body.priority,
        'location': body.location,
        'created_at': now,
        'updated_at': now,
    }

    await db.db['customers'].insert_one(customer_doc)

    customer_doc['id'] = customer_doc.pop('_id')
    customer_doc.pop('user_id', None)

    return ApiResponse.ok(
        data=Customer(**customer_doc),
        message='Customer created successfully',
    )


@router.get('/{customer_id}', response_model=ApiResponse[Customer])
async def get_customer(
    customer_id: str = Path(..., description='Customer ID'),
    payload: dict = Depends(verify_jwt),
):
    """Get a specific customer by ID."""
    user_id = payload.get('sub')
    if not user_id:
        raise HTTPException(status_code=401, detail='Invalid token: missing subject')

    doc = await db.db['customers'].find_one({
        '_id': customer_id,
        'user_id': user_id,
    })

    if not doc:
        raise HTTPException(status_code=404, detail='Customer not found')

    doc['id'] = str(doc.pop('_id'))
    doc.pop('user_id', None)

    return ApiResponse.ok(data=Customer(**doc))


@router.put('/{customer_id}', response_model=ApiResponse[Customer])
async def update_customer(
    customer_id: str = Path(..., description='Customer ID'),
    body: CustomerUpdate = Body(...),
    payload: dict = Depends(verify_jwt),
):
    """Update a customer's information."""
    user_id = payload.get('sub')
    if not user_id:
        raise HTTPException(status_code=401, detail='Invalid token: missing subject')

    doc = await db.db['customers'].find_one({
        '_id': customer_id,
        'user_id': user_id,
    })

    if not doc:
        raise HTTPException(status_code=404, detail='Customer not found')

    update_data = {}
    if body.name is not None:
        update_data['name'] = body.name
    if body.priority is not None:
        update_data['priority'] = body.priority
    if body.location is not None:
        update_data['location'] = body.location

    if update_data:
        update_data['updated_at'] = datetime.utcnow()

        await db.db['customers'].update_one(
            {'_id': customer_id, 'user_id': user_id},
            {'$set': update_data},
        )

    updated_doc = await db.db['customers'].find_one({
        '_id': customer_id,
        'user_id': user_id,
    })

    updated_doc['id'] = str(updated_doc.pop('_id'))
    updated_doc.pop('user_id', None)

    return ApiResponse.ok(
        data=Customer(**updated_doc),
        message='Customer updated successfully',
    )


@router.delete('/{customer_id}', response_model=ApiResponse[dict])
async def delete_customer(
    customer_id: str = Path(..., description='Customer ID'),
    payload: dict = Depends(verify_jwt),
):
    """Delete a customer."""
    user_id = payload.get('sub')
    if not user_id:
        raise HTTPException(status_code=401, detail='Invalid token: missing subject')

    result = await db.db['customers'].delete_one({
        '_id': customer_id,
        'user_id': user_id,
    })

    if result.deleted_count == 0:
        raise HTTPException(status_code=404, detail='Customer not found')

    return ApiResponse.ok(
        data={'id': customer_id},
        message='Customer deleted successfully',
    )


@router.post('/seed-demo', response_model=ApiResponse[List[Customer]])
async def seed_demo_customers(
    payload: dict = Depends(verify_jwt),
):
    """Seed the database with demo customers (replaces existing ones)."""
    user_id = payload.get('sub')
    if not user_id:
        raise HTTPException(status_code=401, detail='Invalid token: missing subject')

    await db.db['customers'].delete_many({'user_id': user_id})

    now = datetime.utcnow()
    demo_docs = [
        {
            '_id': c.id,
            'user_id': user_id,
            'name': c.name,
            'priority': c.priority,
            'location': c.location,
            'created_at': now,
            'updated_at': now,
        }
        for c in DUMMY_CUSTOMERS
    ]

    if demo_docs:
        await db.db['customers'].insert_many(demo_docs)

    customers = [
        Customer(
            id=doc['_id'],
            name=doc['name'],
            priority=doc['priority'],
            location=doc['location'],
            created_at=doc['created_at'],
            updated_at=doc['updated_at'],
        )
        for doc in demo_docs
    ]

    return ApiResponse.ok(
        data=customers,
        message=f'{len(customers)} demo customers seeded',
    )
