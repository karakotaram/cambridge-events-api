"""Database initialization script"""
import os
import sys
from pathlib import Path

# Add project root to path
project_root = Path(__file__).parent.parent.parent
sys.path.insert(0, str(project_root))

from src.db.database import engine, Base
from src.models.user import User, EmailLog, ClickTracking, EventPopularity


def init_database():
    """Create all database tables"""
    if engine is None:
        print("ERROR: DATABASE_URL not configured")
        print("Set the DATABASE_URL environment variable to your PostgreSQL connection string")
        print("Example: postgresql://user:password@host:port/database")
        return False

    print("Creating database tables...")

    try:
        Base.metadata.create_all(bind=engine)
        print("Database tables created successfully!")
        print("\nTables created:")
        for table in Base.metadata.tables:
            print(f"  - {table}")
        return True
    except Exception as e:
        print(f"ERROR creating tables: {e}")
        return False


def drop_all_tables():
    """Drop all database tables (use with caution!)"""
    if engine is None:
        print("ERROR: DATABASE_URL not configured")
        return False

    print("WARNING: This will delete all data!")
    confirm = input("Type 'yes' to confirm: ")

    if confirm.lower() != 'yes':
        print("Aborted.")
        return False

    try:
        Base.metadata.drop_all(bind=engine)
        print("All tables dropped.")
        return True
    except Exception as e:
        print(f"ERROR dropping tables: {e}")
        return False


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Initialize the database")
    parser.add_argument("--drop", action="store_true", help="Drop all tables first")

    args = parser.parse_args()

    if args.drop:
        drop_all_tables()

    init_database()
