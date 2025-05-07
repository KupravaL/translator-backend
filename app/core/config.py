import os
import json
from typing import List, Optional, Dict, Any
from pydantic import BaseModel
from dotenv import load_dotenv
from pydantic_settings import BaseSettings
from functools import lru_cache

# Load environment variables first
load_dotenv()

# Default values
DEFAULT_CORS_ORIGINS = ["http://localhost:5173", "http://localhost:3000"]


class Settings(BaseSettings):
    """Application settings."""
    
    # API Configuration
    API_V1_STR: str = "/api/v1"
    PROJECT_NAME: str = "Document Translation API"
    API_VERSION: str = "1.0.0"
    
    # CORS Configuration
    CORS_ORIGINS: List[str] = DEFAULT_CORS_ORIGINS
    
    # Security
    SECRET_KEY: str = "your-secret-key-here"  # Change in production
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 60 * 24 * 8  # 8 days
    
    # Database
    DATABASE_URL: str = "postgresql://user:password@localhost/translator"
    
    # Google Gemini API
    GOOGLE_API_KEY: Optional[str] = None
    
    # Supported File Types
    SUPPORTED_DOC_TYPES: List[str] = [
        "application/pdf",
        "application/msword",
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        "text/plain",
        "text/rtf",
        "application/rtf"
    ]
    
    SUPPORTED_IMAGE_TYPES: List[str] = [
        "image/jpeg",
        "image/png",
        "image/gif",
        "image/bmp",
        "image/tiff"
    ]
    
    # Translation Settings
    MAX_CHUNK_SIZE: int = 9000  # Maximum size of a translation chunk
    BATCH_SIZE: int = 8  # Number of pages to process in parallel
    API_TIMEOUT: int = 30  # Timeout for API calls in seconds
    
    # Language Configuration
    LANGUAGE_CONFIG: Dict[str, Dict[str, Any]] = {
        "default": {
            "temperature": 0,
            "top_p": 0.97,
            "top_k": 45,
            "max_chunk_size": 9000,
            "max_output_tokens": 8192
        }
    }
    
    # Logging
    LOG_LEVEL: str = "INFO"
    LOG_FORMAT: str = "%(asctime)s [%(levelname)s] [%(name)s] %(message)s"
    
    class Config:
        case_sensitive = True
        env_file = ".env"

@lru_cache()
def get_settings() -> Settings:
    """Get cached settings instance."""
    return Settings()

settings = get_settings()

# Process CORS_ORIGINS separately from pydantic
cors_value = os.getenv("CORS_ORIGINS")
if cors_value:
    try:
        # If string is empty, use defaults
        if not cors_value.strip():
            settings = Settings(CORS_ORIGINS=DEFAULT_CORS_ORIGINS)
        # Try JSON format
        elif cors_value.startswith("["):
            try:
                origins = json.loads(cors_value)
                settings = Settings(CORS_ORIGINS=origins)
            except json.JSONDecodeError:
                # Fallback to comma-separated if JSON parsing fails
                origins = [origin.strip() for origin in cors_value.split(",") if origin.strip()]
                settings = Settings(CORS_ORIGINS=origins)
        # Use comma-separated format
        else:
            origins = [origin.strip() for origin in cors_value.split(",") if origin.strip()]
            settings = Settings(CORS_ORIGINS=origins)
    except Exception as e:
        print(f"Warning: Failed to process CORS_ORIGINS: {str(e)}")
        settings = Settings(CORS_ORIGINS=DEFAULT_CORS_ORIGINS)