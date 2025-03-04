from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session, joinedload
from typing import List, Optional
from app.core.database import get_db
from app.core.auth import get_current_user
from app.models.translation import TranslationProgress, TranslationChunk
from datetime import datetime
from pydantic import BaseModel

router = APIRouter()

class TranslationHistoryItem(BaseModel):
    processId: str
    fileName: str
    fromLang: str
    toLang: str
    status: str
    totalPages: int
    completedAt: Optional[datetime]
    createdAt: datetime

class TranslationHistoryResponse(BaseModel):
    history: List[TranslationHistoryItem]
    total: int

@router.get("/history", response_model=TranslationHistoryResponse)
async def get_translation_history(
    limit: int = 2,
    current_user: str = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """
    Get the user's translation history with the most recent completed translations.
    
    Returns the specified number of most recent completed translations.
    """
    try:
        # Get completed translations for this user, ordered by most recent first
        translations = db.query(TranslationProgress).filter(
            TranslationProgress.userId == current_user,
            TranslationProgress.status == "completed"
        ).order_by(
            TranslationProgress.updatedAt.desc()
        ).limit(limit).all()
        
        # Count total number of completed translations for this user
        total_count = db.query(TranslationProgress).filter(
            TranslationProgress.userId == current_user,
            TranslationProgress.status == "completed"
        ).count()
        
        # Format the response
        history_items = []
        for translation in translations:
            history_items.append(TranslationHistoryItem(
                processId=translation.processId,
                fileName=translation.fileName or "Untitled Document",
                fromLang=translation.fromLang or "Unknown",
                toLang=translation.toLang or "Unknown",
                status=translation.status,
                totalPages=translation.totalPages,
                completedAt=translation.updatedAt if translation.status == "completed" else None,
                createdAt=translation.createdAt
            ))
        
        return TranslationHistoryResponse(
            history=history_items,
            total=total_count
        )
    
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to retrieve translation history: {str(e)}"
        )

@router.get("/history/{process_id}/preview")
async def get_translation_preview(
    process_id: str,
    current_user: str = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """
    Get a preview of a specific translation's content.
    
    Returns a sample of the first chunk of translated content for the specified translation.
    """
    try:
        # First verify that this translation belongs to the current user
        translation = db.query(TranslationProgress).filter(
            TranslationProgress.processId == process_id,
            TranslationProgress.userId == current_user
        ).first()
        
        if not translation:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Translation not found or you don't have permission to access it"
            )
        
        # Get the first chunk of the translation
        first_chunk = db.query(TranslationChunk).filter(
            TranslationChunk.processId == process_id
        ).order_by(
            TranslationChunk.pageNumber
        ).first()
        
        if not first_chunk:
            return {
                "processId": process_id,
                "preview": "No content available for preview",
                "hasContent": False
            }
        
        # Extract a short preview (first 1000 characters)
        content_preview = first_chunk.content[:1000] if first_chunk.content else ""
        
        return {
            "processId": process_id,
            "preview": content_preview,
            "hasContent": bool(content_preview),
            "totalChunks": db.query(TranslationChunk).filter(
                TranslationChunk.processId == process_id
            ).count(),
            "metadata": {
                "fileName": translation.fileName,
                "fromLang": translation.fromLang,
                "toLang": translation.toLang,
                "totalPages": translation.totalPages,
                "completedAt": translation.updatedAt.isoformat() if translation.updatedAt else None
            }
        }
    
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to retrieve translation preview: {str(e)}"
        )

@router.get("/history/{process_id}/content")
async def get_translation_content(
    process_id: str,
    current_user: str = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """
    Get the full content of a specific translation.
    
    Returns all translated content chunks for the specified translation.
    """
    try:
        # First verify that this translation belongs to the current user
        translation = db.query(TranslationProgress).filter(
            TranslationProgress.processId == process_id,
            TranslationProgress.userId == current_user
        ).first()
        
        if not translation:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Translation not found or you don't have permission to access it"
            )
        
        # Get all chunks for this translation
        chunks = db.query(TranslationChunk).filter(
            TranslationChunk.processId == process_id
        ).order_by(
            TranslationChunk.pageNumber
        ).all()
        
        if not chunks:
            return {
                "processId": process_id,
                "content": [],
                "hasContent": False,
                "metadata": {
                    "fileName": translation.fileName,
                    "fromLang": translation.fromLang,
                    "toLang": translation.toLang,
                    "totalPages": translation.totalPages,
                    "status": translation.status,
                    "completedAt": translation.updatedAt.isoformat() if translation.updatedAt else None
                }
            }
        
        # Format response with all content chunks
        content_chunks = []
        for chunk in chunks:
            content_chunks.append({
                "pageNumber": chunk.pageNumber + 1,  # Make 1-indexed for display
                "content": chunk.content
            })
        
        # Combine all chunks into a single HTML document if requested
        combined_content = ""
        if chunks:
            combined_content = "<div class='document'>\n"
            for chunk in chunks:
                combined_content += f"<div class='page' id='page-{chunk.pageNumber + 1}'>\n{chunk.content}\n</div>\n"
            combined_content += "</div>"
        
        return {
            "processId": process_id,
            "chunks": content_chunks,
            "combinedContent": combined_content,
            "hasContent": bool(content_chunks),
            "metadata": {
                "fileName": translation.fileName,
                "fromLang": translation.fromLang,
                "toLang": translation.toLang,
                "totalPages": translation.totalPages,
                "status": translation.status,
                "fileType": translation.fileType,
                "createdAt": translation.createdAt.isoformat() if translation.createdAt else None,
                "completedAt": translation.updatedAt.isoformat() if translation.updatedAt else None,
                "direction": "rtl" if translation.toLang in ['fa', 'ar'] else "ltr"
            }
        }
    
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to retrieve translation content: {str(e)}"
        )