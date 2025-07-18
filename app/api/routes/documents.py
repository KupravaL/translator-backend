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
from fastapi import APIRouter, Depends, HTTPException, UploadFile, File, Form, status, BackgroundTasks, Query, Request, Response
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
import math 
from concurrent.futures import ThreadPoolExecutor

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
def estimate_translation_time(file_size: int, file_type: str) -> int:
    """
    Estimate translation time based on file size and type.
    Returns estimated seconds.
    """
    # Base time for processing overhead
    base_time = 10
    
    # Estimate number of pages
    estimated_pages = 1
    
    if 'pdf' in file_type:
        # Rough estimate: 100KB per page for PDFs
        estimated_pages = max(1, int(file_size / (100 * 1024)))
    elif file_type == "application/msword":
        # DOC files: estimate 120KB per page
        estimated_pages = max(1, int(file_size / (120 * 1024)))
    elif file_type == "application/vnd.openxmlformats-officedocument.wordprocessingml.document":
        # DOCX files: estimate 80KB per page
        estimated_pages = max(1, int(file_size / (80 * 1024)))
    elif file_type == "application/vnd.oasis.opendocument.text":
        # ODT files: estimate 100KB per page
        estimated_pages = max(1, int(file_size / (100 * 1024)))
    elif file_type in ["text/plain"]:
        # Plain text: estimate 4KB per page (much smaller)
        estimated_pages = max(1, int(file_size / (4 * 1024)))
    elif file_type in ["text/rtf", "application/rtf"]:
        # RTF files: estimate 15KB per page
        estimated_pages = max(1, int(file_size / (15 * 1024)))
    elif file_type in settings.SUPPORTED_IMAGE_TYPES:
        estimated_pages = 1
    
    # For text-based documents, processing time is generally faster per page
    if file_type in ["text/plain", "text/rtf", "application/rtf"]:
        time_per_page = 25  # Text documents process faster
    else:
        # Average time per page based on logs: ~30-35 seconds
        time_per_page = 35
    
    return base_time + (estimated_pages * time_per_page)

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
            
            # Provide a more helpful error message with supported file types
            supported_types = []
            supported_types.extend(["JPEG", "PNG", "WebP", "HEIC", "HEIF"])  # Image types
            supported_types.extend(["PDF", "Word (DOC/DOCX)", "OpenDocument Text (ODT)", "Text (TXT)", "Rich Text (RTF)"])  # Document types
            
            return {
                "error": f"Unsupported file type: {file_type}. Please upload one of these supported file types: {', '.join(supported_types)}",
                "type": "VALIDATION_ERROR",
                "supportedTypes": {
                    "images": settings.SUPPORTED_IMAGE_TYPES,
                    "documents": settings.SUPPORTED_DOC_TYPES
                }
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
        
        # Calculate required pages based on file size
        required_pages = 1
        if 'pdf' in file_type:
            # More accurately estimate pages in PDFs
            try:
                # Try to get actual page count from the PDF if possible
                buffer = io.BytesIO(file_content)
                with fitz.open(stream=buffer, filetype="pdf") as doc:
                    required_pages = len(doc)
                logger.info(f"Determined PDF has {required_pages} pages from document metadata")
            except Exception as e:
                # Fall back to size-based estimation if PDF parsing fails
                logger.warning(f"Could not determine PDF page count, using size estimate: {str(e)}")
                kb_size = file_size / 1024
                required_pages = max(1, math.ceil(kb_size / 100))  # 1 page per 100KB
                logger.info(f"Estimated {required_pages} pages based on file size ({kb_size:.1f}KB)")
        else:
            # For images, use a flat rate but consider size for very large images
            if file_size > 1024 * 1024:  # If over 1MB
                required_pages = 2  # Charge 2 pages for large images
            else:
                required_pages = 1
            logger.info(f"Using {required_pages} pages for image document")
        
        # Check if user has enough balance
        balance_check = balance_service.check_balance_for_pages(db, current_user, required_pages)
        if not balance_check["hasBalance"]:
            logger.error(f"Insufficient balance: required={required_pages}, available={balance_check['availablePages']}")
            return {
                "error": f"Insufficient balance. Required: {required_pages} pages, Available: {balance_check['availablePages']} pages",
                "type": "INSUFFICIENT_BALANCE",
                "requiredPages": required_pages,
                "availablePages": balance_check["availablePages"]
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
            totalPages=required_pages,  # Set the total pages right away
            currentPage=0,
            progress=0
        )
        
        # Start a database transaction for atomicity
        try:
            # Add the translation record
            db.add(translation_progress)
            
            # Deduct pages from user balance - with consistent page count
            # Create a fixed-length string based on the required pages
            content_for_deduction = ' ' * (required_pages * 3000)
            deduction_result = balance_service.deduct_pages_for_translation(db, current_user, content_for_deduction)
            
            if not deduction_result["success"]:
                logger.error(f"Failed to deduct pages: {deduction_result['error']}")
                # Don't need to explicitly rollback as we'll raise an exception
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=deduction_result["error"]
                )
            
            # If we get here, both operations succeeded, commit the transaction
            db.commit()
            logger.info(f"Created translation record: {process_id} and deducted {required_pages} pages")
            
        except Exception as tx_error:
            # Roll back the transaction if any error occurred
            db.rollback()
            logger.error(f"Transaction failed, rolling back: {str(tx_error)}")
            return {
                "error": f"Failed to process request: {str(tx_error)}",
                "type": "TRANSACTION_ERROR"
            }
        
        # Submit translation task to the dedicated worker pool
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
            "estimatedTimeSeconds": estimated_time,
            "pagesDeducted": required_pages,
            "remainingBalance": deduction_result["remainingBalance"]
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
        
        # Process the translation using the translation service
        try:
            # Log file info
            file_size = len(file_content)
            logger.info(f"[BG TASK] Processing file: {file_size/1024/1024:.2f}MB for {process_id}")
            
            # Use the translation service's sync method
            translation_result = translation_service.translate_document_content_sync(
                process_id=process_id,
                file_content=file_content,
                from_lang=from_lang,
                to_lang=to_lang,
                file_type=file_type,
                db=db
            )
            
            # Check if translation was successful
            if translation_result and translation_result.get("success") == True:
                logger.info(f"[BG TASK] Translation completed successfully for {process_id}")
                
                # Update status to completed
                progress.status = "completed"
                progress.progress = 100
                progress.currentPage = progress.totalPages
                db.commit()
                
                # Log balance audit for successful translation
                balance_service.log_balance_audit(
                    db, 
                    user_id, 
                    "completed", 
                    progress.totalPages, 
                    f"Translation completed: {process_id}, {file_name}"
                )
            else:
                # Translation failed - refund pages
                logger.error(f"[BG TASK] Translation failed for {process_id}: {translation_result.get('error', 'Unknown error')}")
                update_translation_status(db, process_id, "failed")
                
                # Attempt to refund the pages that were deducted
                try:
                    if progress.totalPages > 0:
                        refund_result = balance_service.refund_pages_for_failed_translation(
                            db, user_id, progress.totalPages
                        )
                        if refund_result["success"]:
                            logger.info(f"[BG TASK] Refunded {progress.totalPages} pages to user {user_id} for failed translation")
                        else:
                            logger.error(f"[BG TASK] Failed to refund pages: {refund_result.get('error')}")
                except Exception as refund_error:
                    logger.exception(f"[BG TASK] Error refunding pages: {str(refund_error)}")
            
        except Exception as processing_error:
            logger.exception(f"[BG TASK] Translation processing error: {str(processing_error)}")
            update_translation_status(db, process_id, "failed")
            
            # Attempt to refund the pages that were deducted
            try:
                if progress and progress.totalPages > 0:
                    refund_result = balance_service.refund_pages_for_failed_translation(
                        db, user_id, progress.totalPages
                    )
                    if refund_result["success"]:
                        logger.info(f"[BG TASK] Refunded {progress.totalPages} pages to user {user_id} for failed translation")
                    else:
                        logger.error(f"[BG TASK] Failed to refund pages: {refund_result.get('error')}")
            except Exception as refund_error:
                logger.exception(f"[BG TASK] Error refunding pages: {str(refund_error)}")
            
    except Exception as e:
        logger.exception(f"[BG TASK] Background task error: {str(e)}")
        try:
            if db:
                update_translation_status(db, process_id, "failed")
                
                # Attempt to refund pages if the transaction failed
                try:
                    progress = db.query(TranslationProgress).filter(
                        TranslationProgress.processId == process_id
                    ).first()
                    
                    if progress and progress.totalPages > 0:
                        refund_result = balance_service.refund_pages_for_failed_translation(
                            db, user_id, progress.totalPages
                        )
                        if refund_result["success"]:
                            logger.info(f"[BG TASK] Refunded {progress.totalPages} pages to user {user_id} for failed translation")
                        else:
                            logger.error(f"[BG TASK] Failed to refund pages: {refund_result.get('error')}")
                except Exception as refund_error:
                    logger.exception(f"[BG TASK] Error refunding pages: {str(refund_error)}")
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

