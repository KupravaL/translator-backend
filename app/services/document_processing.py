import time
import uuid
import os
import io
import gc
import re
import tempfile
import logging
import traceback
from fastapi import UploadFile
from sqlalchemy.orm import Session
from app.core.config import settings
from app.models.translation import TranslationProgress, TranslationChunk
from typing import Tuple, List, Dict, Any, Optional
import fitz  # PyMuPDF
from bs4 import BeautifulSoup

# Define our own TranslationError class to avoid circular imports
class TranslationError(Exception):
    def __init__(self, message: str, code: str):
        super().__init__(message)
        self.code = code
        self.name = 'TranslationError'

# Configure logger
logger = logging.getLogger("documents")

class DocumentProcessingService:
    @staticmethod
    async def process_file(
        file: UploadFile,
        from_lang: str,
        to_lang: str,
        user_id: str,
        db: Session,
    ) -> Tuple[str, int, str]:
        """Process a file for translation and return the content, page count, and process ID."""
        
        print(f"📢 Processing file: {file.filename} ({file.content_type})")

        # Ensure `settings` is correctly referenced
        file_type = file.content_type.lower() if file.content_type else ""
        file_content = await file.read()
        file_size = len(file_content)

        # File size validation
        if file_size > settings.MAX_FILE_SIZE:
            print("❌ File too large")
            raise TranslationError(
                f"File too large. Maximum size is {settings.MAX_FILE_SIZE / (1024 * 1024)}MB.",
                "VALIDATION_ERROR",
            )

        # Supported file type check
        if file_type not in settings.SUPPORTED_IMAGE_TYPES + settings.SUPPORTED_DOC_TYPES:
            print("❌ Unsupported file type")
            raise TranslationError(
                f"Unsupported file type: {file_type}. Supported types: {', '.join(settings.SUPPORTED_IMAGE_TYPES + settings.SUPPORTED_DOC_TYPES)}",
                "VALIDATION_ERROR",
            )

        # Generate unique process ID
        process_id = str(uuid.uuid4())
        print(f"📄 Starting document processing: {process_id}")

        # Create a translation progress record
        translation_progress = TranslationProgress(
            processId=process_id,
            userId=user_id,
            status="in_progress",
            fileName=file.filename,
            fromLang=from_lang,
            toLang=to_lang,
            fileType=file_type,
        )
        db.add(translation_progress)
        db.commit()

        try:
            print("✅ Reached processing block")

            if file_type in settings.SUPPORTED_IMAGE_TYPES:
                print("🖼 Processing image file...")
                # Import translation_service here to avoid circular imports
                from app.services.translation import translation_service
                html_content = await translation_service.extract_from_image(file_content)
                translated_content = await translation_service.translate_chunk(html_content, from_lang, to_lang)
                content = translated_content
                total_pages = 1

                translation_progress.totalPages = 1
                translation_progress.currentPage = 1
                translation_progress.progress = 100
                db.add(TranslationChunk(processId=process_id, content=content, pageNumber=1))
                db.commit()

            else:
                print("📄 Processing PDF file using in-memory approach...")

                # Create a BytesIO object from the file content
                pdf_buffer = io.BytesIO(file_content)
                
                try:
                    # Open PDF directly from memory
                    with fitz.open(stream=pdf_buffer, filetype="pdf") as doc:
                        translated_contents = []
                        total_pages = len(doc)
                        translation_progress.totalPages = total_pages
                        db.commit()

                        for page_num in range(total_pages):
                            print(f"📖 Processing page {page_num + 1}/{total_pages}")
                            page = doc[page_num]

                            # Update progress
                            translation_progress.currentPage = page_num + 1
                            translation_progress.progress = ((page_num + 1) / total_pages) * 100
                            db.commit()

                            # Import translation_service here to avoid circular imports
                            from app.services.translation import translation_service
                            # Extract formatted content using the in-memory version
                            html_content = await translation_service._get_formatted_text_from_gemini_buffer(page)

                            if html_content and len(html_content) > 50:
                                translated_content = await translation_service.translate_chunk(html_content, from_lang, to_lang)
                                if translated_content:
                                    translated_contents.append(translated_content)
                                    db.add(TranslationChunk(processId=process_id, content=translated_content, pageNumber=page_num + 1))
                                    db.commit()
                                else:
                                    print(f"⚠️ Translation failed for page {page_num + 1}")
                            else:
                                print(f"⚠️ No valid content extracted from page {page_num + 1}")

                        if not translated_contents:
                            translation_progress.status = "failed"
                            db.commit()
                            raise TranslationError("No content extracted and translated from the document", "CONTENT_ERROR")

                        content = translation_service.combine_html_content(translated_contents)

                finally:
                    # Ensure all resources are properly closed
                    if 'pdf_buffer' in locals():
                        pdf_buffer.close()
                    
                    # Force garbage collection
                    gc.collect()

            # Final validation of the translation result
            if not content.strip():
                translation_progress.status = "failed"
                db.commit()
                raise TranslationError("Translation resulted in empty content", "CONTENT_ERROR")

            if "<" not in content or ">" not in content:
                translation_progress.status = "failed"
                db.commit()
                raise TranslationError("Translation result lacks proper HTML formatting", "CONTENT_ERROR")

            translation_progress.status = "completed"
            translation_progress.progress = 100
            db.commit()

            print(f"✅ Document processing completed: {process_id}")
            return content, total_pages, process_id

        except Exception as e:
            print(f"❌ Error during processing: {e}")
            translation_progress.status = "failed"
            db.commit()
            raise e

    async def process_text_document(self, file_content: bytes, file_type: str) -> str:
        """Extract content from text-based documents like DOC, DOCX, ODT, TXT, RTF."""
        try:
            # For text/plain documents
            if file_type == "text/plain":
                # Decode text content directly
                text_content = file_content.decode('utf-8', errors='replace')
                # Convert to simple HTML
                html_content = f"<div class='text-content'>{text_content}</div>"
                return html_content
                
            # For RTF documents
            elif file_type in ["text/rtf", "application/rtf"]:
                # Extract text from RTF - this is simplified
                # In a real implementation, you'd use a library like striprtf
                # For now, we'll just extract text between RTF markers
                text_content = file_content.decode('utf-8', errors='replace')
                # Strip RTF formatting (simplified approach)
                text_content = re.sub(r'\\[a-z]+', ' ', text_content)
                text_content = re.sub(r'[{}]', '', text_content)
                # Convert to simple HTML
                html_content = f"<div class='text-content'>{text_content}</div>"
                return html_content
                
            # For word processor documents (DOC, DOCX, ODT)
            elif file_type in ["application/msword", 
                             "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                             "application/vnd.oasis.opendocument.text"]:
                # Use the same approach as PDF - send to Gemini for processing
                # For simplified implementation, import translation_service locally to avoid circular imports
                from app.services.translation import translation_service
                
                # Create a temporary file
                with tempfile.NamedTemporaryFile(delete=False, suffix='.docx') as temp_file:
                    temp_file.write(file_content)
                    temp_file_path = temp_file.name
                    
                try:
                    # Read file data
                    with open(temp_file_path, 'rb') as f:
                        doc_data = f.read()
                    
                    # Use Gemini for processing (through translation_service)
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
                    
                    Analyze the content carefully and use the most appropriate structure for each section. Return only valid HTML."""
                    
                    # Use Gemini through translation_service
                    response = translation_service.extraction_model.generate_content(
                        contents=[prompt, {"mime_type": file_type, "data": doc_data}],
                        generation_config={"temperature": 0.1}
                    )
                    
                    html_content = response.text.strip()
                    html_content = html_content.replace('```html', '').replace('```', '').strip()
                    
                    # Add CSS styles
                    if '<style>' not in html_content:
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
                        html_content = f"{css_styles}\n{html_content}"
                    
                    # Process and normalize index numbers
                    soup = BeautifulSoup(html_content, 'html.parser')
                    for index_div in soup.find_all(class_='index'):
                        index_text = index_div.get_text().strip()
                        # Use the local normalize_index method
                        corrected_index = self.normalize_index(index_text) if hasattr(self, 'normalize_index') else index_text
                        if corrected_index != index_text:
                            index_div.string = corrected_index
                            
                    html_content = str(soup)
                    
                    logger.info(f"Successfully extracted HTML content from document: {len(html_content)} chars")
                    
                    return html_content
                    
                except Exception as e:
                    logger.error(f"Error using Gemini for document processing: {str(e)}")
                    # Create a basic fallback HTML content
                    html_content = f"""
                    <div class='document'>
                        <h1>Document Content</h1>
                        <p>Error extracting content from {file_type}: {str(e)}</p>
                        <p>This is a fallback for the document content.</p>
                    </div>
                    """
                    return html_content
                    
                finally:
                    # Clean up the temporary file
                    try:
                        os.remove(temp_file_path)
                    except Exception as e:
                        logger.warning(f"Could not delete temp file {temp_file_path}: {e}")
                    
            else:
                # Unsupported text document type
                return f"<div class='text-content'>Unsupported document type: {file_type}</div>"
                
        except Exception as e:
            logger.error(f"Error processing text document: {str(e)}")
            return f"<div class='error'>Error processing document: {str(e)}</div>"

    # Helper method to normalize index numbers
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
    
document_processing_service = DocumentProcessingService()