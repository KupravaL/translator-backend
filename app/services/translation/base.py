from abc import ABC, abstractmethod
from typing import List, Dict, Any, Optional
from app.core.config import settings
from app.core.logging import loggers
from google import generativeai as genai
from google.generativeai import types

logger = loggers["translation"]

class TranslationError(Exception):
    """Base exception for translation errors."""
    def __init__(self, message: str, code: str):
        super().__init__(message)
        self.code = code
        self.name = 'TranslationError'

class BaseTranslationService(ABC):
    """Base class for translation services."""
    
    def __init__(self):
        """Initialize the translation service."""
        if settings.GOOGLE_API_KEY:
            self.client = genai.Client(api_key=settings.GOOGLE_API_KEY)
            self.model = "gemini-2.5-pro-preview-05-06"
            logger.info("Initialized Google Gemini client")
        else:
            self.client = None
            self.model = None
            logger.warning("Google API key not configured")
        
        # Initialize caches
        self.extraction_cache = {}
        self.translation_cache = {}
        
        # Set API timeout
        self.api_timeout = settings.API_TIMEOUT
    
    @abstractmethod
    async def extract_content(self, content: bytes, file_type: str) -> str:
        """Extract content from a file."""
        pass
    
    @abstractmethod
    async def translate_content(self, content: str, from_lang: str, to_lang: str) -> str:
        """Translate content from one language to another."""
        pass
    
    def get_language_config(self, language: str) -> Dict[str, Any]:
        """Get language-specific configuration."""
        return settings.LANGUAGE_CONFIG.get(language, settings.LANGUAGE_CONFIG["default"])
    
    def get_max_chunk_size(self, language: str) -> int:
        """Get maximum chunk size for a language."""
        return self.get_language_config(language).get("max_chunk_size", settings.MAX_CHUNK_SIZE)
    
    async def _call_gemini_api(
        self,
        prompt: str,
        model: str = None,
        temperature: float = 0,
        max_tokens: int = None,
        timeout: int = None
    ) -> str:
        """Make a call to the Gemini API with error handling."""
        if not self.client:
            raise TranslationError("Google API key not configured", "CONFIG_ERROR")
        
        try:
            # Prepare generation config
            generation_config = types.GenerateContentConfig(
                temperature=temperature,
                response_mime_type="text/plain",
                max_output_tokens=max_tokens
            )
            
            # Prepare content
            contents = [
                types.Content(
                    role="user",
                    parts=[types.Part.from_text(text=prompt)]
                )
            ]
            
            # Make API call
            response = await self.client.models.generate_content(
                model=model or self.model,
                contents=contents,
                config=generation_config
            )
            
            if not response.text:
                raise TranslationError("Empty response from Gemini API", "EMPTY_RESPONSE")
            
            return response.text.strip()
            
        except Exception as e:
            error_str = str(e)
            if "429" in error_str or "rate limit" in error_str.lower():
                raise TranslationError(f"Rate limit exceeded: {error_str}", "RATE_LIMIT")
            elif "timeout" in error_str.lower():
                raise TranslationError(f"API request timed out: {error_str}", "TIMEOUT")
            else:
                raise TranslationError(f"API error: {error_str}", "API_ERROR") 