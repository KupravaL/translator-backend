import time
import uuid
import os
import tempfile
import io
import gc
import fitz
from fastapi import APIRouter, Depends, HTTPException, UploadFile, File, Form, status, BackgroundTasks
from sqlalchemy.orm import Session
from typing import Optional, List
from app.core.database import get_db, SessionLocal
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

        # Check API Keys early
        if not settings.GOOGLE_API_KEY:
            print("❌ GOOGLE_API_KEY not configured")
            return {
                "error": "Google API key not configured.",
                "type": "CONFIG_ERROR"
            }, status.HTTP_500_INTERNAL_SERVER_ERROR
        
        if not settings.ANTHROPIC_API_KEY:
            print("❌ ANTHROPIC_API_KEY not configured")
            return {
                "error": "Anthropic API key not configured.",
                "type": "CONFIG_ERROR"
            }, status.HTTP_500_INTERNAL_SERVER_ERROR
            
        # Generate a unique process ID
        process_id = str(uuid.uuid4())
        print(f"Generated process ID: {process_id}")
        
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
        print(f"Created translation progress record for {process_id}")
        
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
        print(f"Added background task for process {process_id}")
        
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
        print(f"Updated status to in_progress for {process_id}")
        
        # Read the file content
        with open(temp_path, "rb") as f:
            file_content = f.read()
        print(f"Read file content, size: {len(file_content)} bytes")
            
        try:
            # Process file based on its type
            if file_type in settings.SUPPORTED_IMAGE_TYPES:
                print(f"Starting image extraction with Gemini API...")
                try:
                    html_content = await translation_service.extract_from_image(file_content)
                    print(f"Extraction successful, content length: {len(html_content)}")
                except Exception as e:
                    print(f"❌ Image extraction error: {str(e)}")
                    translation_progress.status = "failed"
                    translation_progress.progress = 0
                    db.commit()
                    return
                
                try:
                    print(f"Starting translation with Claude API...")
                    translated_content = await translation_service.translate_chunk(html_content, from_lang, to_lang)
                    print(f"Translation successful, content length: {len(translated_content)}")
                except Exception as e:
                    print(f"❌ Translation error: {str(e)}")
                    translation_progress.status = "failed"
                    translation_progress.progress = 0
                    db.commit()
                    return
                
                content = translated_content
                total_pages = 1

                # Check balance
                balance_check = balance_service.check_balance_for_content(db, user_id, content)
                if not balance_check["hasBalance"]:
                    print(f"❌ Insufficient balance for user {user_id}")
                    translation_progress.status = "failed"
                    translation_progress.progress = 0
                    db.commit()
                    return

                # Update progress
                translation_progress.totalPages = 1
                translation_progress.currentPage = 1
                translation_progress.progress = 100
                
                # Store translation chunk
                try:
                    chunk = TranslationChunk(processId=process_id, content=content, pageNumber=1)
                    db.add(chunk)
                    db.commit()
                    print(f"Saved translation chunk for process {process_id}")
                except Exception as e:
                    print(f"❌ Failed to save translation chunk: {str(e)}")
                    translation_progress.status = "failed"
                    translation_progress.progress = 0
                    db.commit()
                    return
                
                # Deduct balance
                try:
                    deduction = balance_service.deduct_pages_for_translation(db, user_id, content)
                    print(f"Balance deducted: {deduction}")
                except Exception as e:
                    print(f"❌ Failed to deduct balance: {str(e)}")
                    # Continue anyway since the translation is already complete
                
            else:  # PDF handling
                # Process PDF file using document_processing_service's PDF handling
                print(f"Processing PDF file...")
                buffer = io.BytesIO(file_content)
                
                try:
                    # Open PDF directly from memory
                    with fitz.open(stream=buffer, filetype="pdf") as doc:
                        translated_contents = []
                        total_pages = len(doc)
                        translation_progress.totalPages = total_pages
                        db.commit()
                        print(f"PDF has {total_pages} pages")

                        # Check balance for total pages (assuming 1 page of content per PDF page)
                        estimated_pages = total_pages
                        balance_check = {
                            "hasBalance": balance_service.get_user_balance(db, user_id).pages_balance >= estimated_pages,
                            "availablePages": balance_service.get_user_balance(db, user_id).pages_balance,
                            "requiredPages": estimated_pages
                        }
                        
                        if not balance_check["hasBalance"]:
                            print(f"❌ Insufficient balance for PDF with {total_pages} pages")
                            translation_progress.status = "failed"
                            translation_progress.progress = 0
                            db.commit()
                            return

                        for page_num in range(total_pages):
                            print(f"Processing page {page_num + 1}/{total_pages}")
                            page = doc[page_num]

                            # Update progress
                            translation_progress.currentPage = page_num + 1
                            translation_progress.progress = ((page_num + 1) / total_pages) * 100
                            db.commit()

                            # Extract formatted content
                            try:
                                print(f"Extracting content from page {page_num + 1}...")
                                html_content = await translation_service._get_formatted_text_from_gemini_buffer(page)
                                print(f"Extracted content length: {len(html_content) if html_content else 0}")
                            except Exception as e:
                                print(f"❌ Error extracting page {page_num + 1}: {str(e)}")
                                html_content = f"<p>Error extracting content from page {page_num + 1}</p>"

                            if html_content and len(html_content) > 50:
                                try:
                                    print(f"Translating page {page_num + 1}...")
                                    translated_content = await translation_service.translate_chunk(html_content, from_lang, to_lang)
                                    print(f"Translated content length: {len(translated_content) if translated_content else 0}")
                                except Exception as e:
                                    print(f"❌ Error translating page {page_num + 1}: {str(e)}")
                                    continue  # Skip this page and continue with the next
                                
                                if translated_content:
                                    translated_contents.append(translated_content)
                                    try:
                                        db.add(TranslationChunk(processId=process_id, content=translated_content, pageNumber=page_num + 1))
                                        db.commit()
                                        print(f"Saved translation chunk for page {page_num + 1}")
                                    except Exception as e:
                                        print(f"❌ Failed to save translation chunk: {str(e)}")
                                else:
                                    print(f"❌ Empty translation result for page {page_num + 1}")
                            else:
                                print(f"❌ Insufficient content extracted from page {page_num + 1}")

                        # Deduct balance
                        if translated_contents:
                            # Calculate actual pages based on content length
                            try:
                                combined_content = " ".join(translated_contents)
                                deduction = balance_service.deduct_pages_for_translation(db, user_id, combined_content)
                                print(f"Balance deducted: {deduction}")
                            except Exception as e:
                                print(f"❌ Failed to deduct balance: {str(e)}")
                                # Continue anyway since the translation is already saved
                        else:
                            print(f"❌ No translated content for PDF")
                            translation_progress.status = "failed" 
                            db.commit()
                            return
                finally:
                    # Ensure all resources are properly closed
                    if 'buffer' in locals():
                        buffer.close()
                    
                    # Force garbage collection
                    gc.collect()
                
            # Mark as completed
            print(f"Marking translation as completed for {process_id}")
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
            print(f"Cleaned up temporary file {temp_path}")
        except Exception as e:
            print(f"❌ Failed to clean up temporary file: {str(e)}")

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