import os
from sqlalchemy import create_engine, inspect
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

# Import database models
from app.core.database import Base

def check_tables():
    """Connect to the database and list all tables."""
    db_url = os.getenv("DATABASE_URL_DIRECT", 
                       "postgresql://neondb_owner:npg_aE8CX0qBvTGi@ep-curly-union-a2yahj4n.eu-central-1.aws.neon.tech/neondb?sslmode=require")
    
    # Ensure URL uses correct prefix
    if db_url.startswith('postgres://'):
        db_url = db_url.replace('postgres://', 'postgresql://', 1)
    
    engine = create_engine(db_url, connect_args={"sslmode": "require"})

    # ✅ List all tables
    inspector = inspect(engine)
    tables = inspector.get_table_names()

    print("\n📌 Existing Tables in Database:")
    for table in tables:
        print(f" - {table}")

if __name__ == "__main__":
    print("Checking database tables...")
    check_tables()
    print("Done!")
