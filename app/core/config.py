import os
import json
from typing import List, ClassVar
from pydantic_settings import BaseSettings
from pydantic import field_validator, Field
from dotenv import load_dotenv

load_dotenv()

def parse_cors_origins(v: str) -> List[str]:
    """Parse CORS origins from string to list."""
    if not v:
        return ["http://localhost:5173", "http://localhost:3000"]
    
    if v.startswith("["):
        try:
            return json.loads(v)
        except json.JSONDecodeError:
            # Fallback to comma-separated if JSON parsing fails
            pass
    
    # Handle as comma-separated string
    return [origin.strip() for origin in v.split(",") if origin.strip()]

class Settings(BaseSettings):
    # API Settings
    API_V1_STR: str = "/api"
    API_BASE_URL: str = Field(default=os.getenv("API_BASE_URL", "http://localhost:8000"))
    
    # CORS origins - this uses Field with default_factory to handle parsing properly
    CORS_ORIGINS: List[str] = Field(
        default_factory=lambda: parse_cors_origins(os.getenv("CORS_ORIGINS", "http://localhost:5173,http://localhost:3000"))
    )
    
    # Authentication - Clerk
    CLERK_SECRET_KEY: str = Field(default=os.getenv("CLERK_SECRET_KEY", ""))
    CLERK_PUBLISHABLE_KEY: str = Field(default=os.getenv("CLERK_PUBLISHABLE_KEY", ""))
    CLERK_ISSUER_URL: str = Field(default=os.getenv("CLERK_ISSUER_URL", "https://api.clerk.dev"))
    CLERK_AUDIENCE: str = Field(default=os.getenv("CLERK_AUDIENCE", ""))
    CLERK_WEBHOOK_SECRET: str = Field(default=os.getenv("CLERK_WEBHOOK_SECRET", ""))
    
    # Database
    DATABASE_URL: str = Field(
        default=os.getenv("DATABASE_URL", "postgresql://neondb_owner:npg_aE8CX0qBvTGi@ep-curly-union-a2yahj4n-pooler.eu-central-1.aws.neon.tech/neondb?sslmode=require")
    )
    DATABASE_URL_DIRECT: str = Field(
        default=os.getenv("DATABASE_URL_DIRECT", "postgresql://neondb_owner:npg_aE8CX0qBvTGi@ep-curly-union-a2yahj4n.eu-central-1.aws.neon.tech/neondb?sslmode=require")
    )
    
    # External APIs
    GOOGLE_API_KEY: str = Field(default=os.getenv("GOOGLE_API_KEY", ""))
    ANTHROPIC_API_KEY: str = Field(default=os.getenv("ANTHROPIC_API_KEY", ""))
    RESEND_API_KEY: str = Field(default=os.getenv("RESEND_API_KEY", ""))
    
    # Translation Settings
    MAX_FILE_SIZE: int = 20 * 1024 * 1024  # 20MB
    MAX_CHUNK_SIZE: int = 2500  # Maximum characters per chunk
    SUPPORTED_IMAGE_TYPES: List[str] = ["image/jpeg", "image/png", "image/webp", "image/heic", "image/heif"]
    SUPPORTED_DOC_TYPES: List[str] = ["application/pdf"]
    
    # Default user settings
    DEFAULT_BALANCE_PAGES: int = 10

    GOOGLE_CLIENT_ID: str = Field(default=os.getenv("GOOGLE_CLIENT_ID", ""))
    GOOGLE_CLIENT_SECRET: str = Field(default=os.getenv("GOOGLE_CLIENT_SECRET", ""))
    GOOGLE_PROJECT_ID: str = Field(default=os.getenv("GOOGLE_PROJECT_ID", ""))
    
    # No custom __init__ method - using Field and Field validators instead
    # This avoids issues with pydantic initialization

settings = Settings()