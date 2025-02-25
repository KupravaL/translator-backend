import os
from sqlalchemy import create_engine, inspect
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

# Import database models
from app.core.database import Base
from app.models.balance import UserBalance
from app.models.payment import Payment
from app.models.translation import TranslationProgress, TranslationChunk

def check_and_init_tables():
    """Check if required tables exist and create them if needed."""
    # Get database URL from environment directly
    db_url = os.getenv("DATABASE_URL_DIRECT", 
                       "postgresql://translator_user:WSgMhbWEZV5LK9gm4yQuAnjGDJN7iqMr@dpg-cuuu4hdds78s73b51ta0-a/translator_lh8q")
    
    # Ensure URL has the correct prefix
    if db_url.startswith('postgres://'):
        db_url = db_url.replace('postgres://', 'postgresql://', 1)
    
    print(f"Connecting to database at: {db_url}")
    
    # Use direct connection (not pooled) for schema updates
    engine = create_engine(db_url, connect_args={"sslmode": "require"})

    # Check if tables exist
    inspector = inspect(engine)
    existing_tables = inspector.get_table_names()
    
    print("\n📌 Existing Tables in Database:")
    for table in existing_tables:
        print(f" - {table}")
    
    required_tables = ["user_balances", "payments", "translation_progress", "translation_chunks"]
    missing_tables = [table for table in required_tables if table not in existing_tables]
    
    if missing_tables:
        print(f"\n⚠️ Missing tables detected: {', '.join(missing_tables)}")
        print("Creating database tables...")
        Base.metadata.create_all(bind=engine)
        print("Database tables created successfully!")
        
        # Verify tables were created
        inspector = inspect(engine)
        new_tables = inspector.get_table_names()
        print("\n📌 Updated Tables in Database:")
        for table in new_tables:
            print(f" - {table}")
            
        return True
    else:
        print("\n✅ All required database tables already exist. No changes made.")
        return False

if __name__ == "__main__":
    print("Checking database tables...")
    tables_created = check_and_init_tables()
    if tables_created:
        print("\n🎉 Database was updated!")
    else:
        print("\n🔍 No database changes were needed.")