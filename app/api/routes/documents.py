import time
import uuid
import os
import tempfile
import io
import gc
import fitz
import asyncio
from fastapi import APIRouter, Depends, HTTPException, UploadFile, File, Form, status, BackgroundTasks
from sqlalchemy.orm import Session, load_only
from typing import Optional, List
from app.core.database import get_db, SessionLocal
from app.core.auth import get_current_user
from app.services.document_processing import document_processing_service
from app.services.translation import translation_service, TranslationError  # ✅ Import TranslationError
from app.services.balance import balance_service
from app.models.translation import TranslationProgress, TranslationChunk
from app.core.config import settings
from pydantic import BaseModel
from starlette.concurrency import run_in_threadpool
from datetime import datetime


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
    """Initiate an asynchronous document translation."""
    try:
        start_time = time.time()
        request_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        print(f"[{request_time}] Translation request received for file: {file.filename}, from {from_lang} to {to_lang}")
        
        # Get file info
        file_type = file.content_type.lower() if file.content_type else ""
        file_name = file.filename or "document"

        print(f"[{request_time}] Processing file: {file_name}, type: {file_type}")

        # Validate file type
        if file_type not in settings.SUPPORTED_IMAGE_TYPES + settings.SUPPORTED_DOC_TYPES:
            print(f"[{request_time}] ❌ Unsupported file type: {file_type}")
            return {
                "error": f"Unsupported file type: {file_type}",
                "type": "VALIDATION_ERROR"
            }

        # Validate file size
        file_content = await file.read()
        file_size = len(file_content)
        
        print(f"[{request_time}] File size: {file_size / (1024 * 1024):.2f} MB")
        
        if file_size > settings.MAX_FILE_SIZE:
            print(f"[{request_time}] ❌ File too large: {file_size / (1024 * 1024):.2f} MB")
            return {
                "error": f"File too large. Maximum size is {settings.MAX_FILE_SIZE / (1024 * 1024)}MB.",
                "type": "VALIDATION_ERROR"
            }

        # Check API Keys early
        if not settings.GOOGLE_API_KEY:
            print(f"[{request_time}] ❌ GOOGLE_API_KEY not configured")
            return {
                "error": "Google API key not configured.",
                "type": "CONFIG_ERROR"
            }
        
        if not settings.ANTHROPIC_API_KEY:
            print(f"[{request_time}] ❌ ANTHROPIC_API_KEY not configured")
            return {
                "error": "Anthropic API key not configured.",
                "type": "CONFIG_ERROR"
            }
            
        # Generate a unique process ID
        process_id = str(uuid.uuid4())
        print(f"[{request_time}] Generated process ID: {process_id}")
        
        # Create a translation progress record
        translation_progress = TranslationProgress(
            processId=process_id,
            userId=current_user,
            status="pending", # Initially "pending" before processing starts
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
        print(f"[{request_time}] Created translation progress record for {process_id}")
        
        # Create a temporary file for the uploaded content
        with tempfile.NamedTemporaryFile(delete=False, suffix=os.path.splitext(file_name)[1]) as temp_file:
            temp_file.write(file_content)
            temp_path = temp_file.name
        
        print(f"[{request_time}] Saved file to temporary path: {temp_path}")
        
        # Add translation task to background
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
        print(f"[{request_time}] Added background task for process {process_id}")
        
        duration = round((time.time() - start_time) * 1000)
        print(f"[{request_time}] Request processing completed in {duration}ms")
        
        return {
            "success": True,
            "message": "Translation process initiated",
            "processId": process_id,
            "status": "pending"
        }
        
    except Exception as e:
        print(f"❌ Error initiating translation: {str(e)}")
        return {
            "error": f"Failed to initiate translation: {str(e)}",
            "type": "SYSTEM_ERROR"
        }
    
async def process_document_translation(temp_path, process_id, from_lang, to_lang, user_id, file_type, file_name):
    """Process document translation in the background with improved error handling and resource management."""
    start_time = time.time()
    request_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    print(f"[{request_time}] 🚀 Starting background translation process for ID: {process_id}")
    db = None
    
    try:
        # Get a database session using a context manager for automatic cleanup
        db = SessionLocal()
        
        # Update progress to "in_progress" using a retry mechanism
        for attempt in range(3):
            try:
                translation_progress = db.query(TranslationProgress).filter(
                    TranslationProgress.processId == process_id
                ).with_for_update(nowait=True).first()
                
                if not translation_progress:
                    print(f"[{request_time}] ❌ Translation progress record not found for {process_id}")
                    return
                
                translation_progress.status = "in_progress"
                db.commit()
                print(f"[{request_time}] Updated status to in_progress for {process_id}")
                break
            except Exception as e:
                print(f"[{request_time}] Attempt {attempt+1}: Failed to update status: {str(e)}")
                db.rollback()
                await asyncio.sleep(1)
                if attempt == 2:  # Last attempt
                    print(f"[{request_time}] ❌ Failed to update translation status after multiple attempts")
                    return
        
        # Read the file content with proper resource management
        file_content = None
        try:
            with open(temp_path, "rb") as f:
                file_content = f.read()
            print(f"[{request_time}] Read file content, size: {len(file_content)} bytes")
        except Exception as e:
            print(f"[{request_time}] ❌ Failed to read file: {str(e)}")
            update_translation_status(db, process_id, "failed")
            return
            
        # Process file based on its type with improved error handling...
        # [Rest of your translation processing logic]
        
    except Exception as e:
        print(f"[{request_time}] ❌ Background task error: {str(e)}")
        if db:
            try:
                update_translation_status(db, process_id, "failed")
            except Exception as inner_e:
                print(f"[{request_time}] ❌ Error updating translation status to failed: {str(inner_e)}")
    finally:
        # Clean up resources
        if db:
            db.close()
        
        # Clean up temporary file with retry logic
        if os.path.exists(temp_path):
            for attempt in range(3):
                try:
                    os.unlink(temp_path)
                    print(f"[{request_time}] Cleaned up temporary file {temp_path}")
                    break
                except Exception as e:
                    print(f"[{request_time}] Attempt {attempt+1}: Failed to clean up temporary file: {str(e)}")
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
            return True
    except Exception as e:
        db.rollback()
        print(f"Failed to update translation status: {str(e)}")
        return False
    
@router.get("/status/{process_id}")
async def get_translation_status(
    process_id: str,
    current_user: str = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """Get status of a translation process."""
    start_time = time.time()
    request_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    
    print(f"[{request_time}] Received status check request for process ID: {process_id}, user: {current_user}")
    
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
            .execution_options(
                timeout=5,  # 5 second query timeout
                isolation_level="READ COMMITTED"  # Lower isolation level for status checks
            )
            .first()
        )
        
        if not progress:
            print(f"[{request_time}] ❌ Process ID not found: {process_id}")
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
        print(f"[{request_time}] ✅ Status check completed in {duration}ms for {process_id}: status={progress.status}, progress={progress.progress}%, page {progress.currentPage}/{progress.totalPages}")
        
        # If process is in_progress, add additional logging about background task
        if progress.status == "in_progress":
            last_update_seconds = (time.time() - progress.updatedAt.timestamp()) if progress.updatedAt else 0
            print(f"[{request_time}] 🔄 Active translation - Last updated: {round(last_update_seconds)}s ago")
        
        return response
    except HTTPException:
        raise
    except Exception as e:
        # Log the error but don't expose details to client
        duration = round((time.time() - start_time) * 1000)
        print(f"[{request_time}] ❌ Status check error after {duration}ms: {str(e)}")
        
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
    request_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    
    print(f"[{request_time}] Fetching translation result for process ID: {process_id}")
    
    # Find the translation progress
    progress = db.query(TranslationProgress).filter(
        TranslationProgress.processId == process_id,
        TranslationProgress.userId == current_user
    ).first()
    
    if not progress:
        print(f"[{request_time}] ❌ Process ID not found: {process_id}")
        raise HTTPException(status_code=404, detail="Translation process not found")
    
    if progress.status != "completed":
        print(f"[{request_time}] ❌ Translation not completed: {process_id}, status: {progress.status}")
        raise HTTPException(status_code=400, detail=f"Translation is not completed. Current status: {progress.status}")
    
    # Fetch all chunks for this translation
    print(f"[{request_time}] Fetching translation chunks for process ID: {process_id}")
    chunks = db.query(TranslationChunk).filter(
        TranslationChunk.processId == process_id
    ).order_by(TranslationChunk.pageNumber).all()
    
    if not chunks:
        print(f"[{request_time}] ❌ No translation chunks found for process ID: {process_id}")
        raise HTTPException(status_code=404, detail="Translation content not found")
    
    # Combine all chunks
    contents = [chunk.content for chunk in chunks]
    combined_content = translation_service.combine_html_content(contents)
    
    duration = round((time.time() - start_time) * 1000)
    print(f"[{request_time}] ✅ Translation result fetched in {duration}ms: {len(chunks)} chunks, combined length: {len(combined_content)} chars")
    
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