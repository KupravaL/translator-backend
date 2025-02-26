from sqlalchemy import create_engine
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from app.core.config import settings

# Ensure the URL starts with 'postgresql://' not 'postgres://'
db_url = settings.DATABASE_URL
if db_url.startswith('postgres://'):
    db_url = db_url.replace('postgres://', 'postgresql://', 1)

# Create SQLAlchemy engine with optimized parameters
engine = create_engine(
    db_url,
    connect_args={"sslmode": "require"},
    # Connection pool settings
    pool_size=10,              # Default pool size
    max_overflow=20,           # Allow 20 connections beyond pool_size
    pool_timeout=30,           # Timeout waiting for a connection from pool
    pool_recycle=1800,         # Recycle connections every 30 minutes
    pool_pre_ping=True         # Verify connections before using them
)

# Create session factory
SessionLocal = sessionmaker(
    autocommit=False, 
    autoflush=False, 
    bind=engine
)

# Create base class for models
Base = declarative_base()

# Dependency to get DB session
def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

# Setup async database support for non-blocking operations
async_db_url = db_url.replace('postgresql://', 'postgresql+asyncpg://')
async_engine = create_async_engine(
    async_db_url,
    connect_args={"ssl": "require"},
    pool_size=10,
    max_overflow=20,
    pool_pre_ping=True,
    pool_recycle=1800
)

async_session = sessionmaker(
    bind=async_engine,
    expire_on_commit=False,
    class_=AsyncSession
)

async def get_async_db():
    async with async_session() as session:
        try:
            yield session
        finally:
            await session.close()