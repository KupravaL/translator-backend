import time
import uuid
import os
import tempfile
import io
import gc
import fitz
import asyncio
import logging
import traceback  # Added for detailed error tracing
from fastapi import APIRouter, Depends, HTTPException, UploadFile, File, Form, status, BackgroundTasks, Query
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
from app.core.worker import worker

# Configure logger
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

# Modified section in app/api/routes/documents.py

@router.post("/translate", summary="Initiate document translation")
async def translate_document(
    file: UploadFile = File(...),
    from_lang: str = Form(...),
    to_lang: str = Form(...),
    current_user: str = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """
    Start a document translation process.
    
    This endpoint:
    1. Creates a new translation record
    2. Returns a process ID immediately
    3. Processes the file in a background task using dedicated worker pool
    
    The client should poll the /status/{process_id} endpoint to check progress.
    """
    try:
        start_time = time.time()
        logger.info(f"[TRANSLATE START] User {current_user} requested translation: {file.filename}, {from_lang}->{to_lang}")
        
        # Get file info
        file_type = file.content_type.lower() if file.content_type else ""
        file_name = file.filename or "document"
        
        # Validate file type
        if file_type not in settings.SUPPORTED_IMAGE_TYPES + settings.SUPPORTED_DOC_TYPES:
            logger.error(f"Unsupported file type: {file_type}")
            return {
                "error": f"Unsupported file type: {file_type}",
                "type": "VALIDATION_ERROR" 
            }
        
        # Read file content here in the request handler, before passing to background task
        file_content = await file.read()
        file_size = len(file_content)
        
        # File size validation
        if file_size == 0:
            logger.error(f"Empty file received")
            return {
                "error": "File is empty",
                "type": "VALIDATION_ERROR"
            }
            
        if file_size > settings.MAX_FILE_SIZE:
            logger.error(f"File too large: {file_size/1024/1024:.2f}MB")
            return {
                "error": f"File too large. Maximum size is {settings.MAX_FILE_SIZE/(1024*1024):.1f}MB.",
                "type": "VALIDATION_ERROR"
            }
        
        # Generate a unique process ID first
        process_id = str(uuid.uuid4())
        logger.info(f"Generated process ID: {process_id}")
        
        # Create a translation progress record in pending state
        translation_progress = TranslationProgress(
            processId=process_id,
            userId=current_user,
            status="pending",  # Start with pending status
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
        logger.info(f"Created translation record: {process_id}")
        
        # Submit translation task to the dedicated worker pool
        # instead of using FastAPI's BackgroundTasks
        worker.submit_task(
            task_id=process_id,
            func=handle_translation,
            file_content=file_content,
            process_id=process_id,
            from_lang=from_lang,
            to_lang=to_lang,
            user_id=current_user,
            file_type=file_type,
            file_name=file_name
        )
        
        # Return success response immediately with process ID
        duration = round((time.time() - start_time) * 1000)
        logger.info(f"[TRANSLATE INIT] Completed in {duration}ms, returning {process_id}")
        
        # Estimate translation time based on file size and type
        estimated_time = estimate_translation_time(file_size, file_type)
        
        return {
            "success": True,
            "message": "Translation process initiated",
            "processId": process_id,
            "status": "pending",
            "estimatedTimeSeconds": estimated_time
        }
        
    except Exception as e:
        logger.exception(f"Error initiating translation: {str(e)}")
        return {
            "error": f"Failed to initiate translation: {str(e)}",
            "type": "SYSTEM_ERROR"
        }

# Define the translation function to be executed in the worker thread pool
def handle_translation(
    file_content: bytes,
    process_id: str, 
    from_lang: str, 
    to_lang: str, 
    user_id: str, 
    file_type: str, 
    file_name: str
):
    """Process a translation task in the background worker."""
    start_time = time.time()
    logger.info(f"[BG TASK] Starting file processing for {process_id}")
    temp_file_path = None
    db = None
    
    try:
        # First establish database connection
        db = SessionLocal()
        
        # Update status to processing
        progress = db.query(TranslationProgress).filter(
            TranslationProgress.processId == process_id
        ).first()
        
        if not progress:
            logger.error(f"Translation record not found: {process_id}")
            return
            
        progress.status = "in_progress"
        db.commit()
        logger.info(f"[BG TASK] Updated status to in_progress: {process_id}")
        
        # Process the translation
        try:
            # Log file info
            file_size = len(file_content)
            logger.info(f"[BG TASK] Processing file: {file_size/1024/1024:.2f}MB for {process_id}")
            
            # Perform actual translation using the synchronous version
            translation_result = translation_service.translate_document_content_sync(
                process_id=process_id,
                file_content=file_content,
                from_lang=from_lang,
                to_lang=to_lang,
                file_type=file_type,
                db=db
            )
            
            # The translation_result will contain status information
            # or the synchronous method will have updated the status directly
            logger.info(f"[BG TASK] Translation completed for {process_id}")
            
        except Exception as processing_error:
            logger.exception(f"[BG TASK] Translation processing error: {str(processing_error)}")
            update_translation_status(db, process_id, "failed")
            
    except Exception as e:
        logger.exception(f"[BG TASK] Background task error: {str(e)}")
        try:
            if db:
                update_translation_status(db, process_id, "failed")
        except Exception as inner_e:
            logger.exception(f"Failed to update status to failed: {str(inner_e)}")
            
    finally:
        # Clean up resources
        if db:
            db.close()
            
        # Log task completion
        duration = time.time() - start_time
        logger.info(f"[BG TASK] Background task completed in {duration:.2f}s for {process_id}")
        
        return {
            "processId": process_id,
            "status": "completed",
            "duration": duration
        }     

async def translate_document_content(
    process_id: str,
    file_content: bytes,
    from_lang: str, 
    to_lang: str,
    file_type: str,
    db: Session
):
    """Performs the actual content extraction and translation."""
    start_time = time.time()
    logger.info(f"[TRANSLATE] Starting content extraction for {process_id}")
    
    try:
        total_pages = 0
        translated_pages = []
        
        # Handle PDFs
        if file_type in settings.SUPPORTED_DOC_TYPES and 'pdf' in file_type:
            try:
                # Get PDF document information
                buffer = io.BytesIO(file_content)
                with fitz.open(stream=buffer, filetype="pdf") as doc:
                    total_pages = len(doc)
                
                logger.info(f"[TRANSLATE] PDF has {total_pages} pages for {process_id}")
                
                # Update total pages
                progress = db.query(TranslationProgress).filter(
                    TranslationProgress.processId == process_id
                ).first()
                
                if progress:
                    progress.totalPages = total_pages
                    db.commit()
                
                # Process each page
                for page_index in range(total_pages):
                    page_start = time.time()
                    current_page = page_index + 1
                    
                    # Update progress
                    progress = db.query(TranslationProgress).filter(
                        TranslationProgress.processId == process_id
                    ).first()
                    
                    if not progress or progress.status == "failed":
                        logger.warning(f"[TRANSLATE] Process was canceled: {process_id}")
                        return
                        
                    progress.currentPage = current_page
                    progress.progress = int((current_page / total_pages) * 100)
                    db.commit()
                    
                    logger.info(f"[TRANSLATE] Processing page {current_page}/{total_pages} for {process_id}")
                    
                    # Extract content
                    try:
                        html_content = await translation_service.extract_page_content(file_content, page_index)
                        
                        if html_content and len(html_content.strip()) > 0:
                            logger.info(f"[TRANSLATE] Extracted {len(html_content)} chars from page {current_page}")
                            
                            # Translate content
                            try:
                                translated_content = None
                                
                                # Split content if needed
                                if len(html_content) > 12000:
                                    chunks = translation_service.split_content_into_chunks(html_content, 10000)
                                    logger.info(f"[TRANSLATE] Split into {len(chunks)} chunks")
                                    
                                    translated_chunks = []
                                    for i, chunk in enumerate(chunks):
                                        chunk_id = f"{process_id}-p{current_page}-c{i+1}"
                                        logger.info(f"[TRANSLATE] Translating chunk {i+1}/{len(chunks)}")
                                        chunk_result = await translation_service.translate_chunk(
                                            chunk, from_lang, to_lang, retries=3, chunk_id=chunk_id
                                        )
                                        translated_chunks.append(chunk_result)
                                        
                                    translated_content = translation_service.combine_html_content(translated_chunks)
                                else:
                                    chunk_id = f"{process_id}-p{current_page}"
                                    translated_content = await translation_service.translate_chunk(
                                        html_content, from_lang, to_lang, retries=3, chunk_id=chunk_id
                                    )
                                
                                # Save translation
                                translation_chunk = TranslationChunk(
                                    processId=process_id,
                                    pageNumber=page_index,
                                    content=translated_content
                                )
                                db.add(translation_chunk)
                                db.commit()
                                
                                translated_pages.append(page_index)
                                logger.info(f"[TRANSLATE] Completed page {current_page} in {time.time() - page_start:.2f}s")
                                
                            except Exception as translate_error:
                                logger.exception(f"[TRANSLATE] Error translating page {current_page}: {str(translate_error)}")
                                # Continue to next page
                        else:
                            logger.warning(f"[TRANSLATE] Empty content from page {current_page}")
                    except Exception as extract_error:
                        logger.exception(f"[TRANSLATE] Error extracting page {current_page}: {str(extract_error)}")
                
            except Exception as pdf_error:
                logger.exception(f"[TRANSLATE] PDF processing error: {str(pdf_error)}")
                update_translation_status(db, process_id, "failed")
                return
                
        # Handle images
        elif file_type in settings.SUPPORTED_IMAGE_TYPES:
            try:
                # Set total pages = 1 for images
                total_pages = 1
                progress = db.query(TranslationProgress).filter(
                    TranslationProgress.processId == process_id
                ).first()
                
                if progress:
                    progress.totalPages = total_pages
                    progress.currentPage = 1
                    db.commit()
                
                logger.info(f"[TRANSLATE] Processing image for {process_id}")
                
                # Extract content
                html_content = await translation_service.extract_from_image(file_content)
                
                if html_content and len(html_content.strip()) > 0:
                    logger.info(f"[TRANSLATE] Extracted {len(html_content)} chars from image")
                    
                    # Translate content
                    try:
                        translated_content = None
                        
                        # Split content if needed
                        if len(html_content) > 12000:
                            chunks = translation_service.split_content_into_chunks(html_content, 10000)
                            logger.info(f"[TRANSLATE] Split into {len(chunks)} chunks")
                            
                            translated_chunks = []
                            for i, chunk in enumerate(chunks):
                                chunk_id = f"{process_id}-img-c{i+1}"
                                logger.info(f"[TRANSLATE] Translating chunk {i+1}/{len(chunks)}")
                                chunk_result = await translation_service.translate_chunk(
                                    chunk, from_lang, to_lang, retries=3, chunk_id=chunk_id
                                )
                                translated_chunks.append(chunk_result)
                                
                            translated_content = translation_service.combine_html_content(translated_chunks)
                        else:
                            chunk_id = f"{process_id}-img"
                            translated_content = await translation_service.translate_chunk(
                                html_content, from_lang, to_lang, retries=3, chunk_id=chunk_id
                            )
                        
                        # Save translation
                        translation_chunk = TranslationChunk(
                            processId=process_id,
                            pageNumber=0,
                            content=translated_content
                        )
                        db.add(translation_chunk)
                        db.commit()
                        
                        translated_pages.append(0)
                        logger.info(f"[TRANSLATE] Completed image translation")
                        
                    except Exception as translate_error:
                        logger.exception(f"[TRANSLATE] Error translating image: {str(translate_error)}")
                        update_translation_status(db, process_id, "failed")
                        return
                else:
                    logger.error(f"[TRANSLATE] Failed to extract content from image")
                    update_translation_status(db, process_id, "failed")
                    return
            except Exception as img_error:
                logger.exception(f"[TRANSLATE] Image processing error: {str(img_error)}")
                update_translation_status(db, process_id, "failed")
                return
        else:
            logger.error(f"[TRANSLATE] Unsupported file type: {file_type}")
            update_translation_status(db, process_id, "failed")
            return
            
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
        else:
            logger.error(f"[TRANSLATE] No pages were translated for {process_id}")
            update_translation_status(db, process_id, "failed")
            
    except Exception as e:
        logger.exception(f"[TRANSLATE] Translation error: {str(e)}")
        update_translation_status(db, process_id, "failed")

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
    
@router.get("/status/{process_id}", summary="Check translation status")
async def get_translation_status(
    process_id: str,
    current_user: str = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """
    Get the status of a translation process.
    
    Returns current progress, status, and page information.
    """
    start_time = time.time()
    logger.info(f"[STATUS] Status check for {process_id}")
    
    try:
        # Use optimized query with select columns only
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
            logger.error(f"[STATUS] Process ID not found: {process_id}")
            raise HTTPException(status_code=404, detail="Translation process not found")
        
        # Prepare response
        response = {
            "processId": progress.processId,
            "status": progress.status,
            "progress": progress.progress,
            "currentPage": progress.currentPage,
            "totalPages": progress.totalPages,
            "fileName": progress.fileName
        }
        
        # Log status check
        duration = round((time.time() - start_time) * 1000)
        logger.info(f"[STATUS] Status check completed in {duration}ms: status={progress.status}, progress={progress.progress}%")
        
        return response
    except HTTPException:
        raise
    except Exception as e:
        # Log error but return default status
        logger.exception(f"[STATUS] Error checking status: {str(e)}")
        
        # Return minimal response to prevent client errors
        return {
            "processId": process_id,
            "status": "pending",
            "progress": 0,
            "currentPage": 0,
            "totalPages": 0,
            "fileName": None,
        }


@router.get("/active", summary="List active translations")
async def list_active_translations(
    current_user: str = Depends(get_current_user),
    db: Session = Depends(get_db),
    limit: int = Query(10, ge=1, le=50)
):
    """
    Get a list of recent and active translations for the current user.
    
    This helps to recover from timeouts by finding ongoing translations.
    """
    try:
        # Find recent translations for this user
        recent_translations = db.query(TranslationProgress).filter(
            TranslationProgress.userId == current_user
        ).order_by(
            TranslationProgress.createdAt.desc()
        ).limit(limit).all()
        
        # Convert to response format
        translations = []
        for translation in recent_translations:
            translations.append({
                "processId": translation.processId,
                "status": translation.status,
                "progress": translation.progress,
                "currentPage": translation.currentPage,
                "totalPages": translation.totalPages,
                "fileName": translation.fileName,
                "createdAt": translation.createdAt.isoformat() if translation.createdAt else None,
                "updatedAt": translation.updatedAt.isoformat() if translation.updatedAt else None
            })
        
        return {
            "translations": translations
        }
    except Exception as e:
        logger.exception(f"Error listing active translations: {str(e)}")
        return {
            "error": f"Failed to list translations: {str(e)}",
            "translations": []
        }

@router.get("/find", summary="Find translation by file name")
async def find_translation_by_file(
    file_name: str,
    status: Optional[str] = None,
    current_user: str = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """
    Find a translation process by file name.
    
    This is used to recover from timeouts by finding the process ID
    when the client didn't receive the initial response.
    """
    try:
        # Build query to find translations by file name
        query = db.query(TranslationProgress).filter(
            TranslationProgress.userId == current_user,
            TranslationProgress.fileName == file_name
        )
        
        # Optionally filter by status
        if status:
            query = query.filter(TranslationProgress.status == status)
            
        # Order by most recent first
        query = query.order_by(TranslationProgress.createdAt.desc())
        
        # Get the first match
        translation = query.first()
        
        if not translation:
            # If no exact match, try a fuzzy match on the file name
            fuzzy_query = db.query(TranslationProgress).filter(
                TranslationProgress.userId == current_user,
                TranslationProgress.fileName.like(f"%{file_name.split('.')[0]}%")
            )
            
            if status:
                fuzzy_query = fuzzy_query.filter(TranslationProgress.status == status)
                
            fuzzy_query = fuzzy_query.order_by(TranslationProgress.createdAt.desc())
            translation = fuzzy_query.first()
            
        if not translation:
            # Still no match, return a 404
            raise HTTPException(status_code=404, detail="Translation not found")
        
        # Return the translation details
        return {
            "processId": translation.processId,
            "status": translation.status,
            "progress": translation.progress,
            "currentPage": translation.currentPage,
            "totalPages": translation.totalPages,
            "fileName": translation.fileName,
            "fromLang": translation.fromLang,
            "toLang": translation.toLang,
            "createdAt": translation.createdAt.isoformat() if translation.createdAt else None,
            "updatedAt": translation.updatedAt.isoformat() if translation.updatedAt else None,
            "exactMatch": translation.fileName == file_name
        }
        
    except HTTPException:
        raise
    except Exception as e:
        logger.exception(f"Error finding translation: {str(e)}")
        raise HTTPException(
            status_code=500,
            detail=f"Failed to find translation: {str(e)}"
        )

@router.get("/result/{process_id}", summary="Get translation result")
async def get_translation_result(
    process_id: str,
    current_user: str = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """
    Get the completed translation result.
    
    Returns the translated content and metadata.
    """
    start_time = time.time()
    logger.info(f"[RESULT] Fetching result for {process_id}")
    
    # Find the translation record
    progress = db.query(TranslationProgress).filter(
        TranslationProgress.processId == process_id,
        TranslationProgress.userId == current_user
    ).first()
    
    if not progress:
        logger.error(f"[RESULT] Process ID not found: {process_id}")
        raise HTTPException(status_code=404, detail="Translation process not found")
    
    if progress.status != "completed":
        logger.error(f"[RESULT] Translation not completed: {process_id}, status: {progress.status}")
        raise HTTPException(status_code=400, detail=f"Translation is not completed. Current status: {progress.status}")
    
    # Fetch translation chunks
    logger.info(f"[RESULT] Fetching chunks for {process_id}")
    chunks = db.query(TranslationChunk).filter(
        TranslationChunk.processId == process_id
    ).order_by(TranslationChunk.pageNumber).all()
    
    if not chunks:
        logger.error(f"[RESULT] No chunks found for {process_id}")
        raise HTTPException(status_code=404, detail="Translation content not found")
    
    # Combine chunks
    contents = [chunk.content for chunk in chunks]
    combined_content = translation_service.combine_html_content(contents)
    
    # Log completion
    duration = round((time.time() - start_time) * 1000)
    logger.info(f"[RESULT] Result fetched in {duration}ms: {len(chunks)} chunks, {len(combined_content)} chars")
    
    # Return result
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