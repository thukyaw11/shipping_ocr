import io
import logging
from typing import List, Optional
from uuid import uuid4
from datetime import datetime

import openpyxl
from fastapi import APIRouter, Body, Depends, File, HTTPException, Path, Query, UploadFile

from src.core.auth import verify_jwt
from src.core.database import db
from src.core.response_wrapper import ApiResponse
from src.models.customer_schemas import (
    Customer,
    CustomerCreate,
    CustomerUpdate,
    HSCodeData,
    Priority,
    CustomersGroupedByPriority,
    PrioritySection,
)
from src.services.s3_service import s3_service

logger = logging.getLogger('shipping_bill_ocr')

router = APIRouter()

ALLOWED_IMAGE_TYPES = {'image/jpeg', 'image/png', 'image/webp'}
MAX_PROFILE_PIC_BYTES = 5 * 1024 * 1024

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
    Customer(id='5', name='FLAVOR FORCE',
             priority='medium', location='Sereethai'),
    Customer(id='6', name='SILESIA', priority='medium', location='Asoke'),
    Customer(id='7', name='SHERWIN', priority='low', location='Bangna'),
    Customer(id='8', name='ALLNEX', priority='low', location='Thepalax'),
    Customer(id='9', name='KH ROBERT', priority='low', location=''),
    Customer(id='10', name='THAI SPECIALTY', priority='low', location=''),
    Customer(id='11', name='PERSPECES', priority='low', location=''),
    Customer(id='12', name='NOURYON', priority='low', location=''),
    Customer(id='13', name='Colossal International',
             priority='low', location=''),
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
    priority: Optional[Priority] = Query(
        None, description='Filter by priority level'),
    payload: dict = Depends(verify_jwt),
):
    """List all customers with optional priority filter."""
    user_id = payload.get('sub')
    if not user_id:
        raise HTTPException(
            status_code=401, detail='Invalid token: missing subject')

    skip = (page - 1) * limit
    query = {'user_id': user_id}

    if priority:
        query['priority'] = priority

    cursor = db.db['customers'].find(query).sort(
        'created_at', -1).skip(skip).limit(limit)

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
        raise HTTPException(
            status_code=401, detail='Invalid token: missing subject')

    cursor = db.db['customers'].find(
        {'user_id': user_id}).sort('created_at', -1)

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
        raise HTTPException(
            status_code=401, detail='Invalid token: missing subject')

    now = datetime.utcnow()
    customer_doc = {
        '_id': str(uuid4()),
        'user_id': user_id,
        'name': body.name,
        'priority': body.priority,
        'location': body.location,
        'address': body.address,
        'emails': body.emails,
        'hs_code_data': [entry.model_dump() for entry in body.hs_code_data],
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


@router.get('/{customer_id}/hs-codes', response_model=ApiResponse[List[HSCodeData]])
async def get_hs_codes(
    customer_id: str = Path(..., description='Customer ID'),
    page: int = Query(1, ge=1),
    pageSize: int = Query(20, ge=1, le=100),
    search: Optional[str] = Query(
        None, description='Search by code or product name'),
    payload: dict = Depends(verify_jwt),
):
    """Get paginated HS code data for a specific customer."""
    user_id = payload.get('sub')
    if not user_id:
        raise HTTPException(
            status_code=401, detail='Invalid token: missing subject')

    doc = await db.db['customers'].find_one(
        {'_id': customer_id, 'user_id': user_id},
        {'hs_code_data': 1},
    )
    if not doc:
        raise HTTPException(status_code=404, detail='Customer not found')

    all_entries = doc.get('hs_code_data', [])

    if search:
        term = search.strip().lower()
        all_entries = [
            e for e in all_entries
            if term in str(e.get('code', '')).lower()
            or term in str(e.get('product', '')).lower()
        ]

    skip = (page - 1) * pageSize
    paged = all_entries[skip: skip + pageSize]

    return ApiResponse.ok(
        data=[HSCodeData(**entry) for entry in paged],
        message=f'Page {page}',
    )


@router.post('/{customer_id}/hs-codes/upload', response_model=ApiResponse[Customer])
async def upload_hs_codes(
    customer_id: str = Path(..., description='Customer ID'),
    file: UploadFile = File(...),
    payload: dict = Depends(verify_jwt),
):
    """Replace customer hs_code_data from an uploaded .xlsx file.

    Row 1 must be a header row with these column names (case-insensitive):
    product | definition | code | duty | license | remark
    """
    user_id = payload.get('sub')
    if not user_id:
        raise HTTPException(
            status_code=401, detail='Invalid token: missing subject')

    if not file.filename or not file.filename.lower().endswith('.xlsx'):
        raise HTTPException(
            status_code=400, detail='Only .xlsx files are supported')

    doc = await db.db['customers'].find_one({'_id': customer_id, 'user_id': user_id})
    if not doc:
        raise HTTPException(status_code=404, detail='Customer not found')

    contents = await file.read()
    wb = openpyxl.load_workbook(io.BytesIO(
        contents), read_only=True, data_only=True)
    ws = wb.active

    REQUIRED_COLUMNS = {'product', 'thai_definition',
                        'h_s_code', 'duty', 'license', 'remark'}
    OPTIONAL_COLUMNS = {'flight'}
    COLUMN_MAP = REQUIRED_COLUMNS | OPTIONAL_COLUMNS

    hs_entries = []
    col_index: dict[str, int] = {}

    HEADER_ROW_INDEX = 2  # 0-based; row 3 in Excel

    for i, row in enumerate(ws.iter_rows(values_only=True)):
        if i < HEADER_ROW_INDEX:
            continue

        if i == HEADER_ROW_INDEX:
            def _normalize(val) -> str:
                return str(val).strip().lower().replace('.', '_').replace(' ', '_')

            normalized_headers = [_normalize(cell) for cell in row if cell]
            print('[hs_upload] raw headers from Excel:', list(row))
            print('[hs_upload] normalized headers     :', normalized_headers)
            print('[hs_upload] expected COLUMN_MAP    :', COLUMN_MAP)

            col_index = {
                _normalize(cell): j
                for j, cell in enumerate(row)
                if cell and _normalize(cell) in COLUMN_MAP
            }
            missing = REQUIRED_COLUMNS - set(col_index.keys())
            if missing:
                wb.close()
                raise HTTPException(
                    status_code=400,
                    detail=f'Missing required columns: {", ".join(sorted(missing))}',
                )
            continue

        if not any(cell for cell in row):
            continue

        def _get(field: str) -> str:
            idx = col_index.get(field)
            if idx is None or idx >= len(row):
                return ''
            return str(row[idx] or '').strip()

        hs_entries.append({
            'product': _get('product'),
            'definition': _get('thai_definition'),
            'code': _get('h_s_code'),
            'duty': _get('duty'),
            'license': _get('license'),
            'flight': _get('flight'),
            'remark': _get('remark'),
        })
    wb.close()

    await db.db['customers'].update_one(
        {'_id': customer_id, 'user_id': user_id},
        {'$set': {'hs_code_data': hs_entries, 'updated_at': datetime.utcnow()}},
    )

    updated_doc = await db.db['customers'].find_one({'_id': customer_id, 'user_id': user_id})
    updated_doc['id'] = str(updated_doc.pop('_id'))
    updated_doc.pop('user_id', None)

    return ApiResponse.ok(
        data=Customer(**updated_doc),
        message=f'{len(hs_entries)} HS codes imported successfully',
    )


@router.post('/{customer_id}/hs-codes', response_model=ApiResponse[Customer])
async def add_hs_code(
    customer_id: str = Path(..., description='Customer Id'),
    body: HSCodeData = Body(...),
    payload: dict = Depends(verify_jwt)
):

    user_id = payload.get('sub')
    if not user_id:
        raise HTTPException(
            status_code=401, detail='Invalid token: missing subject')

    doc = await db.db['customers'].find_one({'_id': customer_id, 'user_id': user_id})
    if not doc:
        raise HTTPException(status_code=404, detail='Customer not found')

    await db.db['customers'].update_one(
        {'_id': customer_id, 'user_id': user_id},
        {
            '$push': {'hs_code_data': body.model_dump()},
            '$set': {'updated_at': datetime.utcnow()}
        }
    )

    updated_doc = await db.db['customers'].find_one({
        '_id': customer_id, 'user_id': user_id
    })
    updated_doc['id'] = str(updated_doc.pop('_id'))
    updated_doc.pop('user_id', None)

    return ApiResponse.ok(
        data=Customer(**updated_doc),
        message='HS code added successfully'
    )


@router.get('/{customer_id}', response_model=ApiResponse[Customer])
async def get_customer(
    customer_id: str = Path(..., description='Customer ID'),
    payload: dict = Depends(verify_jwt),
):
    """Get a specific customer by ID."""
    user_id = payload.get('sub')
    if not user_id:
        raise HTTPException(
            status_code=401, detail='Invalid token: missing subject')

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
        raise HTTPException(
            status_code=401, detail='Invalid token: missing subject')

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
    if body.address is not None:
        update_data['address'] = body.address
    if body.emails is not None:
        update_data['emails'] = body.emails
    if body.hs_code_data is not None:
        update_data['hs_code_data'] = [entry.model_dump()
                                       for entry in body.hs_code_data]

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


@router.post('/{customer_id}/profile-pic', response_model=ApiResponse[Customer])
async def upload_profile_pic(
    customer_id: str = Path(..., description='Customer ID'),
    file: UploadFile = File(...),
    payload: dict = Depends(verify_jwt),
):
    """Upload or replace a customer's profile picture (JPEG/PNG/WebP, max 5 MB)."""
    user_id = payload.get('sub')
    if not user_id:
        raise HTTPException(
            status_code=401, detail='Invalid token: missing subject')

    if file.content_type not in ALLOWED_IMAGE_TYPES:
        raise HTTPException(
            status_code=400, detail='Only JPEG, PNG, and WebP images are allowed')

    contents = await file.read()
    if len(contents) > MAX_PROFILE_PIC_BYTES:
        raise HTTPException(
            status_code=400, detail='File size exceeds 5 MB limit')

    doc = await db.db['customers'].find_one({'_id': customer_id, 'user_id': user_id})
    if not doc:
        raise HTTPException(status_code=404, detail='Customer not found')

    object_name = f"customer-profiles/{customer_id}"
    url = s3_service.upload_file(io.BytesIO(
        contents), object_name, file.content_type)

    await db.db['customers'].update_one(
        {'_id': customer_id, 'user_id': user_id},
        {'$set': {'profile_url': url, 'updated_at': datetime.utcnow()}},
    )

    updated_doc = await db.db['customers'].find_one({'_id': customer_id, 'user_id': user_id})
    updated_doc['id'] = str(updated_doc.pop('_id'))
    updated_doc.pop('user_id', None)

    return ApiResponse.ok(
        data=Customer(**updated_doc),
        message='Profile picture updated successfully',
    )


@router.delete('/{customer_id}', response_model=ApiResponse[dict])
async def delete_customer(
    customer_id: str = Path(..., description='Customer ID'),
    payload: dict = Depends(verify_jwt),
):
    """Delete a customer."""
    user_id = payload.get('sub')
    if not user_id:
        raise HTTPException(
            status_code=401, detail='Invalid token: missing subject')

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
        raise HTTPException(
            status_code=401, detail='Invalid token: missing subject')

    await db.db['customers'].delete_many({'user_id': user_id})

    now = datetime.utcnow()
    demo_docs = [
        {
            '_id': c.id,
            'user_id': user_id,
            'name': c.name,
            'priority': c.priority,
            'location': c.location,
            'address': c.address,
            'emails': c.emails,
            'hs_code_data': [entry.model_dump() for entry in c.hs_code_data],
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
            address=doc.get('address', ''),
            emails=doc.get('emails', []),
            hs_code_data=doc.get('hs_code_data', []),
            created_at=doc['created_at'],
            updated_at=doc['updated_at'],
        )
        for doc in demo_docs
    ]

    return ApiResponse.ok(
        data=customers,
        message=f'{len(customers)} demo customers seeded',
    )
