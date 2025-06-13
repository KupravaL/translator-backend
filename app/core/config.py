import os
import json
from typing import List, Optional
from pydantic import BaseModel
from dotenv import load_dotenv

# Load environment variables first
load_dotenv()

# Default values
DEFAULT_CORS_ORIGINS = ["http://localhost:5173", "http://localhost:3000"]


class Settings(BaseModel):
    # API Settings
    API_V1_STR: str = "/api"
    API_BASE_URL: str = os.getenv("API_BASE_URL", "http://localhost:8000")
    
    # CORS origins
    CORS_ORIGINS: List[str] = DEFAULT_CORS_ORIGINS
    
    # Authentication - Clerk
    CLERK_SECRET_KEY: str = os.getenv("CLERK_SECRET_KEY", "")
    CLERK_PUBLISHABLE_KEY: str = os.getenv("CLERK_PUBLISHABLE_KEY", "")
    CLERK_ISSUER_URL: str = os.getenv("CLERK_ISSUER_URL", "")
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
    SUPPORTED_DOC_TYPES: List[str] = [
    "application/pdf", 
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "application/msword",  # DOC files
    "application/vnd.oasis.opendocument.text",  # ODT files
    "text/plain",  # TXT files
    "text/rtf",  # RTF files
    "application/rtf"  # Alternative MIME type for RTF
]
    
    # Default user settings
    DEFAULT_BALANCE_PAGES: int = 10

    GOOGLE_CLIENT_ID: str = os.getenv("GOOGLE_CLIENT_ID", "")
    GOOGLE_CLIENT_SECRET: str = os.getenv("GOOGLE_CLIENT_SECRET", "")
    GOOGLE_PROJECT_ID: str = os.getenv("GOOGLE_PROJECT_ID", "")

     # Add timeout settings
    DEFAULT_TIMEOUT: int = 60         # Default timeout for general operations
    STATUS_CHECK_TIMEOUT: int = 8     # Timeout for status check operations
    TRANSLATION_TIMEOUT: int = 600    # Timeout for translation operations (10 minutes)
    
    # PDF Processing Performance Settings
    PDF_PIXMAP_MATRIX: float = 1.5    # Matrix multiplier for PDF pixmap generation (was 2.0)
    PDF_MAX_CONCURRENT_PAGES: int = 3 # Maximum concurrent pages for parallel processing
    PDF_JPEG_QUALITY: int = 85        # JPEG quality for image compression (0-100)
    PDF_CHUNK_SIZE: int = 10000       # Maximum characters per PDF chunk before splitting
    
    # Database connection limits
    DB_POOL_SIZE: int = int(os.getenv("DB_POOL_SIZE", "10"))
    DB_MAX_OVERFLOW: int = int(os.getenv("DB_MAX_OVERFLOW", "20"))
    DB_POOL_TIMEOUT: int = int(os.getenv("DB_POOL_TIMEOUT", "30"))

# Create settings object with defaults
settings = Settings()

# Process CORS_ORIGINS separately from pydantic
cors_value = os.getenv("CORS_ORIGINS")
if cors_value:
    # If string is empty, use defaults
    if not cors_value.strip():
        settings.CORS_ORIGINS = DEFAULT_CORS_ORIGINS
    # Try JSON format
    elif cors_value.startswith("["):
        try:
            settings.CORS_ORIGINS = json.loads(cors_value)
        except json.JSONDecodeError:
            # Fallback to comma-separated if JSON parsing fails
            settings.CORS_ORIGINS = [origin.strip() for origin in cors_value.split(",") if origin.strip()]
    # Use comma-separated format
    else:
        settings.CORS_ORIGINS = [origin.strip() for origin in cors_value.split(",") if origin.strip()]