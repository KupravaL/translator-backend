import fitz  # PyMuPDF
import asyncio
from typing import List, Dict, Any, Optional
from bs4 import BeautifulSoup
from app.core.config import settings
from app.core.logging import loggers
from .base import BaseTranslationService, TranslationError

logger = loggers["translation"]

class PDFTranslationService(BaseTranslationService):
    """Service for translating PDF documents."""
    
    def __init__(self):
        """Initialize the PDF translation service."""
        super().__init__()
        self.max_chunk_size = settings.MAX_CHUNK_SIZE
        self.batch_size = settings.BATCH_SIZE
    
    async def extract_content(self, content: bytes, file_type: str) -> str:
        """Extract content from a PDF file."""
        try:
            # Open PDF document
            doc = fitz.open(stream=content, filetype=file_type)
            total_pages = len(doc)
            logger.info(f"Processing PDF with {total_pages} pages")
            
            # Extract text and images from each page
            pages_content = []
            for page_num in range(total_pages):
                page = doc[page_num]
                
                # Extract text
                text = page.get_text()
                
                # Extract images
                image_list = page.get_images()
                for img_index, img in enumerate(image_list):
                    xref = img[0]
                    base_image = doc.extract_image(xref)
                    image_bytes = base_image["image"]
                    
                    # Convert image to text using Gemini Vision
                    try:
                        image_text = await self._extract_text_from_image(image_bytes)
                        if image_text:
                            text += f"\n[Image {img_index + 1}]: {image_text}\n"
                    except Exception as e:
                        logger.warning(f"Failed to extract text from image: {str(e)}")
                
                pages_content.append(text)
            
            # Combine all pages
            full_content = "\n\n".join(pages_content)
            logger.info(f"Successfully extracted content from {total_pages} pages")
            
            return full_content
            
        except Exception as e:
            raise TranslationError(f"Failed to extract PDF content: {str(e)}", "EXTRACTION_ERROR")
    
    async def translate_content(self, content: str, from_lang: str, to_lang: str) -> str:
        """Translate content from one language to another."""
        try:
            # Split content into chunks
            chunks = self._split_into_chunks(content, from_lang)
            logger.info(f"Split content into {len(chunks)} chunks")
            
            # Process chunks in parallel batches
            translated_chunks = []
            for i in range(0, len(chunks), self.batch_size):
                batch = chunks[i:i + self.batch_size]
                batch_results = await asyncio.gather(
                    *[self._translate_chunk(chunk, from_lang, to_lang) for chunk in batch],
                    return_exceptions=True
                )
                
                # Handle results
                for result in batch_results:
                    if isinstance(result, Exception):
                        logger.error(f"Translation error: {str(result)}")
                        translated_chunks.append("")  # Empty string for failed chunks
                    else:
                        translated_chunks.append(result)
            
            # Combine translated chunks
            translated_content = "\n\n".join(translated_chunks)
            logger.info("Successfully translated all chunks")
            
            return translated_content
            
        except Exception as e:
            raise TranslationError(f"Translation failed: {str(e)}", "TRANSLATION_ERROR")
    
    def _split_into_chunks(self, content: str, language: str) -> List[str]:
        """Split content into manageable chunks."""
        max_size = self.get_max_chunk_size(language)
        chunks = []
        
        # Split by paragraphs first
        paragraphs = content.split("\n\n")
        current_chunk = []
        current_size = 0
        
        for para in paragraphs:
            para_size = len(para)
            
            # If paragraph is too large, split it into sentences
            if para_size > max_size:
                sentences = para.split(". ")
                for sentence in sentences:
                    sentence_size = len(sentence)
                    
                    if current_size + sentence_size > max_size:
                        if current_chunk:
                            chunks.append("\n".join(current_chunk))
                            current_chunk = []
                            current_size = 0
                        
                        # If single sentence is too large, split by words
                        if sentence_size > max_size:
                            words = sentence.split()
                            temp_chunk = []
                            temp_size = 0
                            
                            for word in words:
                                if temp_size + len(word) + 1 > max_size:
                                    chunks.append(" ".join(temp_chunk))
                                    temp_chunk = [word]
                                    temp_size = len(word)
                                else:
                                    temp_chunk.append(word)
                                    temp_size += len(word) + 1
                            
                            if temp_chunk:
                                chunks.append(" ".join(temp_chunk))
                        else:
                            chunks.append(sentence)
                    else:
                        current_chunk.append(sentence)
                        current_size += sentence_size + 2
            
            # If adding paragraph would exceed max size, start new chunk
            elif current_size + para_size > max_size:
                chunks.append("\n".join(current_chunk))
                current_chunk = [para]
                current_size = para_size
            else:
                current_chunk.append(para)
                current_size += para_size + 2
        
        # Add remaining content
        if current_chunk:
            chunks.append("\n".join(current_chunk))
        
        return chunks
    
    async def _translate_chunk(self, chunk: str, from_lang: str, to_lang: str) -> str:
        """Translate a single chunk of text."""
        if not chunk.strip():
            return ""
        
        try:
            # Prepare translation prompt
            prompt = f"""Translate the following text from {from_lang} to {to_lang}.
            Preserve all formatting, special characters, and structure.
            Only return the translated text without any explanations or notes.
            
            Text to translate:
            {chunk}"""
            
            # Call Gemini API
            translated = await self._call_gemini_api(
                prompt=prompt,
                temperature=0.1,  # Low temperature for more consistent translations
                max_tokens=len(chunk) * 2  # Allow for longer translations
            )
            
            return translated
            
        except Exception as e:
            logger.error(f"Failed to translate chunk: {str(e)}")
            raise
    
    async def _extract_text_from_image(self, image_bytes: bytes) -> str:
        """Extract text from an image using Gemini Vision."""
        try:
            # Prepare image analysis prompt
            prompt = """Extract all text from this image.
            Include any numbers, symbols, or special characters.
            Preserve the original formatting and structure.
            Only return the extracted text without any explanations."""
            
            # Call Gemini Vision API
            response = await self._call_gemini_api(
                prompt=prompt,
                model="gemini-pro-vision",
                temperature=0.1
            )
            
            return response
            
        except Exception as e:
            logger.error(f"Failed to extract text from image: {str(e)}")
            return "" 