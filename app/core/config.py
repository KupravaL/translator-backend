import os
from typing import List
from pydantic_settings import BaseSettings
from dotenv import load_dotenv

load_dotenv()

class Settings(BaseSettings):
    # API Settings
    API_V1_STR: str = "/api"
    API_BASE_URL: str = os.getenv("API_BASE_URL", "http://localhost:8000")
    CORS_ORIGINS: List[str] = os.getenv("CORS_ORIGINS", "http://localhost:5173,http://localhost:3000").split(',')
    
    # Authentication - Clerk
    CLERK_SECRET_KEY: str = os.getenv("CLERK_SECRET_KEY", "")
    CLERK_PUBLISHABLE_KEY: str = os.getenv("CLERK_PUBLISHABLE_KEY", "")
    CLERK_ISSUER_URL: str = os.getenv("CLERK_ISSUER_URL", "https://api.clerk.dev")
    CLERK_AUDIENCE: str = os.getenv("CLERK_AUDIENCE", "")
    CLERK_WEBHOOK_SECRET: str = os.getenv("CLERK_WEBHOOK_SECRET", "")
    
    # Database
    DATABASE_URL: str = os.getenv("DATABASE_URL", "postgresql://neondb_owner:npg_aE8CX0qBvTGi@ep-curly-union-a2yahj4n-pooler.eu-central-1.aws.neon.tech/neondb?sslmode=require")
    DATABASE_URL_DIRECT: str = os.getenv("DATABASE_URL_DIRECT", "postgresql://neondb_owner:npg_aE8CX0qBvTGi@ep-curly-union-a2yahj4n.eu-central-1.aws.neon.tech/neondb?sslmode=require")
    
    # External APIs
    GOOGLE_API_KEY: str = os.getenv("GOOGLE_API_KEY", "")
    ANTHROPIC_API_KEY: str = os.getenv("ANTHROPIC_API_KEY", "")
    RESEND_API_KEY: str = os.getenv("RESEND_API_KEY", "")
    
    # Translation Settings
    MAX_FILE_SIZE: int = 20 * 1024 * 1024  # 20MB
    MAX_CHUNK_SIZE: int = 2500  # Maximum characters per chunk
    SUPPORTED_IMAGE_TYPES: List[str] = ["image/jpeg", "image/png", "image/webp", "image/heic", "image/heif"]
    SUPPORTED_DOC_TYPES: List[str] = ["application/pdf"]
    
    # Default user settings
    DEFAULT_BALANCE_PAGES: int = 10

    GOOGLE_CLIENT_ID: str = os.getenv("GOOGLE_CLIENT_ID", "")
    GOOGLE_CLIENT_SECRET: str = os.getenv("GOOGLE_CLIENT_SECRET", "")
    GOOGLE_PROJECT_ID: str = os.getenv("GOOGLE_PROJECT_ID", "")

settings = Settings()