def update_translation_status(db, process_id, status, progress=0):
    """Update translation status with error handling and refund for failed translations."""
    try:
        translation_progress = db.query(TranslationProgress).filter(
            TranslationProgress.processId == process_id
        ).first()
        
        if translation_progress:
            # Check if status changed from in_progress to failed
            if translation_progress.status == "in_progress" and status == "failed":
                # If translation failed, we should refund the pages
                try:
                    # Estimate used pages based on file size or content
                    # For simplicity, assume 1 page per document
                    refund_pages = 1
                    
                    # Try to determine a more accurate refund amount if possible
                    if translation_progress.totalPages > 0:
                        # We have a better estimate from the document processing
                        refund_pages = translation_progress.totalPages
                    
                    # Refund the pages
                    logger.info(f"Refunding {refund_pages} pages for failed translation {process_id} to user {translation_progress.userId}")
                    refund_result = balance_service.refund_pages_for_failed_translation(
                        db, 
                        translation_progress.userId, 
                        refund_pages
                    )
                    
                    if refund_result["success"]:
                        logger.info(f"Successfully refunded {refund_pages} pages to user {translation_progress.userId}. New balance: {refund_result['newBalance']}")
                    else:
                        logger.error(f"Failed to refund pages: {refund_result.get('error', 'Unknown error')}")
                        
                except Exception as refund_error:
                    logger.exception(f"Error refunding pages for failed translation {process_id}: {str(refund_error)}")
            
            # Update the status
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
    request: Request,
    response: Response,
    current_user: str = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """
    Get the status of a translation process.
    
    Returns current progress, status, and page information.
    Enhanced to handle auth issues more gracefully.
    """
    start_time = time.time()
    logger.info(f"[STATUS] Status check for {process_id}")
    
    # Check for auth-related headers from client
    status_retry = request.headers.get("X-Status-Retry", "false") == "true"
    if status_retry:
        logger.info(f"[STATUS] Status retry request after auth issue for {process_id}")
    
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
            
            # Add a special header to indicate we checked but found nothing
            response.headers["X-Status-Not-Found"] = "true"
            
            raise HTTPException(status_code=404, detail="Translation process not found")
        
        # Prepare response
        result = {
            "processId": progress.processId,
            "status": progress.status,
            "progress": progress.progress,
            "currentPage": progress.currentPage,
            "totalPages": progress.totalPages,
            "fileName": progress.fileName
        }
        
        # Add last update timestamp to help client track stale data
        response.headers["X-Last-Updated"] = datetime.now().isoformat()
        
        # For long-running processes, add a header to indicate the server is still processing
        if progress.status == 'in_progress' and progress.currentPage > 0:
            response.headers["X-Processing-Active"] = "true"
        
        # Log status check
        duration = round((time.time() - start_time) * 1000)
        logger.info(f"[STATUS] Status check completed in {duration}ms: status={progress.status}, progress={progress.progress}%")
        
        return result
    except HTTPException as http_ex:
        # If it's a 401/403 error, add special headers to guide the client
        if http_ex.status_code in (401, 403):
            logger.warning(f"[STATUS] Auth error during status check for {process_id}: {http_ex.detail}")
            response.headers["X-Status-Auth-Error"] = "true"
            # Re-raise with the same status
            raise
        # Re-raise other HTTP exceptions
        raise
    except Exception as e:
        # Log error but return default status
        logger.exception(f"[STATUS] Error checking status: {str(e)}")
        
        # Add header to indicate error occurred
        response.headers["X-Status-Error"] = "true"
        
        # Return minimal response to prevent client errors
        return {
            "processId": process_id,
            "status": "pending",
            "progress": 0,
            "currentPage": 0,
            "totalPages": 0,
            "fileName": None,
            "error": "Failed to retrieve status",
            "errorDetail": str(e) if settings.DEBUG else None
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

@router.get("/health", summary="Check translation service health")
async def get_translation_health(
    current_user: str = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """Get the health status of the translation service."""
    try:
        from app.services.translation import translation_service
        
        health_status = translation_service.get_health_status()
        
        # Add some additional system info
        health_status.update({
            "timestamp": datetime.now().isoformat(),
            "user_id": current_user,
            "database_connected": True  # If we got here, DB is working
        })
        
        return {
            "success": True,
            "health": health_status
        }
    except Exception as e:
        logger.error(f"Health check failed: {str(e)}")
        return {
            "success": False,
            "error": f"Health check failed: {str(e)}"
        }