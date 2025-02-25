import time
import uuid
import io
import gc
from fastapi import UploadFile
from sqlalchemy.orm import Session
from app.services.translation import translation_service, TranslationError
from app.core.config import settings
from app.models.translation import TranslationProgress, TranslationChunk
from typing import Tuple
import fitz  # PyMuPDF

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


# Create an instance of DocumentProcessingService
document_processing_service = DocumentProcessingService()