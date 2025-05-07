from abc import ABC, abstractmethod
from typing import List, Dict, Any, Optional
from app.core.config import settings
from app.core.logging import loggers
from google import generativeai as genai
from google.generativeai import types
import hashlib
import base64

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
        if not settings.GOOGLE_API_KEY:
            raise ValueError("Google API key not configured")
        
        # Configure the Gemini API
        genai.configure(api_key=settings.GOOGLE_API_KEY)
        
        # Initialize the model
        self.model = genai.GenerativeModel('gemini-pro')
        
        # Default generation config
        self.generation_config = {
            "temperature": 0,
            "top_p": 0.97,
            "top_k": 45,
            "max_output_tokens": 8192,
        }
        
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
    
    def _get_language_config(self, language: str) -> Dict[str, Any]:
        """Get configuration for a specific language."""
        return settings.LANGUAGE_CONFIG.get(language, settings.LANGUAGE_CONFIG["default"])
    
    def get_max_chunk_size(self, language: str) -> int:
        """Get maximum chunk size for a language."""
        return self._get_language_config(language).get("max_chunk_size", settings.MAX_CHUNK_SIZE)
    
    def _generate_hash(self, text: str) -> str:
        """Generate a hash for the given text."""
        return base64.b64encode(hashlib.sha256(text.encode()).digest()).decode()
    
    def _chunk_text(self, text: str, max_chunk_size: int = None) -> List[str]:
        """Split text into chunks of maximum size."""
        if max_chunk_size is None:
            max_chunk_size = settings.MAX_CHUNK_SIZE
        
        chunks = []
        current_chunk = ""
        
        for line in text.split("\n"):
            if len(current_chunk) + len(line) + 1 <= max_chunk_size:
                current_chunk += line + "\n"
            else:
                if current_chunk:
                    chunks.append(current_chunk.strip())
                current_chunk = line + "\n"
        
        if current_chunk:
            chunks.append(current_chunk.strip())
        
        return chunks
    
    async def translate_text(self, text: str, target_language: str) -> str:
        """Translate text to target language."""
        if not text.strip():
            return ""
        
        # Get language-specific configuration
        config = self._get_language_config(target_language)
        
        # Split text into chunks if needed
        chunks = self._chunk_text(text, config.get("max_chunk_size"))
        
        # Translate each chunk
        translated_chunks = []
        for chunk in chunks:
            prompt = f"Translate the following text to {target_language}. Maintain the original formatting and structure:\n\n{chunk}"
            
            try:
                response = await self.model.generate_content_async(
                    prompt,
                    generation_config=self.generation_config
                )
                translated_chunks.append(response.text)
            except Exception as e:
                print(f"Error translating chunk: {str(e)}")
                raise
        
        return "\n".join(translated_chunks)
    
    async def translate_batch(self, texts: List[str], target_language: str) -> List[str]:
        """Translate a batch of texts to target language."""
        return [await self.translate_text(text, target_language) for text in texts]
    
    async def _call_gemini_api(
        self,
        prompt: str,
        model: str = None,
        temperature: float = 0,
        max_tokens: int = None,
        timeout: int = None
    ) -> str:
        """Make a call to the Gemini API with error handling."""
        if not self.model:
            raise TranslationError("Google Generative AI model not initialized", "MODEL_ERROR")
        
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
            response = await self.model.generate_content_async(
                prompt,
                generation_config=generation_config
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