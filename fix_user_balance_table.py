import os
from sqlalchemy import create_engine, MetaData, Table, text
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

# Get database URL
db_url = os.getenv("DATABASE_URL_DIRECT", 
                   "postgresql://neondb_owner:npg_aE8CX0qBvTGi@ep-curly-union-a2yahj4n.eu-central-1.aws.neon.tech/neondb?sslmode=require")

# Ensure correct database prefix
if db_url.startswith('postgres://'):
    db_url = db_url.replace('postgres://', 'postgresql://', 1)

# Create SQLAlchemy engine
engine = create_engine(db_url, connect_args={"sslmode": "require"})
metadata = MetaData()

def check_existing_tables():
    """Check if user_balances table exists."""
    with engine.connect() as conn:
        result = conn.execute(text("SELECT EXISTS (SELECT FROM information_schema.tables WHERE table_name = 'user_balances');"))
        return result.scalar()  # True if table exists, False otherwise

def migrate_user_balance_table():
    """Fix user_balances table by renaming old table and creating a new one."""
    with engine.connect() as conn:
        conn.execute(text("COMMIT;"))  # Ensure changes are committed before altering tables

        # Step 1: Rename old table if it exists
        if check_existing_tables():
            print("🔄 Renaming existing user_balances table to old_user_balances...")
            conn.execute(text("ALTER TABLE user_balances RENAME TO old_user_balances;"))

        # Step 2: Create the correct table schema
        print("✅ Creating new user_balances table...")
        conn.execute(text("""
            CREATE TABLE user_balances (
                user_id VARCHAR PRIMARY KEY,
                pages_balance INTEGER DEFAULT 10 NOT NULL,
                pages_used INTEGER DEFAULT 0 NOT NULL,
                last_used TIMESTAMP DEFAULT now(),
                created_at TIMESTAMP DEFAULT now()
            );
        """))

        # Step 3: Migrate existing data (if any)
        result = conn.execute(text("SELECT COUNT(*) FROM old_user_balances;"))
        count = result.scalar()
        if count > 0:
            print(f"🔄 Migrating {count} rows from old_user_balances to user_balances...")
            conn.execute(text("""
                INSERT INTO user_balances (user_id, pages_balance, pages_used, last_used, created_at)
                SELECT user_id, pages_balance, pages_used, last_used, created_at FROM old_user_balances;
            """))

        # Step 4: Drop old table after migration
        print("🗑 Dropping old_user_balances table...")
        conn.execute(text("DROP TABLE old_user_balances;"))

    print("🎉 Database migration complete! New user_balances table is ready.")

if __name__ == "__main__":
    print("🔧 Starting database migration for user_balances table...")
    migrate_user_balance_table()
    print("✅ Migration completed successfully!")
