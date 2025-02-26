import time
from fastapi import APIRouter, Depends, HTTPException, UploadFile, File, Form, status, BackgroundTasks
from sqlalchemy.orm import Session
from typing import Optional, List
from app.core.database import get_db
from app.core.auth import get_current_user
from app.services.document_processing import document_processing_service
from app.services.translation import translation_service, TranslationError  # ✅ Import TranslationError
from app.services.balance import balance_service
from app.models.translation import TranslationProgress, TranslationChunk
from app.core.config import settings
from pydantic import BaseModel

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
    file: UploadFile = File(...),
    from_lang: str = Form(...),
    to_lang: str = Form(...),
    current_user: str = Depends(get_current_user),
    db: Session = Depends(get_db),
    background_tasks: BackgroundTasks = BackgroundTasks()
):
    """Initiate an asynchronous document translation."""
    try:
        # Get file info
        file_type = file.content_type.lower() if file.content_type else ""
        file_name = file.filename or "document"

        print(f"Processing file: {file_name}, type: {file_type}")

        # Validate file type
        if file_type not in settings.SUPPORTED_IMAGE_TYPES + settings.SUPPORTED_DOC_TYPES:
            return {
                "error": f"Unsupported file type: {file_type}",
                "type": "VALIDATION_ERROR"
            }, status.HTTP_400_BAD_REQUEST

        # Validate file size
        file_content = await file.read()
        file_size = len(file_content)
        
        if file_size > settings.MAX_FILE_SIZE:
            return {
                "error": f"File too large. Maximum size is {settings.MAX_FILE_SIZE / (1024 * 1024)}MB.",
                "type": "VALIDATION_ERROR"
            }, status.HTTP_400_BAD_REQUEST

        # Generate a unique process ID
        process_id = str(uuid.uuid4())
        
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
        
        # Create a temporary file for the uploaded content
        with tempfile.NamedTemporaryFile(delete=False, suffix=os.path.splitext(file_name)[1]) as temp_file:
            temp_file.write(file_content)
            temp_path = temp_file.name
        
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
        }, status.HTTP_500_INTERNAL_SERVER_ERROR
    
async def process_document_translation(temp_path, process_id, from_lang, to_lang, user_id, file_type, file_name):
    """Process document translation in the background."""
    db = SessionLocal()
    
    try:
        # Update progress to "in_progress"
        translation_progress = db.query(TranslationProgress).filter(
            TranslationProgress.processId == process_id
        ).first()
        
        if not translation_progress:
            print(f"❌ Translation progress record not found for {process_id}")
            return
        
        translation_progress.status = "in_progress"
        db.commit()
        
        # Read the file content
        with open(temp_path, "rb") as f:
            file_content = f.read()
            
        try:
            # Process file based on its type
            if file_type in settings.SUPPORTED_IMAGE_TYPES:
                html_content = await translation_service.extract_from_image(file_content)
                translated_content = await translation_service.translate_chunk(html_content, from_lang, to_lang)
                content = translated_content
                total_pages = 1

                # Check balance
                balance_check = balance_service.check_balance_for_content(db, user_id, content)
                if not balance_check["hasBalance"]:
                    translation_progress.status = "failed"
                    translation_progress.progress = 0
                    db.commit()
                    return

                # Update progress
                translation_progress.totalPages = 1
                translation_progress.currentPage = 1
                translation_progress.progress = 100
                db.add(TranslationChunk(processId=process_id, content=content, pageNumber=1))
                
                # Deduct balance
                deduction = balance_service.deduct_pages_for_translation(db, user_id, content)
                
            else:  # PDF handling
                # Similar to existing PDF processing logic but with progress updates
                # ...code for PDF processing...
                pass
                
            # Mark as completed
            translation_progress.status = "completed"
            translation_progress.progress = 100
            db.commit()
            
        except Exception as e:
            print(f"❌ Translation processing error: {str(e)}")
            translation_progress.status = "failed"
            translation_progress.progress = 0
            db.commit()
            
    except Exception as e:
        print(f"❌ Background task error: {str(e)}")
        try:
            translation_progress = db.query(TranslationProgress).filter(
                TranslationProgress.processId == process_id
            ).first()
            if translation_progress:
                translation_progress.status = "failed"
                db.commit()
        except:
            pass
    finally:
        db.close()
        # Clean up temporary file
        try:
            os.unlink(temp_path)
        except:
            pass

@router.get("/status/{process_id}")
async def get_translation_status(
    process_id: str,
    current_user: str = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """Get status of a translation process."""
    # Find the translation progress
    progress = db.query(TranslationProgress).filter(
        TranslationProgress.processId == process_id,
        TranslationProgress.userId == current_user
    ).first()
    
    if not progress:
        raise HTTPException(status_code=404, detail="Translation process not found")
    
    return {
        "processId": progress.processId,
        "status": progress.status,
        "progress": progress.progress,
        "currentPage": progress.currentPage,
        "totalPages": progress.totalPages,
        "fileName": progress.fileName
    }

@router.get("/result/{process_id}")
async def get_translation_result(
    process_id: str,
    current_user: str = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """Get the completed translation result."""
    # Find the translation progress
    progress = db.query(TranslationProgress).filter(
        TranslationProgress.processId == process_id,
        TranslationProgress.userId == current_user
    ).first()
    
    if not progress:
        raise HTTPException(status_code=404, detail="Translation process not found")
    
    if progress.status != "completed":
        raise HTTPException(status_code=400, detail=f"Translation is not completed. Current status: {progress.status}")
    
    # Fetch all chunks for this translation
    chunks = db.query(TranslationChunk).filter(
        TranslationChunk.processId == process_id
    ).order_by(TranslationChunk.pageNumber).all()
    
    if not chunks:
        raise HTTPException(status_code=404, detail="Translation content not found")
    
    # Combine all chunks
    contents = [chunk.content for chunk in chunks]
    combined_content = translation_service.combine_html_content(contents)
    
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