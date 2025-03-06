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
from app.services.translation import translation_service, TranslationError
from app.core.config import settings
from app.models.translation import TranslationProgress, TranslationChunk
from typing import Tuple, List, Dict, Any, Optional
import fitz  # PyMuPDF
from bs4 import BeautifulSoup

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
                # This is a placeholder - in a real implementation, you would extract text
                # using appropriate libraries for each format
                
                # For now, we'll use the Gemini API similar to PDFs
                # Create a temporary file
                with tempfile.NamedTemporaryFile(delete=False, suffix='.docx') as temp_file:
                    temp_file.write(file_content)
                    temp_file_path = temp_file.name
                    
                # Send to Gemini for processing (similar to extract_from_image)
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
                
                # Read the file
                with open(temp_file_path, 'rb') as f:
                    doc_data = f.read()
                    
                try:
                    # Use Google Gemini to extract content (similar to image extraction)
                    response = translation_service.gemini_model.generate_content(
                        contents=[prompt, {"mime_type": "application/pdf", "data": doc_data}],
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
        /* Additional styles here */
    </style>
    """
                        html_content = f"{css_styles}\n{html_content}"
                    
                    return html_content
                except Exception as e:
                    logger.error(f"Error processing document with Gemini: {str(e)}")
                    # Fallback to simple text extraction
                    text_content = "Error extracting formatted content. Document may not be properly formatted."
                    return f"<div class='text-content'>{text_content}</div>"
                finally:
                    # Clean up
                    try:
                        os.remove(temp_file_path)
                    except:
                        pass
                        
            else:
                # Unsupported text document type
                return f"<div class='text-content'>Unsupported document type: {file_type}</div>"
                
        except Exception as e:
            logger.error(f"Error processing text document: {str(e)}")
            return f"<div class='error'>Error processing document: {str(e)}</div>"

# Create an instance of DocumentProcessingService
document_processing_service = DocumentProcessingService()