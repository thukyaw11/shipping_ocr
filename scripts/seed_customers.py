"""
Seed script for the customers collection.

Usage:
    # Seed customers for a specific user
    python scripts/seed_customers.py --user-id <user_id>

    # Wipe the user's existing customers first, then seed
    python scripts/seed_customers.py --user-id <user_id> --reset

    # Preview what would be inserted (no DB writes)
    python scripts/seed_customers.py --user-id <user_id> --dry-run
"""

import argparse
import sys
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

# Allow src.* imports from project root
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from pymongo import MongoClient
from pymongo.errors import BulkWriteError

from src.core.config import Config


CUSTOMERS_SEED_DATA = [
    {'name': 'SYMRISE',                'priority': 'high',   'location': 'Sathorn'},
    {'name': 'TAKASAGO',               'priority': 'high',   'location': 'Chong Nonsi'},
    {'name': 'GIVAUDAN',               'priority': 'medium', 'location': 'Bangplee'},
    {'name': 'IFF',                    'priority': 'medium', 'location': 'Pathum Wan'},
    {'name': 'FLAVOR FORCE',           'priority': 'medium', 'location': 'Sereethai'},
    {'name': 'SILESIA',                'priority': 'medium', 'location': 'Asoke'},
    {'name': 'SHERWIN',                'priority': 'low',    'location': 'Bang Na'},
    {'name': 'ALLNEX',                 'priority': 'low',    'location': 'Thepharak'},
    {'name': 'KH ROBERT',              'priority': 'low',    'location': ''},
    {'name': 'THAI SPECIALTY',         'priority': 'low',    'location': ''},
    {'name': 'PERSPECES',              'priority': 'low',    'location': ''},
    {'name': 'NOURYON',                'priority': 'low',    'location': ''},
    {'name': 'COLOSSAL INTERNATIONAL', 'priority': 'low',    'location': ''},
]


def build_docs(user_id: str) -> list[dict]:
    now = datetime.now(timezone.utc)
    return [
        {
            '_id': str(uuid4()),
            'user_id': user_id,
            'name': entry['name'],
            'priority': entry['priority'],
            'location': entry['location'],
            'created_at': now,
            'updated_at': now,
        }
        for entry in CUSTOMERS_SEED_DATA
    ]


def print_table(docs: list[dict]) -> None:
    print(f"\n{'#':<4} {'NAME':<26} {'PRIORITY':<10} LOCATION")
    print('-' * 60)
    for i, doc in enumerate(docs, 1):
        print(f"{i:<4} {doc['name']:<26} {doc['priority']:<10} {doc['location'] or '-'}")
    print()


def seed(user_id: str, reset: bool, dry_run: bool) -> None:
    docs = build_docs(user_id)

    print(f"Customers to seed  ({len(docs)} records, user_id={user_id!r}):")
    print_table(docs)

    if dry_run:
        print('[dry-run] No changes written to the database.')
        return

    client = MongoClient(Config.MONGODB_URL)
    try:
        collection = client[Config.DATABASE_NAME]['customers']

        if reset:
            deleted = collection.delete_many({'user_id': user_id}).deleted_count
            print(f'[reset]  Removed {deleted} existing customer(s).')

        try:
            result = collection.insert_many(docs, ordered=False)
            print(f'[done]   Inserted {len(result.inserted_ids)} customer(s).')
        except BulkWriteError as exc:
            inserted = exc.details.get('nInserted', 0)
            print(f'[warn]   Partial insert: {inserted} inserted, some IDs already existed.')
    finally:
        client.close()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description='Seed customers into MongoDB.',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument(
        '--user-id',
        required=True,
        help='User ID to associate the seeded customers with.',
    )
    parser.add_argument(
        '--reset',
        action='store_true',
        help="Delete this user's existing customers before inserting.",
    )
    parser.add_argument(
        '--dry-run',
        action='store_true',
        help='Print the records that would be inserted without touching the database.',
    )
    return parser.parse_args()


if __name__ == '__main__':
    args = parse_args()
    seed(args.user_id, args.reset, args.dry_run)
