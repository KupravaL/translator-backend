import os
from sqlalchemy import create_engine
from dotenv import load_dotenv

# Load environment variables first
load_dotenv()

# Import models after environment is loaded
from app.core.database import Base
from app.models.balance import UserBalance
from app.models.payment import Payment
from app.models.translation import TranslationProgress, TranslationChunk

def init_db():
    # Get database URL from environment directly
    db_url = os.getenv("DATABASE_URL_DIRECT", 
                       "postgresql://translator_user:WSgMhbWEZV5LK9gm4yQuAnjGDJN7iqMr@dpg-cuuu4hdds78s73b51ta0-a/translator_lh8q")
    
    # Ensure URL has the correct prefix
    if db_url.startswith('postgres://'):
        db_url = db_url.replace('postgres://', 'postgresql://', 1)
    
    print(f"Connecting to database at: {db_url}")
    
    # Use direct connection (not pooled) for schema updates
    engine = create_engine(db_url, connect_args={"sslmode": "require"})
    
    print("Creating database tables...")
    Base.metadata.create_all(bind=engine)
    print("Database tables created successfully!")

if __name__ == "__main__":
    print("Initializing database...")
    init_db()
    print("Done!")