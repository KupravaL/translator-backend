import asyncio
from typing import Optional
from sqlalchemy.orm import Session
from app.core.logging import loggers
from app.models.translation import TranslationProgress, TranslationChunk
from app.services.translation import translation_service
from app.core.config import settings

logger = loggers["worker"]

class TranslationWorker:
    """Worker for handling translation tasks in the background."""
    
    def __init__(self, db: Session):
        """Initialize the translation worker."""
        self.db = db
        self.translation_service = translation_service
    
    async def process_translation(
        self,
        progress_id: str,
        content: bytes,
        file_type: str,
        from_lang: str,
        to_lang: str
    ) -> None:
        """Process a translation task."""
        try:
            # Get translation progress
            progress = self.db.query(TranslationProgress).filter(
                TranslationProgress.id == progress_id
            ).first()
            
            if not progress:
                logger.error(f"Translation progress not found: {progress_id}")
                return
            
            # Update status to processing
            progress.status = "processing"
            self.db.commit()
            
            # Process translation using the translation service
            result = await self.translation_service.translate_document_content_sync_wrapper(
                progress_id,
                content,
                from_lang,
                to_lang,
                file_type,
                self.db
            )
            
            if not result["success"]:
                logger.error(f"Translation failed: {result.get('error', 'Unknown error')}")
                progress.status = "failed"
                self.db.commit()
            
            logger.info(f"Successfully completed translation: {progress_id}")
            
        except Exception as e:
            logger.error(f"Translation failed: {str(e)}")
            
            # Update status to failed
            if progress:
                progress.status = "failed"
                self.db.commit()
    
    async def retry_failed_translation(
        self,
        progress_id: str,
        content: bytes,
        file_type: str,
        from_lang: str,
        to_lang: str
    ) -> None:
        """Retry a failed translation task."""
        try:
            # Get translation progress
            progress = self.db.query(TranslationProgress).filter(
                TranslationProgress.id == progress_id
            ).first()
            
            if not progress:
                logger.error(f"Translation progress not found: {progress_id}")
                return
            
            # Delete existing chunks
            self.db.query(TranslationChunk).filter(
                TranslationChunk.processId == progress_id
            ).delete()
            
            # Reset progress
            progress.currentPage = 0
            progress.progress = 0
            progress.status = "processing"
            self.db.commit()
            
            # Process translation
            await self.process_translation(
                progress_id,
                content,
                file_type,
                from_lang,
                to_lang
            )
            
        except Exception as e:
            logger.error(f"Retry failed: {str(e)}")
            
            # Update status to failed
            if progress:
                progress.status = "failed"
                self.db.commit() 