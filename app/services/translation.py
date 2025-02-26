import time
import google.generativeai as genai
import os
import fitz
import tempfile
import gc
import logging
from datetime import datetime
from anthropic import Anthropic
from app.core.config import settings
from typing import List, Dict, Any, Optional
import re
from bs4 import BeautifulSoup, NavigableString
import io

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
            self.gemini_model = genai.GenerativeModel(model_name="gemini-2.0-flash")
            logger.info("Initialized Google Gemini model")
        else:
            self.gemini_model = None
            logger.warning("Google API key not configured - OCR functionality will be unavailable")
            
        # Initialize Anthropic Claude
        if settings.ANTHROPIC_API_KEY:
            self.claude_client = Anthropic(api_key=settings.ANTHROPIC_API_KEY)
            logger.info("Initialized Anthropic Claude model")
        else:
            self.claude_client = None
            logger.warning("Anthropic API key not configured - translation functionality will be unavailable")
    
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
        if not self.gemini_model:
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
            
            response = self.gemini_model.generate_content(
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
        if not self.gemini_model:
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

            response = self.gemini_model.generate_content(
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
        """Translate a chunk of HTML content using Anthropic Claude."""
        if not self.claude_client:
            logger.error("Anthropic API key not configured for translation")
            raise TranslationError("Anthropic API key not configured", "CONFIG_ERROR")
        
        if not chunk_id:
            chunk_id = f"{hash(html_content)}"[:7]
            
        start_time = time.time()
        logger.info(f"Starting translation of chunk {chunk_id} ({len(html_content)} chars) from {from_lang} to {to_lang}")
        logger.info(f"Using API key: {settings.ANTHROPIC_API_KEY[:10]}...{settings.ANTHROPIC_API_KEY[-5:]}")
        
        last_error = None
        
        for attempt in range(1, retries + 1):
            try:
                # Improved system message with explicit instructions to avoid commentary
                system_message = """You are translating HTML content. Your ONLY task is to translate the text within HTML tags from the source language to the target language.

IMPORTANT RULES:
1. OUTPUT ONLY THE TRANSLATED HTML - do not include any explanations, introductions, or commentary
2. Do not add phrases like "Here's the translation" or "Translated content" to your response
3. Preserve ALL HTML tags and attributes exactly as they appear in the original
4. Maintain document structure, layout, classes, and styling
5. Keep all CSS classes, ID attributes, and other HTML attributes unchanged
6. Preserve table structures and form layouts exactly
7. Translate ONLY the visible text content that would be displayed to users

Your entire response must be valid HTML that could be directly used in a webpage without any modifications."""

                # User message with clear, concise instructions
                user_message = f"""Translate the text in this HTML from {from_lang} to {to_lang}.

{html_content}"""

                logger.info(f"Sending chunk {chunk_id} to Claude for translation (attempt {attempt}/{retries})")
                translation_start = time.time()
                
                # Use a more widely available model instead of claude-3-5-sonnet-20241022
                model_to_use = "claude-3-5-sonnet-20241022"
                logger.info(f"Using model: {model_to_use} for translation")
                
                response = self.claude_client.messages.create(
                    model=model_to_use,
                    max_tokens=4096,
                    system=system_message,
                    messages=[
                        {
                            "role": "user",
                            "content": user_message
                        }
                    ]
                )
                
                translation_duration = time.time() - translation_start
                logger.info(f"Claude completed translation for chunk {chunk_id} in {translation_duration:.2f} seconds")
                
                translated_text = response.content[0].text.strip()
                
                # Additional cleanup for any commentary that might still appear
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

translation_service = TranslationService()