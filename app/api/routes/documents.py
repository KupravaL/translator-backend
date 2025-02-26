import time
import uuid
import os
import tempfile
import io
import gc
import fitz
import asyncio
import logging
from fastapi import APIRouter, Depends, HTTPException, UploadFile, File, Form, status, BackgroundTasks
from sqlalchemy.orm import Session, load_only
from typing import Optional, List
from app.core.database import get_db, SessionLocal
from app.core.auth import get_current_user
from app.services.document_processing import document_processing_service
from app.services.translation import translation_service, TranslationError
from app.services.balance import balance_service
from app.models.translation import TranslationProgress, TranslationChunk
from app.core.config import settings
from pydantic import BaseModel
from starlette.concurrency import run_in_threadpool
from datetime import datetime

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] [API:Documents] %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S'
)
logger = logging.getLogger("documents")

router = APIRouter()

class TranslationProgressResponse(BaseModel):
    processId: str
    userId: str
    totalPages: int
    currentPage: int
    progress: float
    status: str
    fileName: Optional[str] = None
    fromLang: Optional[str] = None
    toLang: Optional[str] = None
    fileType: Optional[str] = None
    createdAt: str
    updatedAt: str

@router.post("/translate")
async def translate_document(
    background_tasks: BackgroundTasks,
    file: UploadFile = File(...),
    from_lang: str = Form(...),
    to_lang: str = Form(...),
    current_user: str = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """Initiate an asynchronous document translation with direct file handling."""
    try:
        start_time = time.time()
        request_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        logger.info(f"Translation request received for file: {file.filename}, from {from_lang} to {to_lang}, user: {current_user}")
        
        # Get file info
        file_type = file.content_type.lower() if file.content_type else ""
        file_name = file.filename or "document"

        logger.info(f"Processing file: {file_name}, type: {file_type}")

        # Validate file type first - fast check
        if file_type not in settings.SUPPORTED_IMAGE_TYPES + settings.SUPPORTED_DOC_TYPES:
            logger.error(f"Unsupported file type: {file_type}")
            return {
                "error": f"Unsupported file type: {file_type}",
                "type": "VALIDATION_ERROR"
            }

        # Check API Keys early
        if not settings.GOOGLE_API_KEY:
            logger.error("GOOGLE_API_KEY not configured")
            return {
                "error": "Google API key not configured.",
                "type": "CONFIG_ERROR"
            }
        
        if not settings.ANTHROPIC_API_KEY:
            logger.error("ANTHROPIC_API_KEY not configured")
            return {
                "error": "Anthropic API key not configured.",
                "type": "CONFIG_ERROR"
            }
            
        # Generate a unique process ID
        process_id = str(uuid.uuid4())
        logger.info(f"Generated process ID: {process_id}")
        
        # Create a translation progress record
        translation_progress = TranslationProgress(
            processId=process_id,
            userId=current_user,
            status="in_progress", # Use in_progress instead of pending
            fileName=file_name,
            fromLang=from_lang,
            toLang=to_lang,
            fileType=file_type,
            totalPages=0,
            currentPage=0,
            progress=0
        )
        db.add(translation_progress)
        db.commit()
        logger.info(f"Created translation progress record for {process_id}")
        
        # Read the file content directly here
        try:
            file_content = await file.read()
            file_size = len(file_content)
            logger.info(f"Read file content, size: {file_size / (1024 * 1024):.2f} MB")
            
            # Validate file size
            if file_size > settings.MAX_FILE_SIZE:
                logger.error(f"File too large: {file_size / (1024 * 1024):.2f} MB")
                update_translation_status(db, process_id, "failed")
                return {
                    "error": f"File too large. Maximum size is {settings.MAX_FILE_SIZE / (1024 * 1024)}MB.",
                    "type": "VALIDATION_ERROR"
                }
            
            # Save to temporary file
            with tempfile.NamedTemporaryFile(delete=False, suffix=os.path.splitext(file_name)[1]) as temp_file:
                temp_file.write(file_content)
                temp_path = temp_file.name
                
            logger.info(f"Saved file to temporary path: {temp_path}")
            
            # Schedule the translation in background
            background_tasks.add_task(
                process_document_translation,
                temp_path=temp_path,
                process_id=process_id,
                from_lang=from_lang,
                to_lang=to_lang,
                user_id=current_user,
                file_type=file_type,
                file_name=file_name
            )
            
        except Exception as file_error:
            logger.error(f"Error processing file: {str(file_error)}", exc_info=True)
            update_translation_status(db, process_id, "failed")
            return {
                "error": f"Failed to process file: {str(file_error)}",
                "type": "FILE_ERROR"
            }
            
        logger.info(f"Added background task for process {process_id}")
        
        # Return quickly with the process ID
        duration = round((time.time() - start_time) * 1000)
        logger.info(f"Initial request processing completed in {duration}ms, background processing started")
        
        return {
            "success": True,
            "message": "Translation process initiated",
            "processId": process_id,
            "status": "in_progress"
        }
        
    except Exception as e:
        logger.error(f"Error initiating translation: {str(e)}", exc_info=True)
        return {
            "error": f"Failed to initiate translation: {str(e)}",
            "type": "SYSTEM_ERROR"
        }
    
async def process_document_translation(temp_path, process_id, from_lang, to_lang, user_id, file_type, file_name):
    """Process document translation in the background with comprehensive logging and robust error handling."""
    start_time = time.time()
    logger.info(f"Starting background translation process for ID: {process_id}")
    logger.info(f"Process details - File: {file_name}, Type: {file_type}, From: {from_lang}, To: {to_lang}, User: {user_id}")
    db = None
    
    try:
        # Get a database session using a context manager for automatic cleanup
        db = SessionLocal()
        
        # Read the file content with proper resource management
        file_content = None
        try:
            with open(temp_path, "rb") as f:
                file_content = f.read()
            logger.info(f"Read file content, size: {len(file_content) / 1024:.2f} KB")
        except Exception as e:
            logger.error(f"Failed to read file: {str(e)}")
            update_translation_status(db, process_id, "failed")
            return
        
        # Process file based on its type
        total_pages = 0
        translated_pages = []
        
        logger.info(f"Starting content extraction for {process_id}")
        
        # For PDF files
        if file_type in settings.SUPPORTED_DOC_TYPES and 'pdf' in file_type:
            try:
                logger.info(f"Processing PDF document for {process_id}")
                # Get PDF document information
                buffer = io.BytesIO(file_content)
                with fitz.open(stream=buffer, filetype="pdf") as doc:
                    total_pages = len(doc)
                    logger.info(f"PDF has {total_pages} pages")
                    
                    # Update total pages in database
                    translation_progress = db.query(TranslationProgress).filter(
                        TranslationProgress.processId == process_id
                    ).first()
                    
                    if translation_progress:
                        translation_progress.totalPages = total_pages
                        db.commit()
                        logger.info(f"Updated total pages for {process_id}: {total_pages}")
                
                # Process each page in the PDF
                for page_index in range(total_pages):
                    page_start_time = time.time()
                    current_page = page_index + 1
                    logger.info(f"Processing page {current_page}/{total_pages} for {process_id}")
                    
                    # Update current page in database
                    translation_progress = db.query(TranslationProgress).filter(
                        TranslationProgress.processId == process_id
                    ).first()
                    
                    if not translation_progress or translation_progress.status == "failed":
                        logger.warning(f"Translation was canceled or failed")
                        return
                    
                    translation_progress.currentPage = current_page
                    translation_progress.progress = int((current_page / total_pages) * 100)
                    db.commit()
                    logger.info(f"Updated progress for {process_id}: Page {current_page}/{total_pages} ({translation_progress.progress}%)")
                    
                    # Extract content from the page
                    try:
                        logger.info(f"Extracting content from page {current_page} for {process_id}")
                        html_content = await translation_service.extract_page_content(file_content, page_index)
                        
                        if html_content and len(html_content.strip()) > 0:
                            logger.info(f"Successfully extracted content from page {current_page} ({len(html_content)} chars)")
                            
                            # Translate the extracted content
                            try:
                                logger.info(f"Translating content from page {current_page} (from {from_lang} to {to_lang})")
                                
                                # Split content into chunks if it's too large
                                if len(html_content) > 12000:
                                    logger.info(f"Content too large ({len(html_content)} chars), splitting into chunks")
                                    chunks = translation_service.split_content_into_chunks(html_content, 10000)
                                    logger.info(f"Split into {len(chunks)} chunks")
                                    
                                    translated_chunks = []
                                    for i, chunk in enumerate(chunks):
                                        chunk_id = f"{process_id}-p{current_page}-c{i+1}"
                                        logger.info(f"Translating chunk {i+1}/{len(chunks)} for page {current_page}")
                                        translated_chunk = await translation_service.translate_chunk(
                                            chunk, from_lang, to_lang, retries=3, chunk_id=chunk_id
                                        )
                                        translated_chunks.append(translated_chunk)
                                        logger.info(f"Successfully translated chunk {i+1}/{len(chunks)} for page {current_page}")
                                    
                                    # Combine translated chunks
                                    translated_content = translation_service.combine_html_content(translated_chunks)
                                    logger.info(f"Combined {len(translated_chunks)} translated chunks for page {current_page}")
                                else:
                                    logger.info(f"Translating content as a single chunk for page {current_page}")
                                    chunk_id = f"{process_id}-p{current_page}"
                                    translated_content = await translation_service.translate_chunk(
                                        html_content, from_lang, to_lang, retries=3, chunk_id=chunk_id
                                    )
                                    logger.info(f"Successfully translated content for page {current_page}")
                                
                                # Store translated content in database
                                translation_chunk = TranslationChunk(
                                    processId=process_id,
                                    pageNumber=page_index,
                                    content=translated_content
                                )
                                db.add(translation_chunk)
                                db.commit()
                                logger.info(f"Saved translation for page {current_page} to database")
                                
                                # Add to translated pages
                                translated_pages.append(page_index)
                                
                                # Update progress
                                page_time = time.time() - page_start_time
                                logger.info(f"Completed page {current_page}/{total_pages} in {page_time:.2f} seconds")
                            except Exception as e:
                                logger.error(f"Error translating page {current_page}: {str(e)}", exc_info=True)
                                # Continue to next page despite error
                        else:
                            logger.warning(f"Empty content extracted from page {current_page}")
                    except Exception as e:
                        logger.error(f"Error extracting content from page {current_page}: {str(e)}", exc_info=True)
                        # Continue to next page despite error
            except Exception as e:
                logger.error(f"Error processing PDF: {str(e)}", exc_info=True)
                update_translation_status(db, process_id, "failed")
                return
        
        # For image files
        elif file_type in settings.SUPPORTED_IMAGE_TYPES:
            try:
                logger.info(f"Processing image file for {process_id}")
                # Update total pages to 1 for images
                total_pages = 1
                translation_progress = db.query(TranslationProgress).filter(
                    TranslationProgress.processId == process_id
                ).first()
                
                if translation_progress:
                    translation_progress.totalPages = total_pages
                    translation_progress.currentPage = 1
                    db.commit()
                    logger.info(f"Set total pages to 1 for image file")
                
                # Extract content from the image
                logger.info(f"Extracting content from image ({len(file_content) / 1024:.2f} KB)")
                html_content = await translation_service.extract_from_image(file_content)
                
                if html_content and len(html_content.strip()) > 0:
                    logger.info(f"Successfully extracted content from image ({len(html_content)} chars)")
                    
                    # Translate the extracted content
                    try:
                        logger.info(f"Translating image content (from {from_lang} to {to_lang})")
                        
                        # Split content into chunks if it's too large
                        if len(html_content) > 12000:
                            logger.info(f"Content too large ({len(html_content)} chars), splitting into chunks")
                            chunks = translation_service.split_content_into_chunks(html_content, 10000)
                            logger.info(f"Split into {len(chunks)} chunks")
                            
                            translated_chunks = []
                            for i, chunk in enumerate(chunks):
                                chunk_id = f"{process_id}-img-c{i+1}"
                                logger.info(f"Translating chunk {i+1}/{len(chunks)} for image")
                                translated_chunk = await translation_service.translate_chunk(
                                    chunk, from_lang, to_lang, retries=3, chunk_id=chunk_id
                                )
                                translated_chunks.append(translated_chunk)
                                logger.info(f"Successfully translated chunk {i+1}/{len(chunks)} for image")
                            
                            # Combine translated chunks
                            translated_content = translation_service.combine_html_content(translated_chunks)
                            logger.info(f"Combined {len(translated_chunks)} translated chunks for image")
                        else:
                            logger.info(f"Translating image content as a single chunk")
                            chunk_id = f"{process_id}-img"
                            translated_content = await translation_service.translate_chunk(
                                html_content, from_lang, to_lang, retries=3, chunk_id=chunk_id
                            )
                            logger.info(f"Successfully translated image content")
                        
                        # Store translated content in database
                        translation_chunk = TranslationChunk(
                            processId=process_id,
                            pageNumber=0,  # Single page for image
                            content=translated_content
                        )
                        db.add(translation_chunk)
                        db.commit()
                        logger.info(f"Saved image translation to database")
                        
                        # Add to translated pages
                        translated_pages.append(0)
                        
                    except Exception as e:
                        logger.error(f"Error translating image content: {str(e)}", exc_info=True)
                        update_translation_status(db, process_id, "failed")
                        return
                else:
                    logger.error(f"Failed to extract content from image")
                    update_translation_status(db, process_id, "failed")
                    return
            except Exception as e:
                logger.error(f"Error processing image: {str(e)}", exc_info=True)
                update_translation_status(db, process_id, "failed")
                return
        else:
            logger.error(f"Unsupported file type: {file_type}")
            update_translation_status(db, process_id, "failed")
            return
        
        # Complete the translation process
        if len(translated_pages) > 0:
            logger.info(f"Translation completed for {process_id}: {len(translated_pages)}/{total_pages} pages translated")
            
            # Update translation progress to completed
            translation_progress = db.query(TranslationProgress).filter(
                TranslationProgress.processId == process_id
            ).first()
            
            if translation_progress:
                translation_progress.status = "completed"
                translation_progress.progress = 100
                translation_progress.currentPage = total_pages
                db.commit()
                logger.info(f"Updated status to completed for {process_id}")
            
            # Complete the process
            total_duration = time.time() - start_time
            logger.info(f"Translation process completed for {process_id} in {total_duration:.2f} seconds")
        else:
            logger.error(f"No pages were successfully translated for {process_id}")
            update_translation_status(db, process_id, "failed")
    except Exception as e:
        logger.error(f"Background task error: {str(e)}", exc_info=True)
        if db:
            try:
                update_translation_status(db, process_id, "failed")
            except Exception as inner_e:
                logger.error(f"Error updating translation status to failed: {str(inner_e)}", exc_info=True)
    finally:
        # Clean up resources
        if db:
            db.close()
        
        # Clean up temporary file with retry logic
        if os.path.exists(temp_path):
            for attempt in range(3):
                try:
                    os.unlink(temp_path)
                    logger.info(f"Cleaned up temporary file {temp_path}")
                    break
                except Exception as e:
                    logger.warning(f"Attempt {attempt+1}: Failed to clean up temporary file: {str(e)}")
                    await asyncio.sleep(0.5)

# Helper function for updating translation status
def update_translation_status(db, process_id, status, progress=0):
    """Update translation status with error handling."""
    try:
        translation_progress = db.query(TranslationProgress).filter(
            TranslationProgress.processId == process_id
        ).first()
        
        if translation_progress:
            translation_progress.status = status
            translation_progress.progress = progress if status == "failed" else translation_progress.progress
            db.commit()
            logger.info(f"Updated translation status for {process_id} to {status}")
            return True
    except Exception as e:
        db.rollback()
        logger.error(f"Failed to update translation status: {str(e)}", exc_info=True)
        return False
    
@router.get("/status/{process_id}")
async def get_translation_status(
    process_id: str,
    current_user: str = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """Get status of a translation process."""
    start_time = time.time()
    logger.info(f"Received status check request for process ID: {process_id}, user: {current_user}")
    
    try:
        # Use a more efficient query with select columns to reduce data transfer
        progress = await run_in_threadpool(
            lambda: db.query(TranslationProgress)
            .options(load_only(
                TranslationProgress.processId,
                TranslationProgress.status,
                TranslationProgress.progress,
                TranslationProgress.currentPage,
                TranslationProgress.totalPages,
                TranslationProgress.fileName
            ))
            .filter(
                TranslationProgress.processId == process_id,
                TranslationProgress.userId == current_user
            )
            .first()
        )
        
        if not progress:
            logger.error(f"Process ID not found: {process_id}")
            raise HTTPException(status_code=404, detail="Translation process not found")
        
        response = {
            "processId": progress.processId,
            "status": progress.status,
            "progress": progress.progress,
            "currentPage": progress.currentPage,
            "totalPages": progress.totalPages,
            "fileName": progress.fileName
        }
        
        duration = round((time.time() - start_time) * 1000)
        logger.info(f"Status check completed in {duration}ms for {process_id}: status={progress.status}, progress={progress.progress}%, page {progress.currentPage}/{progress.totalPages}")
        
        # If process is in_progress, add additional logging about background task
        if progress.status == "in_progress":
            last_update_seconds = (time.time() - progress.updatedAt.timestamp()) if progress.updatedAt else 0
            logger.info(f"Active translation - Last updated: {round(last_update_seconds)}s ago")
        
        return response
    except HTTPException:
        raise
    except Exception as e:
        # Log the error but don't expose details to client
        duration = round((time.time() - start_time) * 1000)
        logger.error(f"Status check error after {duration}ms: {str(e)}", exc_info=True)
        
        # Return a minimal successful response with default status
        # This prevents client-side 500 errors while still allowing polling to continue
        return {
            "processId": process_id,
            "status": "pending",
            "progress": 0,
            "currentPage": 0,
            "totalPages": 0,
            "fileName": None,
        }
    
@router.get("/result/{process_id}")
async def get_translation_result(
    process_id: str,
    current_user: str = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """Get the completed translation result."""
    start_time = time.time()
    logger.info(f"Fetching translation result for process ID: {process_id}")
    
    # Find the translation progress
    progress = db.query(TranslationProgress).filter(
        TranslationProgress.processId == process_id,
        TranslationProgress.userId == current_user
    ).first()
    
    if not progress:
        logger.error(f"Process ID not found: {process_id}")
        raise HTTPException(status_code=404, detail="Translation process not found")
    
    if progress.status != "completed":
        logger.error(f"Translation not completed: {process_id}, status: {progress.status}")
        raise HTTPException(status_code=400, detail=f"Translation is not completed. Current status: {progress.status}")
    
    # Fetch all chunks for this translation
    logger.info(f"Fetching translation chunks for process ID: {process_id}")
    chunks = db.query(TranslationChunk).filter(
        TranslationChunk.processId == process_id
    ).order_by(TranslationChunk.pageNumber).all()
    
    if not chunks:
        logger.error(f"No translation chunks found for process ID: {process_id}")
        raise HTTPException(status_code=404, detail="Translation content not found")
    
    # Combine all chunks
    contents = [chunk.content for chunk in chunks]
    combined_content = translation_service.combine_html_content(contents)
    
    duration = round((time.time() - start_time) * 1000)
    logger.info(f"Translation result fetched in {duration}ms: {len(chunks)} chunks, combined length: {len(combined_content)} chars")
    
    return {
        "translatedText": combined_content,
        "direction": "rtl" if progress.toLang in ['fa', 'ar'] else "ltr",
        "metadata": {
            "originalFileName": progress.fileName,
            "originalFileType": progress.fileType,
            "processingId": process_id,
            "fromLanguage": progress.fromLang,
            "toLanguage": progress.toLang
        }
    }