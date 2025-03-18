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
            
            prompt = """Analyze this document and extract its content with precise structural preservation, extracting the content and formatting it in HTML:

1. Content Organization:
   - Maintain the original hierarchical structure (headers, sections, subsections)
   - IMPORTANT: In cases where the structure is messy, or you can't understand the structure of analyzed document, or if the document is unstructured, make sure to add some structure at your discretion to make the text readable.
   - IMPORTANT: Do not generate HTML FOR IMAGES. IF there is an image inside the document, JUST STKIP IT. Process text only, and it's formatting. The Output Must never have any <img. tags, if the image without any text is identified, skip it. 
   - Preserve paragraph boundaries and logical content grouping
   - Keep related data points together on the same line when they form a logical unit
   - Maintain chronological or numerical sequence where present
   - Take special attention to tables, if there are any. Sometimes 1 row/column can include several rows/columns insidet them, so preseve the exact formatting how it's in the document. 

2. Formatting Guidelines:
   - Clearly distinguish headers and section titles from body content
   - Preserve tabular data relationships without splitting related columns
   - Maintain proper indentation to show hierarchical relationships
   - Keep contextually related numbers, measurements, or values together with their labels

3. Special Handling:
   - For lists of measurements/values, keep all parameters and their values together
   - For date-based content, ensure dates are formatted consistently as section headers
   - For forms or structured data, preserve the relationship between fields and values
   - For technical/scientific data, maintain the relationship between identifiers and their measurements

4. Layout Preservation:
   - Identify when content is presented in columns and preserve column relationships
   - Avoid arbitrary line breaks that split conceptually unified information
   - Maintain spacing that indicates logical grouping in the original
   - Preserve the flow of information in a way that maintains readability

5. HTML Considerations:
   - Do not introduce HTML tags into plain text extraction unless specifically requested
   - If HTML formatting is present in the original, preserve semantic structure but not decorative elements
   - Properly handle tables by maintaining row and column relationships
   - If converting to HTML, use semantic tags to represent the document structure (<h1>, <p>, <ul>, <table>, etc.)
   - Ensure any HTML output is valid and properly nested

Extract the content with minimal unnecessary line breaks, using them only to separate distinct items or sections. The result should be clean, structured text that accurately represents the original document's organization and information hierarchy."""

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
            prompt = """Analyze and Convert this document to clean, semantic HTML while intelligently detecting its structure.

Core Requirements:
1. Structure Analysis:
   - Identify whether content is tabular data, form fields, or flowing text
   - Use appropriate HTML elements based on content type
   - Only use <table> for genuinely tabular information
   - Use flex layouts for form-like content with label:value pairs
   - Apply paragraph tags for standard text without forcing tabular structure
   - Maintain original spacing and layout using proper HTML semantics

2. HTML Element Selection:
   - Implement semantic HTML5 elements (<article>, <section>, <header>, etc.)
   - Use heading tags (<h1> through <h6>) to maintain hierarchy
   - For form-like content, implement:
     <div class="form-row">
       <div class="label">Label:</div>
       <div class="value">Value</div>
     </div>
   - For actual tabular data use:
     <table class="data-table">
       <tr><th>Header</th></tr>
       <tr><td>Data</td></tr>
     </table>

3. Content Type Handling:
   A. Standard Text:
      <p class="text-content">Regular paragraph text without table structure.</p>
   
   B. Form Content (no visible borders):
      <div class="form-section">
        <div class="form-row">
          <div class="label">Field Name:</div>
          <div class="value">Field Value</div>
        </div>
      </div>
   
   C. Tabular Data:
      <table class="data-table">
        <tr>
          <th>Column 1</th>
          <th>Column 2</th>
        </tr>
        <tr>
          <td>Value 1</td>
          <td>Value 2</td>
        </tr>
      </table>

4. CSS Class Implementation:
   - "form-section" for form content containers
   - "data-table" for genuine tables
   - "text-content" for regular text blocks
   - "no-borders" for elements that should appear borderless

Carefully analyze each section of the document and apply the most appropriate HTML structure. Do not include any images in the output, even if present in the source. Return only valid, well-formed HTML."""

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
    
    # Method 1: Translate chunk
    async def translate_chunk(self, html_content: str, from_lang: str, to_lang: str, retries: int = 3, chunk_id: str = None) -> str:
        """
        Translate a chunk of HTML content to the target language.
        This function is language-agnostic and will translate all text content to the target language,
        regardless of what languages are present in the source.
        """
        if not self.translation_model:
            logger.error("Google API key not configured for translation")
            raise TranslationError("Google API key not configured", "CONFIG_ERROR")
        
        if not chunk_id:
            chunk_id = f"{hash(html_content)}"[:7]
                
        start_time = time.time()
        logger.info(f"Starting translation of chunk {chunk_id} ({len(html_content)} chars) to {to_lang}")
        
        # Save original HTML for comparison
        original_html = html_content
        
        last_error = None
        
        for attempt in range(1, retries + 1):
            try:
                # Create a prompt specifically designed for HTML translation with language-agnostic instructions
                prompt = f"""Translate all text content in this HTML to {to_lang}.

    IMPORTANT RULES:
    1. Translate ALL text content to {to_lang} regardless of what language it's in
    2. Preserve ALL HTML tags, attributes, CSS classes, and structure exactly as they appear in the input
    3. Do not add any commentary, explanations, or notes to your response - ONLY return the translated HTML
    4. Keep all spacing, indentation, and formatting consistent with the input
    5. Ensure your output is valid HTML that can be rendered directly in a browser
    6. Don't translate content within <style> tags
    7. Don't translate these specific items:
    - Technical codes and identifiers (like product IDs, registration numbers)
    - Email addresses and URLs
    - Physical addresses 
    - Brand and company names
    - Technical standards (like EN 14411:2016)
    - Unit measurements and technical values (like NPD, N/mm2)

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
                
                # Verify that the HTML structure is preserved
                # If the original had a div.document or div.page, the translated version should too
                if ('<div class="document"' in original_html or "<div class='document'" in original_html) and \
                not ('<div class="document"' in translated_text or "<div class='document'" in translated_text):
                    logger.warning(f"Document structure may be lost in translation, attempting to fix")
                    try:
                        # Try to wrap the content in document/page structure if needed
                        soup = BeautifulSoup(translated_text, 'html.parser')
                        if not soup.find('div', class_='document'):
                            # Create new document structure
                            doc_div = soup.new_tag('div', attrs={'class': 'document'})
                            page_div = soup.new_tag('div', attrs={'class': 'page'})
                            
                            # Move all content into the page div
                            for child in list(soup.children):
                                if child.name:  # Skip NavigableString objects
                                    page_div.append(child.extract())
                            
                            # Build the structure
                            doc_div.append(page_div)
                            soup.append(doc_div)
                            translated_text = str(soup)
                            logger.info(f"Fixed document structure in translated content")
                    except Exception as struct_error:
                        logger.warning(f"Couldn't fix document structure: {str(struct_error)}")
                
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

    # Method 2: Split content into chunks
    def split_content_into_chunks(self, content: str, max_size: int) -> List[str]:
        """
        Split content into chunks of maximum size while preserving HTML structure.
        Language-agnostic implementation for universal translation support.
        """
        logger.info(f"Splitting content of length {len(content)} into chunks (max size: {max_size})")
        
        # If content is small enough, return it as a single chunk
        if len(content) <= max_size:
            logger.info("Content fits in a single chunk, no splitting needed")
            return [content]
        
        try:
            # Use BeautifulSoup to parse the HTML
            soup = BeautifulSoup(content, 'html.parser')
            
            # Check if we have a document/page structure
            has_document_structure = bool(soup.find('div', class_='document'))
            
            # Find all elements to split by (either pages or top-level elements)
            pages = soup.find_all('div', class_='page')
            
            if pages:
                logger.info(f"Found {len(pages)} page divs to split by")
                
                # If we have pages, split by page
                chunks = []
                current_chunk = ""
                current_pages = []
                
                for page in pages:
                    page_html = str(page)
                    
                    # If adding this page would exceed max size, start a new chunk
                    if len(current_chunk) + len(page_html) > max_size and current_chunk:
                        # Create a proper document structure
                        if has_document_structure:
                            chunk = f'<div class="document">{current_chunk}</div>'
                        else:
                            chunk = current_chunk
                            
                        chunks.append(chunk)
                        logger.info(f"Created chunk with {len(current_pages)} pages, size: {len(chunk)}")
                        
                        # Start a new chunk with this page
                        current_chunk = page_html
                        current_pages = [page]
                    else:
                        # Add page to current chunk
                        current_chunk += page_html
                        current_pages.append(page)
                
                # Add the last chunk if it has content
                if current_chunk:
                    if has_document_structure:
                        chunk = f'<div class="document">{current_chunk}</div>'
                    else:
                        chunk = current_chunk
                        
                    chunks.append(chunk)
                    logger.info(f"Created final chunk with {len(current_pages)} pages, size: {len(chunk)}")
                
                logger.info(f"Split content into {len(chunks)} chunks by pages")
                return chunks
                
            else:
                # If no pages, get direct children of document div or body
                doc_div = soup.find('div', class_='document')
                if doc_div:
                    elements = list(doc_div.children)
                    logger.info(f"Found document div with {len(elements)} direct children")
                else:
                    body = soup.body or soup
                    elements = list(body.children)
                    logger.info(f"No document structure found, using {len(elements)} body/root children")
                
                # Filter to keep only tag elements and non-empty strings
                elements = [el for el in elements if el.name or (isinstance(el, str) and el.strip())]
                logger.info(f"Found {len(elements)} significant elements to distribute into chunks")
                
                # Create chunks based on these elements
                chunks = []
                current_chunk = ""
                current_elements = []
                
                for element in elements:
                    # Convert element to string
                    element_str = str(element)
                    
                    # If adding this element would exceed max size, start a new chunk
                    if len(current_chunk) + len(element_str) > max_size and current_chunk:
                        # Wrap in appropriate structure
                        if has_document_structure:
                            chunk = f'<div class="document"><div class="page">{current_chunk}</div></div>'
                        else:
                            chunk = f'<div class="page">{current_chunk}</div>'
                            
                        chunks.append(chunk)
                        logger.info(f"Created chunk with {len(current_elements)} elements, size: {len(chunk)}")
                        
                        # Start a new chunk with this element
                        current_chunk = element_str
                        current_elements = [element]
                    else:
                        # Add element to current chunk
                        current_chunk += element_str
                        current_elements.append(element)
                
                # Add the last chunk if it has content
                if current_chunk:
                    if has_document_structure:
                        chunk = f'<div class="document"><div class="page">{current_chunk}</div></div>'
                    else:
                        chunk = f'<div class="page">{current_chunk}</div>'
                        
                    chunks.append(chunk)
                    logger.info(f"Created final chunk with {len(current_elements)} elements, size: {len(chunk)}")
                
                logger.info(f"Split content into {len(chunks)} chunks by elements")
                return chunks
                
        except Exception as e:
            # Fall back to simpler splitting approach if BeautifulSoup fails
            logger.warning(f"Error using structural splitting, falling back to basic approach: {str(e)}")
            
            # Check if we have a document structure
            has_document_structure = '<div class="document"' in content or "<div class='document'" in content
            
            # Basic approach - try to preserve page structure
            has_pages = '<div class="page"' in content or "<div class='page'" in content
            
            if has_pages:
                # Try to split by page divs
                page_divs = re.findall(r'(<div class=["\']page["\'][^>]*>.*?</div>)', content, re.DOTALL)
                
                if page_divs:
                    chunks = []
                    current_chunk = ""
                    current_pages = []
                    
                    for page in page_divs:
                        if len(current_chunk) + len(page) > max_size and current_chunk:
                            if has_document_structure:
                                chunk = f'<div class="document">{current_chunk}</div>'
                            else:
                                chunk = current_chunk
                                
                            chunks.append(chunk)
                            current_chunk = page
                            current_pages = [page]
                        else:
                            current_chunk += page
                            current_pages.append(page)
                    
                    if current_chunk:
                        if has_document_structure:
                            chunk = f'<div class="document">{current_chunk}</div>'
                        else:
                            chunk = current_chunk
                            
                        chunks.append(chunk)
                    
                    if chunks:
                        logger.info(f"Split content into {len(chunks)} chunks using page regex")
                        return chunks
            
            # If we can't split by pages, try paragraphs or divs
            logger.warning("Couldn't split by pages, trying paragraph/div boundaries")
            chunks = []
            current_chunk = ""
            
            # Try to split at paragraph or div boundaries
            parts = re.split(r'(</p>|</div>)', content)
            
            for i in range(0, len(parts), 2):
                part = parts[i]
                # Add the closing tag if it exists
                if i+1 < len(parts):
                    part += parts[i+1]
                    
                if len(current_chunk) + len(part) > max_size and current_chunk:
                    # Make sure we have valid HTML with appropriate structure
                    if not current_chunk.startswith('<div'):
                        if has_document_structure:
                            current_chunk = f'<div class="document"><div class="page">{current_chunk}</div></div>'
                        else:
                            current_chunk = f'<div class="page">{current_chunk}</div>'
                    
                    chunks.append(current_chunk)
                    current_chunk = part
                else:
                    current_chunk += part
            
            # Add the last chunk if it has content
            if current_chunk:
                if not current_chunk.startswith('<div'):
                    if has_document_structure:
                        current_chunk = f'<div class="document"><div class="page">{current_chunk}</div></div>'
                    else:
                        current_chunk = f'<div class="page">{current_chunk}</div>'
                
                chunks.append(current_chunk)
            
            # If we still have no chunks, use very simple approach
            if not chunks:
                logger.warning("Fallback to fixed-size chunk splitting")
                for i in range(0, len(content), max_size):
                    chunk = content[i:i+max_size]
                    if not chunk.startswith('<div'):
                        if has_document_structure:
                            chunk = f'<div class="document"><div class="page">{chunk}</div></div>'
                        else:
                            chunk = f'<div class="page">{chunk}</div>'
                    
                    chunks.append(chunk)
            
            logger.info(f"Split content into {len(chunks)} chunks using fallback method")
            return chunks

    # Method 3: Combine HTML content
    def combine_html_content(self, html_contents):
        """
        Combine multiple HTML contents into a single document.
        Language-agnostic implementation for consistent translation results.
        """
        if not html_contents:
            logger.warning("No HTML contents to combine")
            return ""
            
        logger.info(f"Combining {len(html_contents)} HTML content pieces into a single document")
        
        # First, check if we need document/page structure
        first_chunk = html_contents[0] if html_contents else ""
        needs_document_wrapper = not ('<div class="document"' in first_chunk or "<div class='document'" in first_chunk)
        needs_page_wrapper = not ('<div class="page"' in first_chunk or "<div class='page'" in first_chunk)
        
        try:
            # Use BeautifulSoup for robust HTML parsing
            combined_html = ""
            page_contents = []
            
            for i, content in enumerate(html_contents):
                # Clean up the content
                content = re.sub(r'<!DOCTYPE[^>]*>', '', content, flags=re.IGNORECASE)
                content = re.sub(r'</?html[^>]*>', '', content, flags=re.IGNORECASE)
                content = re.sub(r'</?head[^>]*>', '', content, flags=re.IGNORECASE)
                content = re.sub(r'</?body[^>]*>', '', content, flags=re.IGNORECASE)
                
                # Parse with BeautifulSoup
                soup = BeautifulSoup(content, 'html.parser')
                
                # Extract the relevant content
                if soup.find('div', class_='document'):
                    # If this chunk has a document structure, extract the pages
                    pages = soup.find_all('div', class_='page')
                    if pages:
                        for page in pages:
                            page_contents.append(str(page))
                    else:
                        # If no pages but has document, extract the document content
                        doc_div = soup.find('div', class_='document')
                        page_contents.append(f'<div class="page">{doc_div.decode_contents()}</div>')
                elif soup.find('div', class_='page'):
                    # If this chunk has pages but no document, add the pages
                    pages = soup.find_all('div', class_='page')
                    for page in pages:
                        page_contents.append(str(page))
                else:
                    # No document or page structure, wrap everything in a page
                    page_contents.append(f'<div class="page">{str(soup)}</div>')
            
            # Now build the combined HTML
            if needs_document_wrapper:
                combined_html = f'<div class="document">\n{"".join(page_contents)}\n</div>'
            else:
                combined_html = "".join(page_contents)
            
            logger.info(f"Successfully combined chunks into a document of {len(combined_html)} chars")
            return combined_html
            
        except Exception as e:
            logger.error(f"Error combining HTML with BeautifulSoup: {str(e)}")
            logger.info("Falling back to basic HTML combining approach")
            
            # Simple concatenation approach as fallback
            combined = ""
            
            # Determine if we need wrappers
            needs_document = not any('<div class="document"' in chunk or "<div class='document'" in chunk for chunk in html_contents)
            needs_page = not any('<div class="page"' in chunk or "<div class='page'" in chunk for chunk in html_contents)
            
            if needs_document:
                combined = '<div class="document">\n'
            
            for content in html_contents:
                # Clean up the content
                content = re.sub(r'<!DOCTYPE[^>]*>', '', content, flags=re.IGNORECASE)
                content = re.sub(r'</?html[^>]*>', '', content, flags=re.IGNORECASE)
                content = re.sub(r'</?head[^>]*>', '', content, flags=re.IGNORECASE)
                content = re.sub(r'</?body[^>]*>', '', content, flags=re.IGNORECASE)
                
                # Remove document wrapper if present and we're adding our own
                if needs_document:
                    content = re.sub(r'<div class=["\'](document|chunk)["\'][^>]*>(.*?)</div>', r'\2', content, flags=re.DOTALL|re.IGNORECASE)
                
                # Add page wrapper if needed
                if needs_page and not ('<div class="page"' in content or "<div class='page'" in content):
                    content = f'<div class="page">\n{content}\n</div>'
                    
                combined += content + '\n'
            
            if needs_document:
                combined += '</div>'
                
            logger.info(f"Combined chunks into document of {len(combined)} chars using basic approach")
            return combined

# Create a singleton instance
translation_service = TranslationService()
