import time
import google.generativeai as genai
import os
import fitz
import tempfile
import gc
import logging
from datetime import datetime
from app.core.config import settings
from app.models.translation import TranslationProgress, TranslationChunk
from typing import List, Dict, Any, Optional
import re
from bs4 import BeautifulSoup, NavigableString
import io
import asyncio
import nest_asyncio

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] [Translation] %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S'
)
logger = logging.getLogger("translation")

class TranslationError(Exception):
    def __init__(self, message: str, code: str):
        super().__init__(message)
        self.code = code
        self.name = 'TranslationError'

class TranslationService:
    def __init__(self):
        # Initialize Google Gemini
        if settings.GOOGLE_API_KEY:
            genai.configure(api_key=settings.GOOGLE_API_KEY)
            # Use Gemini-2.0-flash for content extraction and translation
            self.extraction_model = genai.GenerativeModel(model_name="gemini-2.0-flash")
            self.translation_model = genai.GenerativeModel(model_name="gemini-2.0-flash")
            logger.info("Initialized Google Gemini models for extraction and translation")
        else:
            self.extraction_model = None
            self.translation_model = None
            logger.warning("Google API key not configured - extraction and translation functionality will be unavailable")
    
    def normalize_index(self, index_text):
        """Normalize index numbers by fixing common OCR and formatting errors"""
        if not index_text:
            return index_text
            
        # Fix common OCR errors in index numbers
        index_text = index_text.strip()
        index_text = re.sub(r'[Ll]\.', '1.', index_text)  # Replace 'L.' with '1.'
        
        # Replace incorrect decimal separators
        index_text = re.sub(r'(\d+)[,;](\d+)', r'\1.\2', index_text)
        
        # Fix missing dots
        if re.match(r'^\d+$', index_text):
            index_text = index_text + '.'
            
        # Fix spacing issues
        index_text = re.sub(r'\s+', '', index_text)
        
        # Fix incorrect indices
        index_text = re.sub(r'1\.1\.141', '1.1.1.4.1', index_text)
        index_text = re.sub(r'1\.1\.1\.42', '1.1.1.4.2', index_text)
        
        return index_text

    async def extract_from_image(self, image_bytes: bytes) -> str:
        """Extract content from an image using Google Gemini."""
        if not self.extraction_model:
            logger.error("Google API key not configured for image extraction")
            raise TranslationError("Google API key not configured", "CONFIG_ERROR")
        
        start_time = time.time()
        logger.info("Starting image content extraction")
        
        # Create temporary file
        img_path = None
        
        try:
            # Save image to temporary file
            with tempfile.NamedTemporaryFile(delete=False, suffix='.png') as temp_img:
                temp_img.write(image_bytes)
                img_path = temp_img.name
            
            logger.info(f"Image saved to temporary file: {img_path}")
            
            prompt = """Analyze this document and convert it to properly formatted HTML with intelligent structure detection.

Key Requirements:
1. Structure Detection:
   - Identify if content is tabular/columnar or regular flowing text
   - Use tables ONLY for truly tabular content with clear columns and rows
   - For form-like content (label: value pairs), use flex layout without visible borders
   - For regular paragraphs and text, use simple <p> tags without any table structure
   - Preserve exact spacing and layout while using appropriate HTML elements

2. Document Elements:
   - Use semantic HTML: <article>, <section>, <header>, <p>, <table> as appropriate
   - Use <h1> through <h6> for hierarchical headings
   - For columns/forms without visible borders, use:
     ```html
     <div class="form-row">
       <div class="label">Label:</div>
       <div class="value">Value</div>
     </div>
     ```
   - For actual tables with visible borders use:
     ```html
     <table class="data-table">
       <tr><td>Content</td></tr>
     </table>
     ```

3. Specific Cases:
   A. Regular Text:
      ```html
      <p>Regular paragraph text goes here without any table structure.</p>
      ```
   
   B. Form-like Content (no visible borders):
      ```html
      <div class="form-section">
        <div class="form-row">
          <div class="label">Name:</div>
          <div class="value">John Smith</div>
        </div>
      </div>
      ```
   
   C. True Table Content:
      ```html
      <table class="data-table">
        <tr>
          <th>Header 1</th>
          <th>Header 2</th>
        </tr>
        <tr>
          <td>Data 1</td>
          <td>Data 2</td>
        </tr>
      </table>
      ```

4. CSS Classes:
   - Use 'form-section' for form-like content
   - Use 'data-table' for true tables
   - Use 'text-content' for regular flowing text
   - Add 'no-borders' class to elements that shouldn't show borders

Analyze the content carefully and use the most appropriate structure for each section. Return only valid HTML."""

            # Read image data directly from the file
            with open(img_path, 'rb') as f:
                image_data = f.read()
                
            # Close file handle and ensure garbage collection
            del f
            gc.collect()
            
            logger.info(f"Sending image to Gemini for analysis")
            
            response = self.extraction_model.generate_content(
                contents=[prompt, {"mime_type": "image/jpeg", "data": image_data}],
                generation_config={"temperature": 0.1}
            )
            
            html_content = response.text.strip()
            html_content = html_content.replace('```html', '').replace('```', '').strip()
            
            # Add enhanced CSS styles
            css_styles = """
<style>
    .document {
        width: 100%;
        max-width: 1000px;
        margin: 0 auto;
        font-family: Arial, sans-serif;
        line-height: 1.5;
    }
    .text-content {
        margin-bottom: 1em;
    }
    .form-section {
        margin-bottom: 1em;
    }
    .form-row {
        display: flex;
        margin-bottom: 0.5em;
        gap: 1em;
    }
    .label {
        width: 200px;
        flex-shrink: 0;
    }
    .value {
        flex-grow: 1;
    }
    .data-table {
        width: 100%;
        border-collapse: collapse;
        margin-bottom: 1em;
    }
    .data-table:not(.no-borders) td,
    .data-table:not(.no-borders) th {
        border: 1px solid black;
        padding: 0.5em;
    }
    .no-borders td,
    .no-borders th {
        border: none !important;
    }
    .header {
        text-align: right;
        margin-bottom: 20px;
    }
</style>
"""
            if '<style>' not in html_content:
                html_content = f"{css_styles}\n{html_content}"
            
            # Process and normalize index numbers
            soup = BeautifulSoup(html_content, 'html.parser')
            for index_div in soup.find_all(class_='index'):
                index_text = index_div.get_text().strip()
                corrected_index = self.normalize_index(index_text)
                if corrected_index != index_text:
                    index_div.string = corrected_index
                    
            html_content = str(soup)
            
            if len(html_content) < 50 or '<' not in html_content:
                logger.error("Invalid or insufficient content extracted from image")
                raise TranslationError(
                    "Invalid or insufficient content extracted from image", 
                    "CONTENT_ERROR"
                )
            
            logger.info(f"Successfully extracted content from image, length: {len(html_content)} chars")
            logger.info(f"Image processing took {time.time() - start_time:.2f} seconds")
            
            return html_content
            
        except Exception as e:
            logger.error(f"Gemini image processing error: {str(e)}")
            raise TranslationError(
                f"Failed to process image: {str(e)}",
                getattr(e, 'code', 'PROCESSING_ERROR')
            )
        finally:
            # Clean up the temporary file
            if img_path and os.path.exists(img_path):
                try:
                    # Try multiple times to delete with small delays
                    for attempt in range(3):
                        try:
                            time.sleep(0.2)  # Small delay
                            os.close_fds()  # Try to close any open file descriptors
                            os.remove(img_path)
                            logger.debug(f"Deleted temporary image file: {img_path}")
                            break
                        except Exception as e:
                            if attempt == 2:  # Last attempt
                                logger.warning(f"Could not delete temp file {img_path}: {e}")
                except Exception as e:
                    logger.warning(f"Error during file cleanup: {e}")
    
    async def extract_page_content(self, pdf_bytes: bytes, page_index: int) -> str:
        """Extract content from a PDF page using Google Gemini."""
        if not self.extraction_model:
            logger.error("Google API key not configured for PDF extraction")
            raise TranslationError("Google API key not configured", "CONFIG_ERROR")
        
        start_time = time.time()
        logger.info(f"Starting content extraction for page {page_index + 1}")
        
        # Read PDF in memory without creating a file
        try:
            # Create an in-memory buffer for the PDF content
            buffer = io.BytesIO(pdf_bytes)
            
            # Open PDF with PyMuPDF directly from the buffer
            with fitz.open(stream=buffer, filetype="pdf") as doc:
                if page_index >= len(doc):
                    logger.warning(f"Page {page_index + 1} does not exist")
                    return ''
                
                page = doc[page_index]
                
                # Extract content with Gemini
                html_content = await self._get_formatted_text_from_gemini_buffer(page)
                
                if not html_content or html_content.strip() == '':
                    logger.error(f"Empty or too short content on page {page_index + 1}")
                    return ''
                
                logger.info(f"Successfully extracted content from page {page_index + 1}, length: {len(html_content)} chars")
                logger.info(f"Page extraction took {time.time() - start_time:.2f} seconds")
                
                return html_content
                
        except Exception as e:
            logger.error(f"Gemini processing error for page {page_index + 1}: {str(e)}")
            raise TranslationError(
                f"Failed to process page {page_index + 1}: {str(e)}",
                getattr(e, 'code', 'PROCESSING_ERROR')
            )
        finally:
            # Ensure buffer is closed
            if 'buffer' in locals():
                buffer.close()
            
            # Force garbage collection
            gc.collect()
    
    async def _get_formatted_text_from_gemini_buffer(self, page):
        """Use Gemini to analyze and extract formatted text with improved memory management"""
        page_index = page.number
        page_start_time = time.time()
        logger.info(f"Extracting formatted text from page {page_index + 1} using Gemini")
        
        # Create a pixmap without writing to disk
        pix = page.get_pixmap()
        
        # Convert pixmap to bytes in memory
        img_bytes = pix.tobytes(output="png")
        
        try:
            prompt = """Analyze this document and convert it to properly formatted HTML with intelligent structure detection.

Key Requirements:
1. Structure Detection:
   - Identify if content is tabular/columnar or regular flowing text
   - Use tables ONLY for truly tabular content with clear columns and rows
   - For form-like content (label: value pairs), use flex layout without visible borders
   - For regular paragraphs and text, use simple <p> tags without any table structure
   - Preserve exact spacing and layout while using appropriate HTML elements

2. Document Elements:
   - Use semantic HTML: <article>, <section>, <header>, <p>, <table> as appropriate
   - Use <h1> through <h6> for hierarchical headings
   - For columns/forms without visible borders, use:
     ```html
     <div class="form-row">
       <div class="label">Label:</div>
       <div class="value">Value</div>
     </div>
     ```
   - For actual tables with visible borders use:
     ```html
     <table class="data-table">
       <tr><td>Content</td></tr>
     </table>
     ```

3. Specific Cases:
   A. Regular Text:
      ```html
      <p>Regular paragraph text goes here without any table structure.</p>
      ```
   
   B. Form-like Content (no visible borders):
      ```html
      <div class="form-section">
        <div class="form-row">
          <div class="label">Name:</div>
          <div class="value">John Smith</div>
        </div>
      </div>
      ```
   
   C. True Table Content:
      ```html
      <table class="data-table">
        <tr>
          <th>Header 1</th>
          <th>Header 2</th>
        </tr>
        <tr>
          <td>Data 1</td>
          <td>Data 2</td>
        </tr>
      </table>
      ```

4. CSS Classes:
   - Use 'form-section' for form-like content
   - Use 'data-table' for true tables
   - Use 'text-content' for regular flowing text
   - Add 'no-borders' class to elements that shouldn't show borders

Analyze the content carefully and use the most appropriate structure for each section. Return only valid HTML."""

            response = self.extraction_model.generate_content(
                contents=[prompt, {"mime_type": "image/png", "data": img_bytes}],
                generation_config={"temperature": 0.1}
            )
            
            html_content = response.text.strip()
            html_content = html_content.replace('```html', '').replace('```', '').strip()
            
            # Add enhanced CSS styles
            css_styles = """
<style>
    .document {
        width: 100%;
        max-width: 1000px;
        margin: 0 auto;
        font-family: Arial, sans-serif;
        line-height: 1.5;
    }
    .text-content {
        margin-bottom: 1em;
    }
    .form-section {
        margin-bottom: 1em;
    }
    .form-row {
        display: flex;
        margin-bottom: 0.5em;
        gap: 1em;
    }
    .label {
        width: 200px;
        flex-shrink: 0;
    }
    .value {
        flex-grow: 1;
    }
    .data-table {
        width: 100%;
        border-collapse: collapse;
        margin-bottom: 1em;
    }
    .data-table:not(.no-borders) td,
    .data-table:not(.no-borders) th {
        border: 1px solid black;
        padding: 0.5em;
    }
    .no-borders td,
    .no-borders th {
        border: none !important;
    }
    .header {
        text-align: right;
        margin-bottom: 20px;
    }
</style>
"""
            if '<style>' not in html_content:
                html_content = f"{css_styles}\n{html_content}"
            
            # Process and normalize index numbers
            soup = BeautifulSoup(html_content, 'html.parser')
            for index_div in soup.find_all(class_='index'):
                index_text = index_div.get_text().strip()
                corrected_index = self.normalize_index(index_text)
                if corrected_index != index_text:
                    index_div.string = corrected_index
                    
            html_content = str(soup)
            logger.info(f"Page {page_index + 1} content processed, final size: {len(html_content)} chars")
            
            return html_content
            
        except Exception as e:
            logger.error(f"Error in Gemini processing for page {page_index + 1}: {e}")
            text = page.get_text()
            logger.warning(f"Falling back to plain text extraction for page {page_index + 1} ({len(text)} chars)")
            return f"<div class='text-content'>{text}</div>"
        finally:
            # Clean up resources
            del pix
            del img_bytes
            # Force garbage collection
            gc.collect()
            logger.debug(f"Resources cleaned up for page {page_index + 1}")
            logger.info(f"Total processing time for page {page_index + 1}: {time.time() - page_start_time:.2f} seconds")
    
    async def _get_formatted_text_from_gemini(self, page):
        """Legacy method - retained for backward compatibility"""
        return await self._get_formatted_text_from_gemini_buffer(page)
    
    async def translate_chunk(self, html_content: str, from_lang: str, to_lang: str, retries: int = 3, chunk_id: str = None) -> str:
        """Translate a chunk of HTML content using Google Gemini."""
        if not self.translation_model:
            logger.error("Google API key not configured for translation")
            raise TranslationError("Google API key not configured", "CONFIG_ERROR")
        
        if not chunk_id:
            chunk_id = f"{hash(html_content)}"[:7]
            
        start_time = time.time()
        logger.info(f"Starting translation of chunk {chunk_id} ({len(html_content)} chars) from {from_lang} to {to_lang}")
        
        last_error = None
        
        for attempt in range(1, retries + 1):
            try:
                # Create a prompt specifically designed for HTML translation
                prompt = f"""Translate the text content in this HTML from {from_lang} to {to_lang}.

IMPORTANT RULES:
1. ONLY translate the human-readable text content - DO NOT translate HTML tags, attributes, CSS classes or IDs.
2. Preserve ALL HTML tags, attributes, CSS classes, and structure exactly as they appear in the input.
3. Do not add any commentary, explanations, or notes to your response - ONLY return the translated HTML.
4. Keep all spacing, indentation, and formatting consistent with the input.
5. Ensure your output is valid HTML that can be rendered directly in a browser.
6. Don't translate content within <style> tags.

Here is the HTML to translate:

{html_content}
"""

                logger.info(f"Sending chunk {chunk_id} to Gemini for translation (attempt {attempt}/{retries})")
                translation_start = time.time()
                
                # Use a lower temperature for more reliable translations
                response = self.translation_model.generate_content(
                    prompt,
                    generation_config={
                        "temperature": 0.1,
                        "top_p": 0.95,
                        "top_k": 40,
                        "max_output_tokens": 8192
                    }
                )
                
                translation_duration = time.time() - translation_start
                logger.info(f"Gemini completed translation for chunk {chunk_id} in {translation_duration:.2f} seconds")
                
                translated_text = response.text.strip()
                
                # Clean up any code block formatting that might be added
                translated_text = translated_text.replace('```html', '').replace('```', '').strip()
                
                # Additional cleanup for any commentary that might be added
                cleanup_patterns = [
                    r"^Translation:\s*",
                    r"^Here's the translation:\s*",
                    r"^Translated text:\s*",
                    r"^Here is the translation:\s*",
                    r"^Here's the HTML content translated to [^:]+:\s*",
                    r"^The HTML content translated to [^:]+:\s*",
                    r"^Translated HTML content:\s*",
                    r"^Translated content:\s*",
                    r"^Here is the HTML translated [^:]*:\s*"
                ]
                
                for pattern in cleanup_patterns:
                    translated_text = re.sub(pattern, '', translated_text, flags=re.IGNORECASE)
                
                # If the response still begins with commentary, try to extract just the HTML
                if not translated_text.strip().startswith('<'):
                    logger.warning(f"Response doesn't start with HTML tag, attempting to extract HTML")
                    # Try to extract only the HTML portion by finding the first HTML tag
                    html_start = re.search(r'<\w+', translated_text)
                    if html_start:
                        logger.info(f"Found HTML tag at position {html_start.start()}")
                        translated_text = translated_text[html_start.start():]
                    else:
                        logger.error(f"Failed to find any HTML tags in response")
                
                # Check for empty result
                if len(translated_text) < 1:
                    logger.error(f"Empty translation result for chunk {chunk_id}")
                    raise TranslationError("Empty translation result", "CONTENT_ERROR")
                
                # Validate that the result is proper HTML
                if not translated_text.strip().startswith('<'):
                    logger.error(f"Translation result for chunk {chunk_id} is not valid HTML")
                    logger.error(f"Raw output starts with: {translated_text[:100]}...")
                    raise TranslationError("Translation result is not valid HTML", "CONTENT_ERROR")
                
                logger.info(f"Successfully translated chunk {chunk_id}, length: {len(translated_text)} chars")
                logger.info(f"Translation took {time.time() - start_time:.2f} seconds")
                return translated_text
                
            except Exception as e:
                logger.error(f"Translation error for chunk {chunk_id} (attempt {attempt}/{retries}): {str(e)}")
                logger.error(f"Error type: {type(e).__name__}")
                logger.error(f"Error details: {repr(e)}")
                last_error = e
                
                if attempt == retries:
                    logger.error(f"Translation failed after {retries} attempts for chunk {chunk_id}")
                    raise TranslationError(
                        f"Translation failed after {retries} attempts: {str(e)}",
                        getattr(e, 'code', 'TRANSLATION_ERROR')
                    )
                
                # Exponential backoff
                backoff_time = 2 ** attempt
                logger.info(f"Retrying chunk {chunk_id} in {backoff_time} seconds (attempt {attempt+1}/{retries})")
                time.sleep(backoff_time)
        
        raise TranslationError(
            f"Translation failed after all retries: {str(last_error)}",
            "TRANSLATION_ERROR"
        )

    nest_asyncio.apply()

    def translate_document_content_sync(self, process_id, file_content, from_lang, to_lang, file_type, db):
        """
        Synchronous version of translate_document_content for the worker pool.
        This wraps the async functions to work in a synchronous environment.
        """
        # Create a new event loop for this thread
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        
        try:
            # Run the async translation function in the new loop
            return loop.run_until_complete(
                self._translate_document_content_sync_wrapper(
                    process_id, file_content, from_lang, to_lang, file_type, db
                )
            )
        finally:
            # Clean up the event loop
            loop.close()

    async def _translate_document_content_sync_wrapper(self, process_id, file_content, from_lang, to_lang, file_type, db):
        """Async wrapper implementation that calls the existing async methods."""
        total_pages = 0
        translated_pages = []
        start_time = time.time()
        
        # Find translation progress record to check userId for potential refunds
        try:
            progress = db.query(TranslationProgress).filter(
                TranslationProgress.processId == process_id
            ).first()
            
            if not progress:
                logger.error(f"[TRANSLATE] Translation record not found for {process_id}")
                return {
                    "success": False,
                    "error": "Translation record not found"
                }
                
            user_id = progress.userId
        except Exception as e:
            logger.error(f"[TRANSLATE] Failed to get translation record: {str(e)}")
            user_id = None
        try:
            # Handle PDFs
            if file_type in settings.SUPPORTED_DOC_TYPES and 'pdf' in file_type:
                # Get PDF page count
                buffer = io.BytesIO(file_content)
                with fitz.open(stream=buffer, filetype="pdf") as doc:
                    total_pages = len(doc)
                
                logger.info(f"[TRANSLATE] PDF has {total_pages} pages for {process_id}")
                
                # Update total pages
                if progress:
                    progress.totalPages = total_pages
                    db.commit()
                
                # Process each page
                for page_index in range(total_pages):
                    current_page = page_index + 1
                    
                    # Update progress
                    progress = db.query(TranslationProgress).filter(
                        TranslationProgress.processId == process_id
                    ).first()
                    
                    if not progress or progress.status == "failed":
                        logger.warning(f"[TRANSLATE] Process was canceled or failed: {process_id}")
                        return
                        
                    progress.currentPage = current_page
                    progress.progress = int((current_page / total_pages) * 100)
                    db.commit()
                    
                    logger.info(f"[TRANSLATE] Processing page {current_page}/{total_pages} for {process_id}")
                    
                    # Extract content
                    html_content = await self.extract_page_content(file_content, page_index)
                    
                    if html_content and len(html_content.strip()) > 0:
                        logger.info(f"[TRANSLATE] Extracted {len(html_content)} chars from page {current_page}")
                        
                        # Translate content
                        translated_content = None
                        
                        # Split content if needed
                        if len(html_content) > 12000:
                            chunks = self.split_content_into_chunks(html_content, 10000)
                            logger.info(f"[TRANSLATE] Split into {len(chunks)} chunks")
                            
                            translated_chunks = []
                            for i, chunk in enumerate(chunks):
                                chunk_id = f"{process_id}-p{current_page}-c{i+1}"
                                logger.info(f"[TRANSLATE] Translating chunk {i+1}/{len(chunks)}")
                                try:
                                    chunk_result = await self.translate_chunk(
                                        chunk, from_lang, to_lang, retries=3, chunk_id=chunk_id
                                    )
                                    translated_chunks.append(chunk_result)
                                except Exception as chunk_error:
                                    logger.error(f"[TRANSLATE] Error translating chunk {i+1}: {str(chunk_error)}")
                                    # Continue with other chunks but mark this one as failed
                                    translated_chunks.append(f"<div class='error'>Translation error in section {i+1}: {str(chunk_error)}</div>")
                                
                            translated_content = self.combine_html_content(translated_chunks)
                        else:
                            chunk_id = f"{process_id}-p{current_page}"
                            try:
                                translated_content = await self.translate_chunk(
                                    html_content, from_lang, to_lang, retries=3, chunk_id=chunk_id
                                )
                            except Exception as chunk_error:
                                logger.error(f"[TRANSLATE] Error translating page {current_page}: {str(chunk_error)}")
                                # Create an error message instead
                                translated_content = f"<div class='error'>Translation error on page {current_page}: {str(chunk_error)}</div>"
                        
                        # Save translation
                        translation_chunk = TranslationChunk(
                            processId=process_id,
                            pageNumber=page_index,
                            content=translated_content
                        )
                        db.add(translation_chunk)
                        db.commit()
                        
                        translated_pages.append(page_index)
                    else:
                        logger.warning(f"[TRANSLATE] No content extracted from page {current_page}")

            elif file_type in settings.SUPPORTED_DOC_TYPES:
                # Handle non-PDF document types
                
                # Set total pages - simple estimate for now
                total_pages = 1
                if progress:
                    progress.totalPages = total_pages
                    progress.currentPage = 1
                    db.commit()
                
                logger.info(f"[TRANSLATE] Processing document with type {file_type} for {process_id}")
                
                # Extract content using document processing service
                try:
                    # Import lazily to avoid circular imports
                    from app.services.document_processing import document_processing_service
                    
                    # Process the document
                    html_content = await document_processing_service.process_text_document(
                        file_content, 
                        file_type
                    )
                    
                    if html_content and len(html_content.strip()) > 0:
                        logger.info(f"[TRANSLATE] Extracted {len(html_content)} chars from document")
                        
                        # Translate content
                        translated_content = None
                        
                        # Split content if needed
                        if len(html_content) > 12000:
                            chunks = self.split_content_into_chunks(html_content, 10000)
                            logger.info(f"[TRANSLATE] Split into {len(chunks)} chunks")
                            
                            translated_chunks = []
                            for i, chunk in enumerate(chunks):
                                chunk_id = f"{process_id}-doc-c{i+1}"
                                logger.info(f"[TRANSLATE] Translating chunk {i+1}/{len(chunks)}")
                                try:
                                    chunk_result = await self.translate_chunk(
                                        chunk, from_lang, to_lang, retries=3, chunk_id=chunk_id
                                    )
                                    translated_chunks.append(chunk_result)
                                except Exception as chunk_error:
                                    logger.error(f"[TRANSLATE] Error translating chunk {i+1}: {str(chunk_error)}")
                                    # Continue with other chunks but mark this one as failed
                                    translated_chunks.append(f"<div class='error'>Translation error in section {i+1}: {str(chunk_error)}</div>")
                                
                            translated_content = self.combine_html_content(translated_chunks)
                        else:
                            chunk_id = f"{process_id}-doc"
                            try:
                                translated_content = await self.translate_chunk(
                                    html_content, from_lang, to_lang, retries=3, chunk_id=chunk_id
                                )
                            except Exception as chunk_error:
                                logger.error(f"[TRANSLATE] Error translating document: {str(chunk_error)}")
                                # Create an error message instead
                                translated_content = f"<div class='error'>Translation error: {str(chunk_error)}</div>"
                        
                        # Save translation
                        translation_chunk = TranslationChunk(
                            processId=process_id,
                            pageNumber=0,
                            content=translated_content
                        )
                        db.add(translation_chunk)
                        db.commit()
                        
                        translated_pages.append(0)
                        logger.info(f"[TRANSLATE] Completed document translation")
                    else:
                        logger.error(f"[TRANSLATE] No content extracted from document with type {file_type}")
                        self._update_translation_status_sync(db, process_id, "failed")
                        return {
                            "success": False,
                            "error": f"No content extracted from document with type {file_type}"
                        }
                except Exception as doc_error:
                    logger.exception(f"[TRANSLATE] Document processing error: {str(doc_error)}")
                    self._update_translation_status_sync(db, process_id, "failed")
                    return {
                        "success": False,
                        "error": f"Document processing error: {str(doc_error)}"
                    }
            # Handle images
            elif file_type in settings.SUPPORTED_IMAGE_TYPES:
                # Set total pages = 1 for images
                total_pages = 1
                if progress:
                    progress.totalPages = total_pages
                    progress.currentPage = 1
                    db.commit()
                
                # Extract content
                try:
                    html_content = await self.extract_from_image(file_content)
                    
                    if html_content and len(html_content.strip()) > 0:
                        # Translate content
                        translated_content = None
                        
                        # Split content if needed
                        if len(html_content) > 12000:
                            chunks = self.split_content_into_chunks(html_content, 10000)
                            translated_chunks = []
                            for i, chunk in enumerate(chunks):
                                chunk_id = f"{process_id}-img-c{i+1}"
                                try:
                                    chunk_result = await self.translate_chunk(
                                        chunk, from_lang, to_lang, retries=3, chunk_id=chunk_id
                                    )
                                    translated_chunks.append(chunk_result)
                                except Exception as chunk_error:
                                    logger.error(f"[TRANSLATE] Error translating image chunk {i+1}: {str(chunk_error)}")
                                    translated_chunks.append(f"<div class='error'>Translation error in section {i+1}: {str(chunk_error)}</div>")
                                
                            translated_content = self.combine_html_content(translated_chunks)
                        else:
                            chunk_id = f"{process_id}-img"
                            try:
                                translated_content = await self.translate_chunk(
                                    html_content, from_lang, to_lang, retries=3, chunk_id=chunk_id
                                )
                            except Exception as e:
                                logger.error(f"[TRANSLATE] Error translating image: {str(e)}")
                                translated_content = f"<div class='error'>Translation error: {str(e)}</div>"
                        
                        # Save translation
                        translation_chunk = TranslationChunk(
                            processId=process_id,
                            pageNumber=0,
                            content=translated_content
                        )
                        db.add(translation_chunk)
                        db.commit()
                        
                        translated_pages.append(0)
                    else:
                        logger.error(f"[TRANSLATE] No content extracted from image")
                        raise TranslationError("No content extracted from image", "CONTENT_ERROR")
                except Exception as img_error:
                    logger.exception(f"[TRANSLATE] Image processing error: {str(img_error)}")
                    self._update_translation_status_sync(db, process_id, "failed")
                    return {
                        "success": False,
                        "error": f"Image processing error: {str(img_error)}"
                    }
            else:
                logger.error(f"[TRANSLATE] Unsupported file type: {file_type}")
                self._update_translation_status_sync(db, process_id, "failed")
                return {
                    "success": False,
                    "error": f"Unsupported file type: {file_type}"
                }
                
            # Complete translation process
            if len(translated_pages) > 0:
                logger.info(f"[TRANSLATE] Translation completed: {len(translated_pages)}/{total_pages} pages")
                
                # Update status to completed
                progress = db.query(TranslationProgress).filter(
                    TranslationProgress.processId == process_id
                ).first()
                
                if progress:
                    progress.status = "completed"
                    progress.progress = 100
                    progress.currentPage = total_pages
                    db.commit()
                    
                # Log completion
                duration = time.time() - start_time
                logger.info(f"[TRANSLATE] Translation completed in {duration:.2f}s for {process_id}")
                
                return {
                    "success": True,
                    "totalPages": total_pages,
                    "translatedPages": len(translated_pages)
                }
            else:
                logger.error(f"[TRANSLATE] No pages were translated for {process_id}")
                self._update_translation_status_sync(db, process_id, "failed")
                return {
                    "success": False,
                    "error": "No pages were translated"
                }
                
        except Exception as e:
            logger.exception(f"[TRANSLATE] Translation error: {str(e)}")
            self._update_translation_status_sync(db, process_id, "failed")
            return {
                "success": False,
                "error": str(e)
            }
        
    def _update_translation_status_sync(self, db, process_id, status, progress=0):
        """Synchronous version of update_translation_status for the worker."""
        try:
            translation_progress = db.query(TranslationProgress).filter(
                TranslationProgress.processId == process_id
            ).first()
            
            if translation_progress:
                translation_progress.status = status
                translation_progress.progress = progress if status == "failed" else translation_progress.progress
                db.commit()
                logger.info(f"Updated status to {status} for {process_id}")
                return True
            else:
                logger.error(f"No translation record found for {process_id}")
                return False
        except Exception as e:
            logger.exception(f"Failed to update status to {status}: {str(e)}")
            if 'db' in locals() and db:
                db.rollback()
            return False

    @staticmethod
    def split_content_into_chunks(content: str, max_size: int) -> List[str]:
        """Split content into chunks of maximum size."""
        chunks = []
        current_chunk = ''
        
        # Enhanced sentence splitting regex that preserves HTML tags
        sentences = content.split('. ')
        
        for sentence in sentences:
            # Add period back except for the last sentence
            if sentence != sentences[-1]:
                sentence += '.'
                
            if len(current_chunk) + len(sentence) > max_size:
                if current_chunk:
                    chunks.append(current_chunk.strip())
                current_chunk = sentence
            else:
                if current_chunk:
                    current_chunk += ' ' + sentence
                else:
                    current_chunk = sentence
        
        if current_chunk:
            chunks.append(current_chunk.strip())
            
        logger.info(f"Split content into {len(chunks)} chunks (max size: {max_size})")
        return chunks
    
    @staticmethod
    def combine_html_content(html_contents):
        """Combine multiple HTML contents into a single document"""
        combined = "<div class='document'>\n"
        for content in html_contents:
            content = re.sub(r'</?html[^>]*>', '', content)
            content = re.sub(r'</?head[^>]*>', '', content)
            content = re.sub(r'</?body[^>]*>', '', content)
            combined += f"<div class='page'>\n{content}\n</div>\n"
        combined += "</div>"
        
        logger.info(f"Combined {len(html_contents)} HTML content pieces into a single document of {len(combined)} chars")
        return combined

# Create a singleton instance
translation_service = TranslationService()