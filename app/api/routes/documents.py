import time
from fastapi import APIRouter, Depends, HTTPException, UploadFile, File, Form, status
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
    db: Session = Depends(get_db)
):
    """Translate a document file."""
    try:
        # Get file info
        file_type = file.content_type.lower() if file.content_type else ""
        file_name = file.filename or "document"

        print(f"Processing file: {file_name}, type: {file_type}")

        # Extract first page/chunk for balance check
        try:
            # Reset file position
            await file.seek(0)
            file_content = await file.read()

            # ✅ Use `settings` directly instead of `document_processing_service.settings`
            if file_type in settings.SUPPORTED_IMAGE_TYPES:
                initial_content = await translation_service.extract_from_image(file_content)
            else:
                initial_content = await translation_service.extract_page_content(file_content, 0)

            # ✅ Balance Check
            balance_check = balance_service.check_balance_for_content(db, current_user, initial_content)
            if not balance_check["hasBalance"]:
                return {
                    "error": f"Insufficient balance. Required: {balance_check['requiredPages']} pages, Available: {balance_check['availablePages']} pages",
                    "type": "BALANCE_ERROR",
                    "details": {
                        "requiredPages": balance_check["requiredPages"],
                        "availablePages": balance_check["availablePages"]
                    }
                }, status.HTTP_402_PAYMENT_REQUIRED

            # Reset file position for actual processing
            await file.seek(0)

            # ✅ Process document correctly
            content, total_pages, process_id = await document_processing_service.process_file(
                file, from_lang, to_lang, current_user, db
            )

            # ✅ Deduct pages after successful translation
            deduction = balance_service.deduct_pages_for_translation(db, current_user, content)
            if not deduction["success"]:
                return {
                    "error": deduction["error"] or "Failed to deduct pages",
                    "type": "BALANCE_ERROR",
                    "details": {
                        "remainingBalance": deduction["remainingBalance"]
                    }
                }, status.HTTP_402_PAYMENT_REQUIRED

            print(f"✅ Translation completed: {file_name}, Pages: {total_pages}")

            return {
                "translatedText": content,
                "success": True,
                "message": f"Successfully translated {total_pages} {'page' if total_pages == 1 else 'pages'}",
                "fileType": file_type,
                "direction": "rtl" if to_lang in ['fa', 'ar'] else "ltr",
                "balance": {
                    "deductedPages": deduction["deductedPages"],
                    "remainingBalance": deduction["remainingBalance"]
                },
                "metadata": {
                    "originalFileName": file_name,
                    "originalFileType": file_type,
                    "processingId": process_id,
                    "fromLanguage": from_lang,
                    "toLanguage": to_lang
                }
            }

        except Exception as e:
            print(f"❌ Translation error: {str(e)}")
            status_code = 500 if isinstance(e, TranslationError) and e.code == "CONFIG_ERROR" else 400
            return {
                "error": str(e),
                "type": getattr(e, "code", "UNKNOWN_ERROR"),
                "details": {
                    "processId": "error",
                    "fileName": file_name,
                    "fileType": file_type
                }
            }, status_code

    except Exception as e:
        print(f"❌ Fatal error in document translation: {str(e)}")
        return {
            "error": f"Processing failed: {str(e)}",
            "type": "SYSTEM_ERROR",
            "details": {
                "processId": "error"
            }
        }, status.HTTP_500_INTERNAL_SERVER_ERROR