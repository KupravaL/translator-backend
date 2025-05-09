import time
import os
import fitz
import tempfile
import gc
import logging
from datetime import datetime
from app.core.config import settings
from app.models.translation import TranslationProgress, TranslationChunk
from typing import List, Dict, Any, Optional, Union
import re
from bs4 import BeautifulSoup, NavigableString, Comment
import io
import asyncio
import nest_asyncio
import hashlib
from google import generativeai as genai
from google.generativeai import types
import base64
from app.services.translation.base import BaseTranslationService

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

class TranslationService(BaseTranslationService):
    def __init__(self):
        # Initialize Google Gemini
        if settings.GOOGLE_API_KEY:
            self.client = genai.Client(api_key=settings.GOOGLE_API_KEY)
            self.extraction_model = "gemini-2.5-pro-preview-05-06"
            self.translation_model = "gemini-2.5-pro-preview-05-06"
            logger.info("Initialized Google Gemini 2.5 client for extraction and translation")
        else:
            self.client = None
            self.extraction_model = None
            self.translation_model = None
            logger.warning("Google API key not configured - extraction and translation functionality will be unavailable")
        
        # Add simple cache for extraction results
        self.extraction_cache = {}
        self.translation_cache = {}
        
        # Set maximum request timeout
        self.api_timeout = 30  # 30 seconds per request max (reduced from 45)
        
        # Language-specific configuration - uniform approach for all languages
        self.language_config = {
            # Default settings for all languages
            "default": {
                "temperature": 0,
                "top_p": 0.97,
                "top_k": 45,
                "max_chunk_size": 9000,
                "max_output_tokens": 8192
            }
        }
        
        # Placeholder detection patterns
        self.placeholder_patterns = [
            r'\$[a-zA-Z0-9_]+',  # $ka, $variable pattern
            r'\{\{[a-zA-Z0-9_]+\}\}',  # {{placeholder}} pattern
            r'\[\[[a-zA-Z0-9_]+\]\]'   # [[placeholder]] pattern
        ]
        
        # Language code mapping for proper identification
        self.language_map = {
            "georgian": "ka",
            "english": "en",
            "russian": "ru",
            "spanish": "es",
            "french": "fr",
            "german": "de",
            "italian": "it",
            "japanese": "ja",
            "chinese": "zh",
            "arabic": "ar",
            # Add more as needed
        }
        
        # Reverse mapping for display names
        self.language_display_names = {code: name for name, code in self.language_map.items()}
        self.language_display_names.update({
            "ka": "Georgian",
            "en": "English",
            "ru": "Russian",
            "es": "Spanish",
            "fr": "French",
            "de": "German",
            "it": "Italian",
            "ja": "Japanese",
            "zh": "Chinese",
            "ar": "Arabic"
        })

    def get_language_code(self, language: str) -> str:
        """Convert language name to ISO code for configuration lookup"""
        # If already a code, return lower case
        if len(language) == 2:
            return language.lower()
            
        # Try to get language code from name
        return self.language_map.get(language.lower(), "default")
    
    def get_language_display_name(self, language_code: str) -> str:
        """Convert language code to a display name"""
        return self.language_display_names.get(language_code.lower(), language_code)
    
    def get_language_config(self, language: str) -> Dict[str, Any]:
        """Get language-specific configuration parameters"""
        lang_code = self.get_language_code(language)
        
        # Get language specific config or default if not found
        if lang_code in self.language_config:
            return self.language_config[lang_code]
        else:
            return self.language_config["default"]
    
    def get_max_chunk_size(self, to_lang: str) -> int:
        """Get the appropriate maximum chunk size for a given language"""
        lang_config = self.get_language_config(to_lang)
        return lang_config.get("max_chunk_size", 9000)
    
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

    def tag_untranslatable_content(self, html_content: str) -> str:
        """
        Tag content that should not be translated with HTML comments
        to help the model preserve them correctly.
        """
        soup = BeautifulSoup(html_content, 'html.parser')
        
        # For email addresses
        email_pattern = re.compile(r'[\w.+-]+@[\w-]+\.[\w.-]+')
        for tag in list(soup.find_all(string=True)):  # Use list() to create a static copy of elements
            if isinstance(tag, NavigableString) and not isinstance(tag, Comment):
                # Skip style tags
                if tag.parent and tag.parent.name == 'style':
                    continue
                    
                content = str(tag)
                # Find all email addresses
                matches = list(email_pattern.finditer(content))
                if matches:
                    # Create new string with all email addresses preserved
                    new_text = content
                    for email in reversed(matches):  # Process in reverse to avoid index issues
                        email_text = email.group(0)
                        start, end = email.span()
                        new_text = new_text[:start] + f"<!--PRESERVE-->{email_text}<!--/PRESERVE-->" + new_text[end:]
                    
                    # Only replace if we actually modified the content
                    if new_text != content and tag.parent:
                        tag.replace_with(NavigableString(new_text))
        
        # For URLs in href attributes
        for tag in soup.find_all(href=True):
            tag['href'] = f"<!--PRESERVE-->{tag['href']}<!--/PRESERVE-->"
            
        # For technical codes, IDs, etc.
        code_pattern = re.compile(r'\b[A-Z0-9]{5,}\b')
        for tag in list(soup.find_all(string=True)):  # Use list() to create a static copy
            if isinstance(tag, NavigableString) and not isinstance(tag, Comment):
                if not tag.parent:  # Skip tags without a parent
                    continue
                    
                content = str(tag)
                # Find all technical codes
                matches = list(code_pattern.finditer(content))
                if matches:
                    # Create new string with all codes preserved
                    new_text = content
                    for code in reversed(matches):  # Process in reverse to avoid index issues
                        code_text = code.group(0)
                        start, end = code.span()
                        new_text = new_text[:start] + f"<!--PRESERVE-->{code_text}<!--/PRESERVE-->" + new_text[end:]
                    
                    # Only replace if we actually modified the content
                    if new_text != content:
                        tag.replace_with(NavigableString(new_text))
                
        return str(soup)

    def clean_preservation_tags(self, html_content: str) -> str:
        """
        Thoroughly remove all preservation tags from HTML content using
        multiple approaches to ensure all tags are removed properly.
        """
        # First attempt with BeautifulSoup
        soup = BeautifulSoup(html_content, 'html.parser')
        for comment in soup.find_all(string=lambda text: isinstance(text, Comment)):
            if 'PRESERVE' in comment:
                # Extract the preserved content
                match = re.search(r'<!--PRESERVE-->(.*?)<!--/PRESERVE-->', str(comment))
                if match:
                    preserved_content = match.group(1)
                    # Replace the comment with the preserved content
                    comment.replace_with(preserved_content)
        html_content = str(soup)
        
        # Regex-based cleanup for any remaining tags in various formats
        # Standard HTML comment format
        html_content = re.sub(r'<!--PRESERVE-->', '', html_content)
        html_content = re.sub(r'<!--/PRESERVE-->', '', html_content)
        
        # HTML-encoded format
        html_content = re.sub(r'&lt;!--PRESERVE--&gt;', '', html_content)
        html_content = re.sub(r'&lt;!--/PRESERVE--&gt;', '', html_content)
        
        # Unicode escape format
        html_content = re.sub(r'\\u003c!--PRESERVE--\\u003e', '', html_content)
        html_content = re.sub(r'\\u003c!--/PRESERVE--\\u003e', '', html_content)
        
        # Any other potential nested patterns
        html_content = re.sub(r'<!--.*?PRESERVE.*?-->', '', html_content)
        
        return html_content

    def fix_placeholder_issues(self, translated_text: str, original_html: str) -> str:
        """
        Fix placeholder issues in translated text by comparing with original content.
        """
        logger.info("Attempting to fix placeholder issues in translation")
        
        # Parse both HTML documents
        soup_orig = BeautifulSoup(original_html, 'html.parser')
        soup_trans = BeautifulSoup(translated_text, 'html.parser')
        
        # Function to replace placeholders with original text if necessary
        def process_node(trans_node, orig_node):
            # If this is a text node
            if isinstance(trans_node, NavigableString) and isinstance(orig_node, NavigableString):
                text = str(trans_node)
                # Check for placeholder pattern
                has_placeholder = any(re.search(pattern, text) for pattern in self.placeholder_patterns)
                if has_placeholder:
                    # Use original text as fallback
                    logger.info(f"Replacing placeholder text: '{text}' with original content")
                    return NavigableString(str(orig_node))
            
            return None  # No change
        
        # Try to fix placeholders while preserving structure
        try:
            # Map corresponding elements by structural position and fix placeholders
            def process_trees(trans_elem, orig_elem):
                # Process children only if both elements exist and have same tag
                if trans_elem and orig_elem and trans_elem.name == orig_elem.name:
                    # Process each child
                    trans_children = list(trans_elem.children)
                    orig_children = list(orig_elem.children)
                    
                    # If element counts match, we can try to align directly
                    if len(trans_children) == len(orig_children):
                        for i in range(len(trans_children)):
                            if i >= len(trans_children) or i >= len(orig_children):
                                break  # Safety check
                                
                            t_child = trans_children[i]
                            o_child = orig_children[i] 
                            
                            # If both are strings, check for placeholders
                            if isinstance(t_child, NavigableString) and isinstance(o_child, NavigableString):
                                replacement = process_node(t_child, o_child)
                                if replacement and t_child.parent:  # Only replace if parent exists
                                    t_child.replace_with(replacement)
                            # Recursive processing for elements
                            elif hasattr(t_child, 'name') and hasattr(o_child, 'name'):
                                process_trees(t_child, o_child)
            
            # Start recursive processing from the root
            process_trees(soup_trans, soup_orig)
            result = str(soup_trans)
            
            # Final check for any remaining placeholders
            final_placeholders = []
            for pattern in self.placeholder_patterns:
                final_placeholders.extend(re.findall(pattern, result))
            
            if final_placeholders:
                logger.warning(f"After fixing, {len(final_placeholders)} placeholders remain")
            else:
                logger.info("Successfully removed all placeholders")
                
            return result
            
        except Exception as e:
            logger.error(f"Error fixing placeholders: {str(e)}")
            return translated_text  # Return original on error
        
    async def extract_from_image(self, image_bytes: bytes) -> str:
        """Extract content from an image using Google Gemini."""
        if not self.client:
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
            
            prompt = """You are a professional multilanguage translator with a deep knowledge of HTML. Analyze this document and extract its content with precise structural preservation, extracting the content and formatting it in HTML:

1. Content Organization:
   - Maintain the original hierarchical structure (headers, sections, subsections)
   - IMPORTANT: In cases where the structure is messy, or you can't understand the structure of analyzed document, or if the document is unstructured, make sure to add some structure at your discretion to make the text readable.
   - IMPORTANT: NEVER GENERATE HTML FOR IMAGES. ALWAYS SKIP IMAGES. IF there is an image inside the document, JUST STKIP IT. Process text only, and it's formatting. The Output Must never have any <img. tags, if the image without any text is identified, skip it. If around the image tehre's a text, translate text only.
   - Preserve paragraph boundaries and logical content grouping
   - Maintain chronological or numerical sequence where present
   - Take special attention to tables, if there are any. Sometimes 1 row/column can include several rows/columns insidet them, so preseve the exact formatting how it's in the document. MAKE SURE TO ALWAYS CREATE BORDERS BETWEEN CELLS WHEN YOU CREATE TABLES. Just simple tables without any complex styling.
   - If the text is splitted to columns, but there are no borders between the columns, add some borders (full table).
   - DO NOT Include pages count. 
   - Make sure to format lists properly. Each bullet (numbered or not), should be on separate string. Bullets must be simple regardless of how they are presented in the document. Just simple bullets.

2. Formatting Guidelines:
   - Maintain all the styles, including bolden, italic or other types of formatting.
   - Preserve tabular data relationships
   - Maintain proper indentation to show hierarchical relationships
   - Keep contextually related numbers, measurements, or values together with their labels

3. Special Handling:
   - For lists of measurements/values, keep all parameters and their values together
   - For date-based content, ensure dates are formatted consistently as section headers
   - For forms or structured data, preserve the relationship between fields and values
   - For technical/scientific data, maintain the relationship between identifiers and their measurements
   - If it is an instruction/technical documentation/manual with images, make sure to translate text and preserve all the text that will be around images of the object - just create a list for this case.

4. Layout Preservation:
   - Identify when content is presented in columns and preserve column relationships
   - Maintain spacing that indicates logical grouping in the original
   - Preserve the flow of information in a way that maintains readability

5. HTML Considerations:
   - Properly handle tables by maintaining row and column relationships
   - When converting to HTML, use semantic tags to represent the document structure (<h1>, <p>, <ul>, <table>, etc.)
   - Ensure any HTML output is valid and properly nested
   - Make sure text has paragraphs and they are not together, but logically splited with <p> and <br> tags so the text is readable. 
   
Extract the content so it looks like in the initial document as much as possible. The result should be clean, structured text that accurately represents the original document's organization and information hierarchy."""

            # Read image data directly from the file
            with open(img_path, 'rb') as f:
                image_data = f.read()
                
            # Close file handle and ensure garbage collection
            del f
            gc.collect()
            
            logger.info(f"Sending image to Gemini for analysis")
            
            contents = [
                types.Content(
                    role="user",
                    parts=[
                        types.Part.from_text(text=prompt),
                        types.Part.from_bytes(data=image_data, mime_type="image/jpeg"),
                    ],
                ),
            ]
            
            generation_config = types.GenerateContentConfig(
                temperature=0.1,
                response_mime_type="text/plain"
            )
            
            response = self.client.models.generate_content(
                model=self.extraction_model,
                contents=contents,
                config=generation_config
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

    # Fix 4: Enhance the extract_page_content method to better handle empty responses
    async def extract_page_content(self, pdf_bytes: bytes, page_index: int) -> str:
        """Extract content from a PDF page using Google Gemini."""
        if not self.extraction_model:
            logger.error("Google API key not configured for PDF extraction")
            raise TranslationError("Google API key not configured", "CONFIG_ERROR")
        
        start_time = time.time()
        logger.info(f"[EXTRACT DEBUG] Starting extraction for page {page_index + 1}")
        
        # Check cache first
        cache_key = f"extract_page_{page_index}"
        if cache_key in self.extraction_cache:
            logger.info(f"[EXTRACT DEBUG] Cache hit for page {page_index + 1}, returning cached content")
            return self.extraction_cache[cache_key]
        
        # Read PDF in memory without creating a file
        try:
            # Create an in-memory buffer for the PDF content
            buffer = io.BytesIO(pdf_bytes)
            logger.info(f"[EXTRACT DEBUG] Created buffer for page {page_index + 1}")
            
            # Open PDF with PyMuPDF directly from the buffer
            with fitz.open(stream=buffer, filetype="pdf") as doc:
                if page_index >= len(doc):
                    logger.warning(f"Page {page_index + 1} does not exist")
                    return '<div class="page"><p class="text-content">Page does not exist in document.</p></div>'
                
                page = doc[page_index]
                logger.info(f"[EXTRACT DEBUG] Opened page {page_index + 1} in PDF")
                
                # Add page diagnostics to help with debugging
                logger.info(f"Page {page_index + 1} dimensions: {page.rect}, rotation: {page.rotation}")
                
                # Extract text from page for diagnostics
                raw_text = page.get_text()
                
                # Fast path for text-only PDFs - skip Gemini for simple text content
                if raw_text and len(raw_text.strip()) > 100 and not page.get_images():
                    # Check if the text is simple enough to not need Gemini
                    # Simple heuristic: Check if it has tables or complex formatting
                    has_complex_formatting = "│" in raw_text or "┌" in raw_text or "┐" in raw_text or "└" in raw_text or "┘" in raw_text
                    has_tables = raw_text.count("\t") > 5  # Crude check for tables
                    
                    if not has_complex_formatting and not has_tables:
                        logger.info(f"[EXTRACT DEBUG] Fast path: Using direct text extraction for simple page {page_index + 1}")
                        # Format the raw text directly without Gemini
                        from html import escape
                        escaped_text = escape(raw_text)
                        
                        # Format paragraphs
                        paragraphs = [p for p in escaped_text.split('\n\n') if p.strip()]
                        formatted_content = ""
                        for p in paragraphs:
                            formatted_content += f"<p class='text-content'>{p}</p>\n"
                        
                        content = f"<div class='page'>{formatted_content}</div>"
                        self.extraction_cache[cache_key] = content
                        logger.info(f"[EXTRACT DEBUG] Fast path extraction completed for page {page_index + 1} in {time.time() - start_time:.2f} seconds")
                        return content
                
                if raw_text:
                    logger.info(f"Page {page_index + 1} contains {len(raw_text)} chars of raw text")
                    text_sample = raw_text[:100].replace('\n', ' ')
                    logger.info(f"Sample text: {text_sample}...")
                else:
                    logger.warning(f"Page {page_index + 1} contains no extractable text")
                
                # Extract content with Gemini
                logger.info(f"[EXTRACT DEBUG] Calling Gemini for page {page_index + 1}")
                html_content = await self._get_formatted_text_from_gemini_buffer(page)
                logger.info(f"[EXTRACT DEBUG] Received Gemini response for page {page_index + 1}")
                
                # Enhanced empty content check with better fallback
                if not html_content or html_content.strip() == '':
                    logger.error(f"Empty or too short content on page {page_index + 1}")
                    # Try basic text extraction as fallback
                    if raw_text and len(raw_text.strip()) > 0:
                        from html import escape
                        escaped_text = escape(raw_text)
                        paragraphs = [p for p in escaped_text.split('\n\n') if p.strip()]
                        formatted_content = ""
                        for p in paragraphs:
                            formatted_content += f"<p class='text-content'>{p}</p>\n"
                        html_content = f"<div class='page'>{formatted_content}</div>"
                        logger.info(f"Used fallback text extraction for page {page_index + 1}")
                    else:
                        html_content = "<div class='page'><p class='text-content'>This page appears to be empty or contains only images that couldn't be processed.</p></div>"
                        logger.warning(f"Created placeholder for empty page {page_index + 1}")
                
                # Validate structure of content
                if '<div class=' not in html_content:
                    # Ensure content has proper wrapper
                    html_content = f"<div class='page'>{html_content}</div>"
                    logger.info(f"Added missing page wrapper to content")
                
                # Cache the result
                self.extraction_cache[cache_key] = html_content
                
                logger.info(f"Successfully extracted content from page {page_index + 1}, length: {len(html_content)} chars")
                logger.info(f"Page extraction took {time.time() - start_time:.2f} seconds")
                logger.info(f"[EXTRACT DEBUG] Completed extraction for page {page_index + 1}")
                
                return html_content
                
        except Exception as e:
            logger.error(f"Gemini processing error for page {page_index + 1}: {str(e)}")
            # Return a placeholder instead of raising an exception
            return f"<div class='page'><p class='text-content'>Error processing page {page_index + 1}: {str(e)}</p></div>"
        finally:
            # Ensure buffer is closed
            if 'buffer' in locals():
                buffer.close()
            
            # Force garbage collection
            gc.collect()
    
    # Fix 5: Enhance the translate_document_content_sync_wrapper to handle content verification
    # Add this method to better validate and fix content structure before saving
    def _validate_and_fix_content(self, content, title=""):
        """Ensure content has proper structure and non-empty text content"""
        try:
            if not content or len(content.strip()) < 10:
                logger.warning(f"Empty or very short content detected in {title}")
                return f"<div class='page'><p class='text-content'>No processable content was found in this section.</p></div>"
            
            # Check if content has div structure
            if '<div' not in content:
                content = f"<div class='page'>{content}</div>"
                logger.info(f"Added missing page wrapper in {title}")
            
            # Parse with BeautifulSoup to check content
            soup = BeautifulSoup(content, 'html.parser')
            text_content = soup.get_text().strip()
            
            if not text_content:
                logger.warning(f"Content has HTML but no text in {title}")
                return f"<div class='page'><p class='text-content'>Content structure was detected but no readable text was found.</p></div>"
            
            # Ensure the structure is proper
            page_div = soup.find('div', class_='page')
            if not page_div:
                # Wrap all content in a page div
                new_soup = BeautifulSoup('<div class="page"></div>', 'html.parser')
                new_page = new_soup.find('div', class_='page')
                # Move all content into the page div
                for child in list(soup.children):
                    if isinstance(child, str):
                        if child.strip():
                            p = new_soup.new_tag('p', attrs={'class': 'text-content'})
                            p.string = child
                            new_page.append(p)
                    else:
                        new_page.append(child)
                return str(new_soup)
            
            return content
        except Exception as e:
            logger.error(f"Error validating content in {title}: {str(e)}")
            return content  # Return original to avoid further issues

    # Fix 1: Correct the MIME type in _get_formatted_text_from_gemini_buffer method
    async def _get_formatted_text_from_gemini_buffer(self, page):
        """Use Gemini to analyze and extract formatted text with improved memory management"""
        page_index = page.number
        page_start_time = time.time()
        logger.info(f"[GEMINI DEBUG] Starting Gemini extraction for page {page_index + 1}")
        
        # Check cache first
        cache_key = f"extract_{page_index}"
        if cache_key in self.extraction_cache:
            logger.info(f"[GEMINI DEBUG] Cache hit for page {page_index + 1}, returning cached content")
            return self.extraction_cache[cache_key]
        
        # Create a pixmap with improved resolution for better text extraction
        try:
            # Use ultra-low resolution for faster processing
            # 1.0 = screen resolution, lower is faster but less accurate
            pix = page.get_pixmap(alpha=False, matrix=fitz.Matrix(1.0, 1.0))
            logger.info(f"[GEMINI DEBUG] Created pixmap for page {page_index + 1}")

            # Convert pixmap to bytes in memory
            img_bytes = pix.tobytes(output="png")
            logger.info(f"[GEMINI DEBUG] Converted pixmap to bytes, size: {len(img_bytes)} bytes")

            # Ultra concise prompt for faster processing
            prompt = """Extract text from document as clean HTML. Use basic tags (<div>, <p>, <h1-h6>). Preserve structure. Return only HTML with <div class=\"page\"> wrapper."""

            contents = [
                types.Content(
                    role="user",
                    parts=[
                        types.Part.from_text(text=prompt),
                        # Fix: Use correct MIME type for PNG image
                        types.Part.from_bytes(data=img_bytes, mime_type="image/png")
                    ],
                ),
            ]

            generation_config = types.GenerateContentConfig(
                temperature=0.1,
                response_mime_type="text/plain",
                max_output_tokens=4096  # Limit token count for faster response
            )

            logger.info(f"[GEMINI DEBUG] Sending request to Gemini API for page {page_index + 1}")

            # Set up a timeout for the API call using asyncio
            api_call_task = self.client.models.generate_content(
                model=self.extraction_model,
                contents=contents,
                config=generation_config
            )

            # Add timeout
            try:
                response = await asyncio.wait_for(api_call_task, timeout=self.api_timeout)
                logger.info(f"[GEMINI DEBUG] Received response from Gemini API for page {page_index + 1}")
            except asyncio.TimeoutError:
                logger.error(f"[GEMINI DEBUG] Gemini API request timed out after {self.api_timeout}s for page {page_index + 1}")
                raise TranslationError(f"Gemini API request timed out after {self.api_timeout}s", "TIMEOUT")
            except Exception as api_error:
                # Check specifically for rate limit errors
                error_str = str(api_error)
                if "429" in error_str or "rate limit" in error_str.lower() or "quota" in error_str.lower():
                    logger.error(f"[GEMINI DEBUG] RATE LIMIT DETECTED for page {page_index + 1}: {error_str}")
                    # Wait a bit longer before retrying on rate limits
                    time.sleep(5)
                    raise TranslationError(f"Gemini API rate limit exceeded: {error_str}", "RATE_LIMIT")
                else:
                    logger.error(f"[GEMINI DEBUG] API error for page {page_index + 1}: {error_str}")
                    raise

            # Check if the response has text - handle empty responses
            if not hasattr(response, 'text') or not response.text:
                logger.error(f"[GEMINI DEBUG] Empty response from Gemini API for page {page_index + 1}")
                raise TranslationError("Empty response from Gemini API", "EMPTY_RESPONSE")

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
            logger.info(f"[GEMINI DEBUG] Processed HTML content for page {page_index + 1}")

            # Enhanced validation to ensure we have actual content
            text_content = re.sub(r'<[^>]+>', '', html_content).strip()
            if len(html_content) < 50 or '<' not in html_content or not text_content:
                logger.error(f"[GEMINI DEBUG] Invalid or insufficient content extracted from page {page_index + 1}")
                # Fall back to simpler extraction but don't return empty
                text = page.get_text()
                if text and len(text.strip()) > 0:
                    # Fix 2: Properly escape HTML in text extraction fallback
                    from html import escape
                    escaped_text = escape(text)
                    paragraphs = [p for p in escaped_text.split('\n\n') if p.strip()]
                    formatted_content = ""
                    for p in paragraphs:
                        formatted_content += f"<p class='text-content'>{p}</p>\n"
                    return f"<div class='page'>{formatted_content}</div>"
                else:
                    # If truly empty, create a placeholder saying so
                    logger.warning(f"[GEMINI DEBUG] Using empty page placeholder for page {page_index + 1}")
                    return "<div class='page'><p class='text-content'>This page appears to be empty or contains only images that couldn't be processed.</p></div>"

            logger.info(f"Successfully extracted content from page {page_index + 1}, length: {len(html_content)} chars")
            # Cache the result
            self.extraction_cache[cache_key] = html_content
            return html_content

        except Exception as e:
            logger.error(f"[GEMINI DEBUG] Error in Gemini processing for page {page_index + 1}: {e}")
            # Fix 3: Improved fallback logic for text extraction
            try:
                text = page.get_text()
                logger.warning(f"[GEMINI DEBUG] Falling back to plain text extraction for page {page_index + 1} ({len(text)} chars)")
                # Better handling of fallback text
                if text and len(text.strip()) > 0:
                    from html import escape
                    escaped_text = escape(text)
                    # Split text into paragraphs and preserve structure
                    paragraphs = [p for p in escaped_text.split('\n\n') if p.strip()]
                    formatted_content = ""
                    for p in paragraphs:
                        formatted_content += f"<p class='text-content'>{p}</p>\n"
                    return f"<div class='page'>{formatted_content}</div>"
                else:
                    # Create meaningful placeholder for empty pages
                    return "<div class='page'><p class='text-content'>This page appears to be empty or contains only images that couldn't be processed.</p></div>"
            except Exception as text_error:
                logger.error(f"[GEMINI DEBUG] Fallback text extraction also failed: {text_error}")
                return "<div class='page'><p class='text-content'>Error processing this page: couldn't extract content.</p></div>"
        finally:
            # Clean up resources
            try:
                if 'pix' in locals():
                    del pix
                if 'img_bytes' in locals():
                    del img_bytes
                # Force garbage collection
                gc.collect()
                logger.debug(f"[GEMINI DEBUG] Resources cleaned up for page {page_index + 1}")
            except Exception as cleanup_error:
                logger.warning(f"[GEMINI DEBUG] Error during cleanup for page {page_index + 1}: {str(cleanup_error)}")
            logger.info(f"[GEMINI DEBUG] Total processing time for page {page_index + 1}: {time.time() - page_start_time:.2f} seconds")

    async def _get_formatted_text_from_gemini(self, page):
        """Legacy method - retained for backward compatibility"""
        return await self._get_formatted_text_from_gemini_buffer(page)
    
    async def translate_chunk(self, html_content: str, from_lang: str, to_lang: str, retries: int = 3, chunk_id: str = None) -> str:
        """
        Translate a chunk of HTML content to the target language.
        Enhanced version that handles all languages consistently with special attention to 
        prevention of placeholder issues and proper preservation of content.
        """
        if not self.client:
            logger.error("Google API key not configured for translation")
            raise TranslationError("Google API key not configured", "CONFIG_ERROR")
        
        if not chunk_id:
            chunk_id = f"{hashlib.md5(html_content.encode()).hexdigest()}"[:7]
                
        # Check cache first
        cache_key = f"translate_{chunk_id}_{from_lang}_{to_lang}"
        if cache_key in self.translation_cache:
            logger.info(f"Cache hit for chunk {chunk_id}, returning cached translation")
            return self.translation_cache[cache_key]
        
        start_time = time.time()
        
        # Get the proper language display name (for better prompting)
        to_lang_display = self.get_language_display_name(to_lang)
        logger.info(f"Starting translation of chunk {chunk_id} ({len(html_content)} chars) to {to_lang_display}")
        
        # Skip translation for very short content (likely just formatting)
        if len(html_content) < 50 or html_content.count('<') > len(html_content) / 4:
            logger.info(f"Skipping translation for tiny or format-only chunk {chunk_id}")
            self.translation_cache[cache_key] = html_content
            return html_content
            
        # Quick check if there's any actual text to translate
        text_content = re.sub(r'<[^>]+>', '', html_content).strip()
        if not text_content or len(text_content) < 20:
            logger.info(f"Chunk {chunk_id} has insufficient text content to translate, returning original")
            self.translation_cache[cache_key] = html_content
            return html_content
        
        # Save original HTML for comparison and fallback
        original_html = html_content
        
        # Get language-specific configuration
        lang_config = self.get_language_config(to_lang)
        logger.info(f"Using configuration for {to_lang_display}: {lang_config}")
        
        # Tag content that should not be translated
        html_content_with_tags = self.tag_untranslatable_content(html_content)
        
        last_error = None
        
        for attempt in range(1, retries + 1):
            try:
                # Log chunk content for debugging (only on first attempt)
                if attempt == 1:
                    chunk_preview_start = html_content_with_tags[:200].replace('\n', ' ')
                    chunk_preview_end = html_content_with_tags[-200:].replace('\n', ' ') if len(html_content_with_tags) > 200 else ''
                    logger.info(f"Chunk {chunk_id} content preview (start): {chunk_preview_start} ...")
                    if chunk_preview_end:
                        logger.info(f"Chunk {chunk_id} content preview (end): ... {chunk_preview_end}")
                    logger.info(f"Chunk {chunk_id} content length: {len(html_content_with_tags)} chars")
                
                # Create a very concise prompt for translation
                prompt = f"""Translate HTML to {to_lang_display}. Keep all tags. Translate ONLY text content.

HTML to translate:

{html_content_with_tags}
"""

                logger.info(f"Sending chunk {chunk_id} to Gemini for translation (attempt {attempt}/{retries})")
                translation_start = time.time()
                
                # Use configuration parameters
                contents = [
                    types.Content(
                        role="user",
                        parts=[types.Part.from_text(text=prompt)]
                    )
                ]

                generation_config = types.GenerateContentConfig(
                    temperature=lang_config.get("temperature", 0),  # Use 0 temperature for deterministic output
                    top_p=lang_config.get("top_p", 0.97),
                    top_k=lang_config.get("top_k", 45),
                    max_output_tokens=lang_config.get("max_output_tokens", 8192),
                    response_mime_type="text/plain"
                )

                # Set up a timeout for the API call using asyncio
                api_call_task = self.client.models.generate_content(
                    model=self.translation_model,
                    contents=contents,
                    config=generation_config
                )
                
                # Add timeout
                try:
                    response = await asyncio.wait_for(api_call_task, timeout=self.api_timeout)
                    logger.info(f"Gemini completed translation for chunk {chunk_id} in {time.time() - translation_start:.2f} seconds")
                except asyncio.TimeoutError:
                    logger.error(f"Gemini API request timed out after {self.api_timeout}s for chunk {chunk_id}")
                    raise TranslationError(f"Gemini API request timed out after {self.api_timeout}s", "TIMEOUT")
                except Exception as api_error:
                    # Check specifically for rate limit errors
                    error_str = str(api_error)
                    if "429" in error_str or "rate limit" in error_str.lower() or "quota" in error_str.lower():
                        logger.error(f"RATE LIMIT DETECTED for chunk {chunk_id}: {error_str}")
                        # Wait before retrying on rate limits
                        time.sleep(5)
                        raise TranslationError(f"Gemini API rate limit exceeded: {error_str}", "RATE_LIMIT")
                    else:
                        logger.error(f"API error for chunk {chunk_id}: {error_str}")
                        raise
                
                # Check if the response has text
                if not hasattr(response, 'text') or not response.text:
                    logger.error(f"Empty response from Gemini API for chunk {chunk_id}")
                    raise TranslationError("Empty response from Gemini API", "EMPTY_RESPONSE")
                
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
                
                # Check for placeholder issues in the translated text
                has_placeholders = False
                placeholder_count = 0
                for pattern in self.placeholder_patterns:
                    matches = re.findall(pattern, translated_text)
                    if matches:
                        placeholder_count += len(matches)
                        placeholder_samples = matches[:5]  # Show up to 5 examples
                        logger.error(f"Found {len(matches)} placeholders matching {pattern}: {placeholder_samples}")
                        has_placeholders = True
                
                # If we have placeholders, try to fix them or retry
                if has_placeholders and placeholder_count > 5:
                    logger.error(f"Detected {placeholder_count} placeholder issues in translation")
                    if attempt < retries:
                        logger.info(f"Will retry with modified prompt")
                        raise TranslationError("Placeholder issues detected", "TRANSLATION_ERROR")
                    else:
                        # On last attempt, try to clean up placeholders
                        logger.warning(f"Final attempt: trying to fix placeholder issues in translation")
                        
                        # Try to fix placeholders by passing through a cleanup step
                        fixed_text = self.fix_placeholder_issues(translated_text, original_html)
                        if fixed_text != translated_text:
                            logger.info(f"Applied placeholder fixes to translation")
                            translated_text = fixed_text
                
                # Clean up all preservation tags thoroughly
                translated_text = self.clean_preservation_tags(translated_text)
                
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
                
                # Final check to make sure no preservation markers remain
                if "<!--PRESERVE-->" in translated_text or "<!--/PRESERVE-->" in translated_text:
                    logger.warning("Some preservation markers remain, applying final cleanup")
                    translated_text = re.sub(r'<!--PRESERVE-->|<!--/PRESERVE-->', '', translated_text)
                
                # Final debug check for placeholders
                if any(re.search(pattern, translated_text) for pattern in self.placeholder_patterns):
                    logger.warning("Placeholders still exist in final output")
                else:
                    logger.info("No placeholders detected in final output")
                
                logger.info(f"Successfully translated chunk {chunk_id}, length: {len(translated_text)} chars")
                logger.info(f"Translation took {time.time() - start_time:.2f} seconds")
                
                # Cache the successful translation
                self.translation_cache[cache_key] = translated_text
                
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
    
    def split_content_into_chunks(self, content: str, max_size: int, to_lang: str = None) -> List[str]:
        """
        Split content into chunks of maximum size while preserving HTML structure.
        Enhanced with detailed chunk boundary logging and language-specific sizing.
        """
        # Adjust chunk size based on language if specified
        if to_lang:
            orig_max_size = max_size
            max_size = self.get_max_chunk_size(to_lang)
            if max_size != orig_max_size:
                logger.info(f"Adjusted chunk size for {to_lang}: {orig_max_size} → {max_size}")
        
        logger.info(f"Splitting content of length {len(content)} into chunks (max size: {max_size})")
        
        # If content is small enough, return it as a single chunk
        if len(content) <= max_size:
            logger.info("Content fits in a single chunk, no splitting needed")
            return [content]
        
        try:
            # Use BeautifulSoup to parse the HTML
            soup = BeautifulSoup(content, 'html.parser')
            
            # Log the full document structure for debugging
            document_structure = []
            for element in soup.find_all(['div', 'h1', 'h2', 'h3', 'p', 'table']):
                if element.name and element.get('class'):
                    document_structure.append(f"{element.name}.{'.'.join(element.get('class'))} - {element.get_text()[:30]}...")
                elif element.name:
                    document_structure.append(f"{element.name} - {element.get_text()[:30]}...")
                    
            logger.info(f"Document structure overview: {' > '.join(document_structure[:10])}...")
            
            # Check if we have a document/page structure
            has_document_structure = bool(soup.find('div', class_='document'))
            
            # Find all elements to split by (either pages or top-level elements)
            pages = soup.find_all('div', class_='page')
            
            if pages:
                logger.info(f"Found {len(pages)} page divs to split by")
                
                # Log each page's starting content for debugging
                for i, page in enumerate(pages):
                    page_text = page.get_text()[:100].replace('\n', ' ')
                    logger.info(f"Page {i+1} starts with: {page_text}...")
                
                # Force splitting large pages into smaller chunks
                chunks = []
                for page_index, page in enumerate(pages):
                    page_html = str(page)
                    
                    # If page is too large, split it further
                    if len(page_html) > max_size:
                        logger.info(f"Page {page_index+1} is too large ({len(page_html)} chars), splitting further")
                        
                        # Get all direct children of the page
                        page_soup = BeautifulSoup(page_html, 'html.parser')
                        page_elements = list(page_soup.children)
                        
                        # Filter to keep only tag elements and non-empty strings
                        page_elements = [el for el in page_elements if el.name or (isinstance(el, str) and el.strip())]
                        
                        if len(page_elements) > 1:
                            # Split page elements into chunks based on size
                            current_chunk = ""
                            current_elements = []
                            for i, element in enumerate(page_elements):
                                element_str = str(element)
                                # If adding this element would exceed max size, start a new chunk
                                if len(current_chunk) + len(element_str) > max_size and current_chunk:
                                    # Wrap the chunk in proper structure
                                    page_chunk = f'<div class="page">{current_chunk}</div>'
                                    if has_document_structure:
                                        chunk = f'<div class="document">{page_chunk}</div>'
                                    else:
                                        chunk = page_chunk
                                    logger.info(f"Created sub-chunk for page {page_index+1}, size: {len(chunk)} chars")
                                    chunks.append(chunk)
                                    current_chunk = element_str
                                    current_elements = [element]
                                else:
                                    current_chunk += element_str
                                    current_elements.append(element)
                            # Add the last chunk if it has content
                            if current_chunk:
                                page_chunk = f'<div class="page">{current_chunk}</div>'
                                if has_document_structure:
                                    chunk = f'<div class="document">{page_chunk}</div>'
                                else:
                                    chunk = page_chunk
                                logger.info(f"Created final sub-chunk for page {page_index+1}, size: {len(chunk)} chars")
                                chunks.append(chunk)
                        else:
                            # If the page can't be split by elements, use regex method
                            logger.info(f"Page {page_index+1} has only one element, using regex splitting")
                            # Get the inner content of the page div
                            inner_content = page_soup.decode_contents()
                            # Try to split at paragraph or div boundaries within the content
                            inner_parts = re.split(r'(</p>|</div>)', str(inner_content))
                            current_chunk = ""
                            for part_index in range(0, len(inner_parts), 2):
                                part = inner_parts[part_index]
                                # Add the closing tag if it exists
                                if part_index+1 < len(inner_parts):
                                    part += inner_parts[part_index+1]
                                # If adding this part would exceed max size, start a new chunk
                                if len(current_chunk) + len(part) > max_size and current_chunk:
                                    page_chunk = f'<div class="page">{current_chunk}</div>'
                                    if has_document_structure:
                                        chunk = f'<div class="document">{page_chunk}</div>'
                                    else:
                                        chunk = page_chunk
                                    logger.info(f"Created regex-based sub-chunk for page {page_index+1}")
                                    chunks.append(chunk)
                                    current_chunk = part
                                else:
                                    current_chunk += part
                            # Add the last chunk if it has content
                            if current_chunk:
                                page_chunk = f'<div class="page">{current_chunk}</div>'
                                if has_document_structure:
                                    chunk = f'<div class="document">{page_chunk}</div>'
                                else:
                                    chunk = page_chunk
                                logger.info(f"Created final regex-based sub-chunk for page {page_index+1}")
                                chunks.append(chunk)
                    else:
                        # If page is small enough, use it as is
                        if has_document_structure:
                            chunk = f'<div class="document">{page_html}</div>'
                        else:
                            chunk = page_html
                        
                        logger.info(f"Using page {page_index+1} as a single chunk")
                        chunks.append(chunk)
                
                logger.info(f"Split content into {len(chunks)} chunks with page-aware splitting")
                
                # Verify chunk continuity
                if len(chunks) > 1:
                    for i in range(1, len(chunks)):
                        prev_chunk = BeautifulSoup(chunks[i-1], 'html.parser').get_text()
                        curr_chunk = BeautifulSoup(chunks[i], 'html.parser').get_text()
                        
                        prev_end = prev_chunk[-50:].replace('\n', ' ').strip()
                        curr_start = curr_chunk[:50].replace('\n', ' ').strip()
                        
                        logger.info(f"Continuity check between chunks {i} and {i+1}:")
                        logger.info(f"  Chunk {i} ends with: ...{prev_end}")
                        logger.info(f"  Chunk {i+1} starts with: {curr_start}...")
                
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
                
                # Log sample of elements for debugging
                for i, element in enumerate(elements[:5]):
                    if i < 5:  # Log just the first 5 elements for brevity
                        element_text = str(element)[:100].replace('\n', ' ')
                        logger.info(f"Element {i+1} sample: {element_text}...")
                
                # Create chunks based on these elements
                chunks = []
                current_chunk = ""
                current_elements = []
                
                for i, element in enumerate(elements):
                    # Convert element to string
                    element_str = str(element)
                    
                    # If adding this element would exceed max size, start a new chunk
                    if len(current_chunk) + len(element_str) > max_size and current_chunk:
                        # Wrap in appropriate structure
                        if has_document_structure:
                            chunk = f'<div class="document"><div class="page">{current_chunk}</div></div>'
                        else:
                            chunk = f'<div class="page">{current_chunk}</div>'
                        
                        # Log chunk details for debugging
                        logger.info(f"Created chunk with elements {i-len(current_elements)}-{i-1}")
                        
                        # Log the start and end of the chunk content
                        soup_chunk = BeautifulSoup(chunk, 'html.parser')
                        chunk_text = soup_chunk.get_text()
                        chunk_start = chunk_text[:100].replace('\n', ' ')
                        chunk_end = chunk_text[-100:].replace('\n', ' ')
                        logger.info(f"Chunk starts with: {chunk_start}...")
                        logger.info(f"Chunk ends with: ...{chunk_end}")
                        
                        chunks.append(chunk)
                        
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
                    
                    # Log final chunk details for debugging
                    first_element_idx = len(elements) - len(current_elements)
                    last_element_idx = len(elements) - 1
                    logger.info(f"Created final chunk with elements {first_element_idx+1}-{last_element_idx+1}")
                    
                    # Log the start and end of the chunk content
                    soup_chunk = BeautifulSoup(chunk, 'html.parser')
                    chunk_text = soup_chunk.get_text()
                    chunk_start = chunk_text[:100].replace('\n', ' ')
                    chunk_end = chunk_text[-100:].replace('\n', ' ')
                    logger.info(f"Final chunk starts with: {chunk_start}...")
                    logger.info(f"Final chunk ends with: ...{chunk_end}")
                    
                    chunks.append(chunk)
                
                logger.info(f"Split content into {len(chunks)} chunks by elements")
                
                # Verify chunk continuity
                if len(chunks) > 1:
                    for i in range(1, len(chunks)):
                        prev_chunk = BeautifulSoup(chunks[i-1], 'html.parser').get_text()
                        curr_chunk = BeautifulSoup(chunks[i], 'html.parser').get_text()
                        
                        prev_end = prev_chunk[-50:].replace('\n', ' ').strip()
                        curr_start = curr_chunk[:50].replace('\n', ' ').strip()
                        
                        logger.info(f"Continuity check between chunks {i} and {i+1}:")
                        logger.info(f"  Chunk {i} ends with: ...{prev_end}")
                        logger.info(f"  Chunk {i+1} starts with: {curr_start}...")
                
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
                    
                    for i, page in enumerate(page_divs):
                        # Log page content sample for debugging
                        page_text = re.sub(r'<[^>]+>', '', page[:200]).replace('\n', ' ')
                        logger.info(f"Page {i+1} content sample: {page_text[:100]}...")
                        
                        if len(current_chunk) + len(page) > max_size and current_chunk:
                            if has_document_structure:
                                chunk = f'<div class="document">{current_chunk}</div>'
                            else:
                                chunk = current_chunk
                            
                            # Log chunk details for debugging
                            logger.info(f"Created fallback chunk with pages {i-len(current_pages)}-{i-1}")
                            chunks.append(chunk)
                            
                            # Log chunk content
                            chunk_text = re.sub(r'<[^>]+>', '', chunk[:200]).replace('\n', ' ')
                            logger.info(f"Chunk content sample: {chunk_text[:100]}...")
                            
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
                        
                        # Log final chunk details for debugging
                        logger.info(f"Created fallback final chunk with {len(current_pages)} pages")
                        chunks.append(chunk)
                        
                        # Log chunk content
                        chunk_text = re.sub(r'<[^>]+>', '', chunk[:200]).replace('\n', ' ')
                        logger.info(f"Final chunk content sample: {chunk_text[:100]}...")
                    
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
                
                # Log part content sample for debugging
                part_text = re.sub(r'<[^>]+>', '', part[:200]).replace('\n', ' ')
                if i < 10 or i > len(parts) - 10:  # Log first 10 and last 10 parts
                    logger.info(f"Part {i//2+1} content sample: {part_text[:100]}...")
                    
                if len(current_chunk) + len(part) > max_size and current_chunk:
                    # Make sure we have valid HTML with appropriate structure
                    if not current_chunk.startswith('<div'):
                        if has_document_structure:
                            current_chunk = f'<div class="document"><div class="page">{current_chunk}</div></div>'
                        else:
                            current_chunk = f'<div class="page">{current_chunk}</div>'
                    
                    # Log chunk details for debugging
                    logger.info(f"Created fallback chunk at part boundary {i//2}")
                    chunks.append(current_chunk)
                    
                    # Log chunk content
                    chunk_text = re.sub(r'<[^>]+>', '', current_chunk[:200]).replace('\n', ' ')
                    logger.info(f"Chunk content sample: {chunk_text[:100]}...")
                    
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
                
                # Log final chunk details for debugging
                logger.info(f"Created final fallback chunk")
                chunks.append(current_chunk)
                
                # Log chunk content
                chunk_text = re.sub(r'<[^>]+>', '', current_chunk[:200]).replace('\n', ' ')
                logger.info(f"Final fallback chunk content sample: {chunk_text[:100]}...")
            
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
                    logger.info(f"Created fixed-size chunk {i//max_size+1}, size: {len(chunk)}")
            
            logger.info(f"Split content into {len(chunks)} chunks using fallback method")
            return chunks
    
    def combine_html_content(self, html_contents):
        """
        Combine multiple HTML contents into a single document.
        Enhanced with detailed logging to verify chunk continuity.
        """
        if not html_contents:
            logger.warning("No HTML contents to combine")
            return ""
            
        logger.info(f"Combining {len(html_contents)} HTML content pieces into a single document")
        
        # First, check if we need document/page structure
        first_chunk = html_contents[0] if html_contents else ""
        needs_document_wrapper = not ('<div class="document"' in first_chunk or "<div class='document'" in first_chunk)
        needs_page_wrapper = not ('<div class="page"' in first_chunk or "<div class='page'" in first_chunk)
        
        # Log chunk information for debugging
        for i, content in enumerate(html_contents):
            # Extract some text from the beginning and end of each chunk
            text_content = re.sub(r'<[^>]+>', ' ', content)
            text_content = re.sub(r'\s+', ' ', text_content).strip()
            content_start = text_content[:100]
            content_end = text_content[-100:] if len(text_content) > 100 else text_content
            
            logger.info(f"Chunk {i+1} of {len(html_contents)}:")
            logger.info(f"  Length: {len(content)} chars")
            logger.info(f"  Starts with: {content_start}...")
            logger.info(f"  Ends with: ...{content_end}")
            
            # Check for document/page structure
            has_doc = '<div class="document"' in content or "<div class='document'" in content
            has_page = '<div class="page"' in content or "<div class='page'" in content
            logger.info(f"  Has document structure: {has_doc}, Has page structure: {has_page}")
        
        # Check for continuity between chunks
        if len(html_contents) > 1:
            logger.info("Checking continuity between chunks:")
            for i in range(len(html_contents) - 1):
                # Extract text from end of current chunk and beginning of next chunk
                current_chunk_text = re.sub(r'<[^>]+>', ' ', html_contents[i])
                current_chunk_text = re.sub(r'\s+', ' ', current_chunk_text).strip()
                current_chunk_end = current_chunk_text[-50:] if len(current_chunk_text) > 50 else current_chunk_text
                
                next_chunk_text = re.sub(r'<[^>]+>', ' ', html_contents[i+1])
                next_chunk_text = re.sub(r'\s+', ' ', next_chunk_text).strip()
                next_chunk_start = next_chunk_text[:50] if len(next_chunk_text) > 50 else next_chunk_text
                
                logger.info(f"  Between chunks {i+1} and {i+2}:")
                logger.info(f"    Chunk {i+1} ends with: ...{current_chunk_end}")
                logger.info(f"    Chunk {i+2} starts with: {next_chunk_start}...")
        
        try:
            # Use BeautifulSoup for robust HTML parsing
            combined_html = ""
            page_contents = []
            
            for i, content in enumerate(html_contents):
                logger.info(f"Processing chunk {i+1} for combination")
                
                # Clean up the content
                content = re.sub(r'<!DOCTYPE[^>]*>', '', content, flags=re.IGNORECASE)
                content = re.sub(r'</?html[^>]*>', '', content, flags=re.IGNORECASE)
                content = re.sub(r'</?head[^>]*>', '', content, flags=re.IGNORECASE)
                content = re.sub(r'</?body[^>]*>', '', content, flags=re.IGNORECASE)
                
                # Make sure no preservation tags remain
                content = self.clean_preservation_tags(content)
                
                # Parse with BeautifulSoup
                soup = BeautifulSoup(content, 'html.parser')
                
                # Extract the relevant content
                extracted_content = ""
                if soup.find('div', class_='document'):
                    logger.info(f"  Chunk {i+1} has document structure")
                    # If this chunk has a document structure, extract the pages
                    pages = soup.find_all('div', class_='page')
                    if pages:
                        logger.info(f"  Found {len(pages)} pages in chunk {i+1}")
                        for j, page in enumerate(pages):
                            page_html = str(page)
                            page_contents.append(page_html)
                            
                            # Log page content sample
                            page_text = page.get_text()[:100].replace('\n', ' ')
                            logger.info(f"  Page {j+1} from chunk {i+1} starts with: {page_text}...")
                    else:
                        # If no pages but has document, extract the document content
                        # If no pages but has document, extract the document content
                        logger.info(f"  No pages found in document div of chunk {i+1}, extracting all content")
                        doc_div = soup.find('div', class_='document')
                        doc_content = str(doc_div.decode_contents())
                        wrapped_content = f'<div class="page">{doc_content}</div>'
                        page_contents.append(wrapped_content)
                        
                        # Log content sample
                        content_text = doc_div.get_text()[:100].replace('\n', ' ')
                        logger.info(f"  Document content starts with: {content_text}...")
                elif soup.find('div', class_='page'):
                    logger.info(f"  Chunk {i+1} has page structure without document wrapper")
                    # If this chunk has pages but no document, add the pages
                    pages = soup.find_all('div', class_='page')
                    logger.info(f"  Found {len(pages)} pages in chunk {i+1}")
                    for j, page in enumerate(pages):
                        page_html = str(page)
                        page_contents.append(page_html)
                        
                        # Log page content sample
                        page_text = page.get_text()[:100].replace('\n', ' ')
                        logger.info(f"  Page {j+1} from chunk {i+1} starts with: {page_text}...")
                else:
                    logger.info(f"  Chunk {i+1} has no document or page structure, wrapping all content")
                    # No document or page structure, wrap everything in a page
                    wrapped_content = f'<div class="page">{str(soup)}</div>'
                    page_contents.append(wrapped_content)
                    
                    # Log content sample
                    content_text = soup.get_text()[:100].replace('\n', ' ')
                    logger.info(f"  Wrapped content starts with: {content_text}...")
            
            # Now build the combined HTML
            logger.info(f"Combining {len(page_contents)} total pages into final document")
            if needs_document_wrapper:
                combined_html = f'<div class="document">\n{"".join(page_contents)}\n</div>'
                logger.info("Added document wrapper to combined content")
            else:
                combined_html = "".join(page_contents)
                logger.info("No document wrapper needed for combined content")
            
            # Check for and clean any remaining preservation tags
            combined_html = self.clean_preservation_tags(combined_html)
            
            # Log the final document structure
            soup = BeautifulSoup(combined_html, 'html.parser')
            pages_in_final = soup.find_all('div', class_='page')
            logger.info(f"Final document has {len(pages_in_final)} pages")
            
            # Log the start and end of the combined content
            final_text = soup.get_text()
            final_start = final_text[:100].replace('\n', ' ')
            final_end = final_text[-100:].replace('\n', ' ')
            logger.info(f"Final document starts with: {final_start}...")
            logger.info(f"Final document ends with: ...{final_end}")
            
            # Check for potential content loss by comparing combined size with sum of chunks
            total_chunks_size = sum(len(chunk) for chunk in html_contents)
            logger.info(f"Total size of all chunks: {total_chunks_size}, Combined document size: {len(combined_html)}")
            if len(combined_html) < total_chunks_size * 0.8:  # If we lost more than 20% of content
                logger.warning(f"Possible content loss during combination! Combined size is {len(combined_html)/(total_chunks_size)*100:.2f}% of total chunks size")
            
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
                logger.info("Adding document wrapper in fallback method")
            
            for i, content in enumerate(html_contents):
                logger.info(f"Processing chunk {i+1} in fallback combining")
                
                # Clean up the content
                content = re.sub(r'<!DOCTYPE[^>]*>', '', content, flags=re.IGNORECASE)
                content = re.sub(r'</?html[^>]*>', '', content, flags=re.IGNORECASE)
                content = re.sub(r'</?head[^>]*>', '', content, flags=re.IGNORECASE)
                content = re.sub(r'</?body[^>]*>', '', content, flags=re.IGNORECASE)
                
                # Clean preservation tags
                content = self.clean_preservation_tags(content)
                
                # Remove document wrapper if present and we're adding our own
                if needs_document:
                    content = re.sub(r'<div class=["\'](document|chunk)["\'][^>]*>(.*?)</div>', r'\2', content, flags=re.DOTALL|re.IGNORECASE)
                    logger.info(f"  Removed existing document wrapper from chunk {i+1}")
                
                # Add page wrapper if needed
                if needs_page and not ('<div class="page"' in content or "<div class='page'" in content):
                    content = f'<div class="page">\n{content}\n</div>'
                    logger.info(f"  Added page wrapper to chunk {i+1}")
                    
                combined += content + '\n'
                
                # Log content sample added to combined result
                content_text = re.sub(r'<[^>]+>', ' ', content)
                content_text = re.sub(r'\s+', ' ', content_text).strip()
                content_sample = content_text[:100]
                logger.info(f"  Added content starting with: {content_sample}...")
            
            if needs_document:
                combined += '</div>'
            
            # One final cleanup for any remaining preservation tags
            combined = self.clean_preservation_tags(combined)
                
            logger.info(f"Combined chunks into document of {len(combined)} chars using basic approach")
            return combined

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
        
        # Add detailed debugging to track initialization
        logger.info(f"[TRANSLATE DEBUG] Starting translation process for {process_id}")
        
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
            logger.info(f"[TRANSLATE DEBUG] Found progress record for user {user_id}")
        except Exception as e:
            logger.error(f"[TRANSLATE] Failed to get translation record: {str(e)}")
            user_id = None
            
        try:
            # Handle PDFs
            if file_type in settings.SUPPORTED_DOC_TYPES and 'pdf' in file_type:
                # Get PDF page count
                logger.info(f"[TRANSLATE DEBUG] Opening PDF stream")
                buffer = io.BytesIO(file_content)
                with fitz.open(stream=buffer, filetype="pdf") as doc:
                    total_pages = len(doc)
                    
                    # Quick check if this is a text-only PDF that can use fast path
                    is_simple_pdf = True
                    has_images = False
                    for pg_idx in range(min(3, total_pages)):  # Check just first 3 pages
                        if doc[pg_idx].get_images():
                            has_images = True
                            is_simple_pdf = False
                            break
                
                logger.info(f"[TRANSLATE] PDF has {total_pages} pages for {process_id}")
                logger.info(f"[TRANSLATE DEBUG] PDF opened successfully, has_images={has_images}")
                
                # Update total pages
                if progress:
                    progress.totalPages = total_pages
                    db.commit()
                    logger.info(f"[TRANSLATE DEBUG] Updated total pages in database")
                
                # Process pages in parallel batches
                batch_size = 12  # Process 12 pages at a time (increased from 8)
                logger.info(f"[TRANSLATE DEBUG] Starting batch processing with batch_size={batch_size}")
                
                # Process all batches in one operation with a semaphore for rate limiting
                semaphore = asyncio.Semaphore(20)  # Limit concurrent API calls
                
                async def process_page(page_index):
                    current_page = page_index + 1
                    logger.info(f"[TRANSLATE DEBUG] Processing page {current_page}")
                    # Update progress
                    if progress:
                        progress.currentPage = current_page
                        progress.progress = int((current_page / total_pages) * 100)
                        db.commit()
                    # Extract content (with rate limiting)
                    async with semaphore:
                        html_content = await self.extract_page_content(file_content, page_index)
                    if html_content and len(html_content.strip()) > 0:
                        logger.info(f"[TRANSLATE] Extracted {len(html_content)} chars from page {current_page}")
                        # Translate content
                        translated_content = None
                        # Split content if needed - use language-specific chunking
                        max_chunk_size = self.get_max_chunk_size(to_lang)
                        if len(html_content) > max_chunk_size * 1.2:  # Add 20% buffer
                            chunks = self.split_content_into_chunks(html_content, max_chunk_size, to_lang)
                            logger.info(f"[TRANSLATE] Split into {len(chunks)} chunks for {to_lang} translation")
                            # Process all chunks in parallel with semaphore for rate limiting
                            async def translate_chunk_with_limit(chunk, chunk_num):
                                chunk_id = f"{process_id}-p{current_page}-c{chunk_num+1}"
                                logger.info(f"[TRANSLATE] Translating chunk {chunk_num+1}/{len(chunks)} (parallel)")
                                async with semaphore:
                                    try:
                                        return await self.translate_chunk(chunk, from_lang, to_lang, retries=2, chunk_id=chunk_id)
                                    except Exception as e:
                                        logger.error(f"[TRANSLATE] Error translating chunk {chunk_num+1}: {str(e)}")
                                        return f"<div class='error'>Translation error in section {chunk_num+1}: {str(e)}</div>"
                            # Create tasks for all chunks
                            translation_tasks = [translate_chunk_with_limit(chunk, i) for i, chunk in enumerate(chunks)]
                            translated_chunks = await asyncio.gather(*translation_tasks)
                            translated_content = self.combine_html_content(translated_chunks)
                        else:
                            chunk_id = f"{process_id}-p{current_page}"
                            try:
                                async with semaphore:
                                    translated_content = await self.translate_chunk(
                                        html_content, from_lang, to_lang, retries=2, chunk_id=chunk_id
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
                        return page_index
                    else:
                        logger.warning(f"[TRANSLATE] No content extracted from page {current_page}")
                        return None
                
                # Process all pages
                all_page_tasks = [process_page(i) for i in range(total_pages)]
                processed_pages = await asyncio.gather(*all_page_tasks)
                
                # Filter out None values
                translated_pages = [p for p in processed_pages if p is not None]

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
                        
                        # Split content if needed - use language-specific chunking
                        max_chunk_size = self.get_max_chunk_size(to_lang)
                        if len(html_content) > max_chunk_size * 1.2:  # Add 20% buffer
                            chunks = self.split_content_into_chunks(html_content, max_chunk_size, to_lang)
                            logger.info(f"[TRANSLATE] Split into {len(chunks)} chunks for {to_lang} translation")
                            
                            translated_chunks = []
                            chunk_tasks = []
                            for i, chunk in enumerate(chunks):
                                chunk_id = f"{process_id}-doc-c{i+1}"
                                logger.info(f"[TRANSLATE] Translating chunk {i+1}/{len(chunks)} (parallel)")
                                # Prepare the coroutine for this chunk
                                chunk_tasks.append(self.translate_chunk(chunk, from_lang, to_lang, retries=3, chunk_id=chunk_id))
                            # Run all chunk translations in parallel, preserving order
                            chunk_results = await asyncio.gather(*chunk_tasks, return_exceptions=True)
                            for i, result in enumerate(chunk_results):
                                if isinstance(result, Exception):
                                    logger.error(f"[TRANSLATE] Error translating chunk {i+1}: {str(result)}")
                                    translated_chunks.append(f"<div class='error'>Translation error in section {i+1}: {str(result)}</div>")
                                else:
                                    translated_chunks.append(result)
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
                        
                        # Split content if needed - use language-specific chunking
                        max_chunk_size = self.get_max_chunk_size(to_lang)
                        if len(html_content) > max_chunk_size * 1.2:  # Add 20% buffer
                            chunks = self.split_content_into_chunks(html_content, max_chunk_size, to_lang)
                            logger.info(f"[TRANSLATE] Split image into {len(chunks)} chunks for {to_lang} translation")
                            
                            translated_chunks = []
                            chunk_tasks = []
                            for i, chunk in enumerate(chunks):
                                chunk_id = f"{process_id}-img-c{i+1}"
                                logger.info(f"[TRANSLATE] Translating chunk {i+1}/{len(chunks)} (parallel)")
                                # Prepare the coroutine for this chunk
                                chunk_tasks.append(self.translate_chunk(chunk, from_lang, to_lang, retries=3, chunk_id=chunk_id))
                            # Run all chunk translations in parallel, preserving order
                            chunk_results = await asyncio.gather(*chunk_tasks, return_exceptions=True)
                            for i, result in enumerate(chunk_results):
                                if isinstance(result, Exception):
                                    logger.error(f"[TRANSLATE] Error translating image chunk {i+1}: {str(result)}")
                                    translated_chunks.append(f"<div class='error'>Translation error in section {i+1}: {str(result)}</div>")
                                else:
                                    translated_chunks.append(result)
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

# Create a singleton instance
translation_service = TranslationService()
