from fastapi import APIRouter, Depends, HTTPException, BackgroundTasks, UploadFile, File
from sqlalchemy.orm import Session
from typing import List, Optional
from app.core.database import get_db
from app.models.translation import TranslationProgress, TranslationChunk
from app.workers.translation_worker import TranslationWorker
from app.core.config import settings
from app.core.logging import loggers
import uuid

router = APIRouter()
logger = loggers["api"]

@router.post("/translate")
async def create_translation(
    file: UploadFile = File(...),
    from_lang: str = "en",
    to_lang: str = "es",
    background_tasks: BackgroundTasks = None,
    db: Session = Depends(get_db)
) -> dict:
    """Create a new translation task."""
    try:
        # Validate file type
        if not file.content_type in settings.SUPPORTED_FILE_TYPES:
            raise HTTPException(
                status_code=400,
                detail=f"Unsupported file type. Supported types: {settings.SUPPORTED_FILE_TYPES}"
            )
        
        # Read file content
        content = await file.read()
        
        # Create translation progress
        progress = TranslationProgress(
            processId=str(uuid.uuid4()),
            userId="user123",  # TODO: Get from auth
            totalPages=0,
            currentPage=0,
            progress=0,
            status="pending",
            fileName=file.filename,
            fromLang=from_lang,
            toLang=to_lang,
            fileType=file.content_type
        )
        db.add(progress)
        db.commit()
        
        # Start translation in background
        worker = TranslationWorker(db)
        background_tasks.add_task(
            worker.process_translation,
            progress.processId,
            content,
            file.content_type,
            from_lang,
            to_lang
        )
        
        return {
            "message": "Translation started",
            "processId": progress.processId
        }
        
    except Exception as e:
        logger.error(f"Failed to create translation: {str(e)}")
        raise HTTPException(
            status_code=500,
            detail=f"Failed to create translation: {str(e)}"
        )

@router.get("/progress/{process_id}")
async def get_translation_progress(
    process_id: str,
    db: Session = Depends(get_db)
) -> dict:
    """Get translation progress."""
    try:
        progress = db.query(TranslationProgress).filter(
            TranslationProgress.processId == process_id
        ).first()
        
        if not progress:
            raise HTTPException(
                status_code=404,
                detail="Translation progress not found"
            )
        
        return {
            "processId": progress.processId,
            "status": progress.status,
            "progress": progress.progress,
            "currentPage": progress.currentPage,
            "totalPages": progress.totalPages,
            "fileName": progress.fileName,
            "fromLang": progress.fromLang,
            "toLang": progress.toLang
        }
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to get translation progress: {str(e)}")
        raise HTTPException(
            status_code=500,
            detail=f"Failed to get translation progress: {str(e)}"
        )

@router.get("/chunks/{process_id}")
async def get_translation_chunks(
    process_id: str,
    page: Optional[int] = None,
    db: Session = Depends(get_db)
) -> dict:
    """Get translated chunks."""
    try:
        # Check if translation is completed
        progress = db.query(TranslationProgress).filter(
            TranslationProgress.processId == process_id
        ).first()
        
        if not progress:
            raise HTTPException(
                status_code=404,
                detail="Translation progress not found"
            )
        
        if progress.status != "completed":
            raise HTTPException(
                status_code=400,
                detail="Translation is not completed yet"
            )
        
        # Get chunks
        query = db.query(TranslationChunk).filter(
            TranslationChunk.processId == process_id
        )
        
        if page:
            query = query.filter(TranslationChunk.pageNumber == page)
        
        chunks = query.order_by(TranslationChunk.pageNumber).all()
        
        return {
            "processId": process_id,
            "chunks": [
                {
                    "pageNumber": chunk.pageNumber,
                    "content": chunk.content
                }
                for chunk in chunks
            ]
        }
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to get translation chunks: {str(e)}")
        raise HTTPException(
            status_code=500,
            detail=f"Failed to get translation chunks: {str(e)}"
        )

@router.post("/retry/{process_id}")
async def retry_translation(
    process_id: str,
    file: UploadFile = File(...),
    background_tasks: BackgroundTasks = None,
    db: Session = Depends(get_db)
) -> dict:
    """Retry a failed translation."""
    try:
        # Get translation progress
        progress = db.query(TranslationProgress).filter(
            TranslationProgress.processId == process_id
        ).first()
        
        if not progress:
            raise HTTPException(
                status_code=404,
                detail="Translation progress not found"
            )
        
        if progress.status != "failed":
            raise HTTPException(
                status_code=400,
                detail="Can only retry failed translations"
            )
        
        # Read file content
        content = await file.read()
        
        # Start retry in background
        worker = TranslationWorker(db)
        background_tasks.add_task(
            worker.retry_failed_translation,
            process_id,
            content,
            file.content_type,
            progress.fromLang,
            progress.toLang
        )
        
        return {
            "message": "Translation retry started",
            "processId": process_id
        }
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to retry translation: {str(e)}")
        raise HTTPException(
            status_code=500,
            detail=f"Failed to retry translation: {str(e)}"
        ) 