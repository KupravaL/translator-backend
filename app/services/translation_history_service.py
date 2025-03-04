import logging
from sqlalchemy.orm import Session
from typing import List, Dict, Any, Optional
from app.models.translation import TranslationProgress, TranslationChunk
from datetime import datetime

# Configure logging
logger = logging.getLogger("history")

class TranslationHistoryService:
    """
    Service for handling translation history operations.
    This service provides methods to retrieve and manage user translation history.
    """
    
    @staticmethod
    def get_recent_translations(db: Session, user_id: str, limit: int = 2) -> List[Dict[str, Any]]:
        """
        Get a user's most recent completed translations.
        
        Args:
            db: Database session
            user_id: User ID to get translations for
            limit: Maximum number of translations to return
            
        Returns:
            List of translation records
        """
        try:
            # Query completed translations
            translations = db.query(TranslationProgress).filter(
                TranslationProgress.userId == user_id,
                TranslationProgress.status == "completed"
            ).order_by(
                TranslationProgress.updatedAt.desc()
            ).limit(limit).all()
            
            # Format results
            results = []
            for translation in translations:
                results.append({
                    "processId": translation.processId,
                    "fileName": translation.fileName or "Untitled Document",
                    "fromLang": translation.fromLang or "Unknown",
                    "toLang": translation.toLang or "Unknown",
                    "status": translation.status,
                    "totalPages": translation.totalPages,
                    "completedAt": translation.updatedAt if translation.status == "completed" else None,
                    "createdAt": translation.createdAt
                })
            
            return results
        except Exception as e:
            logger.error(f"Error getting recent translations: {str(e)}")
            return []
    
    @staticmethod
    def get_translation_content(db: Session, process_id: str, user_id: str) -> Optional[Dict[str, Any]]:
        """
        Get the content of a specific translation.
        
        Args:
            db: Database session
            process_id: Process ID of the translation
            user_id: User ID to verify ownership
            
        Returns:
            Dictionary containing translation content and metadata or None if not found
        """
        try:
            # Verify translation exists and belongs to user
            translation = db.query(TranslationProgress).filter(
                TranslationProgress.processId == process_id,
                TranslationProgress.userId == user_id
            ).first()
            
            if not translation:
                logger.warning(f"Translation {process_id} not found or does not belong to user {user_id}")
                return None
            
            # Get all chunks for this translation
            chunks = db.query(TranslationChunk).filter(
                TranslationChunk.processId == process_id
            ).order_by(
                TranslationChunk.pageNumber
            ).all()
            
            if not chunks:
                logger.warning(f"No content found for translation {process_id}")
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
                        "completedAt": translation.updatedAt
                    }
                }
            
            # Format content by page
            content_by_page = []
            for chunk in chunks:
                content_by_page.append({
                    "pageNumber": chunk.pageNumber + 1,  # Make 1-indexed for display
                    "content": chunk.content
                })
            
            return {
                "processId": process_id,
                "content": content_by_page,
                "hasContent": True,
                "metadata": {
                    "fileName": translation.fileName,
                    "fromLang": translation.fromLang,
                    "toLang": translation.toLang,
                    "totalPages": translation.totalPages,
                    "status": translation.status,
                    "completedAt": translation.updatedAt
                }
            }
            
        except Exception as e:
            logger.error(f"Error getting translation content for {process_id}: {str(e)}")
            return None
    
    @staticmethod
    def get_translation_stats(db: Session, user_id: str) -> Dict[str, Any]:
        """
        Get statistics about user's translations.
        
        Args:
            db: Database session
            user_id: User ID to get statistics for
            
        Returns:
            Dictionary with translation statistics
        """
        try:
            # Get total completed translations
            completed_count = db.query(TranslationProgress).filter(
                TranslationProgress.userId == user_id,
                TranslationProgress.status == "completed"
            ).count()
            
            # Get total pages translated
            total_pages = db.query(TranslationProgress).filter(
                TranslationProgress.userId == user_id,
                TranslationProgress.status == "completed"
            ).with_entities(
                db.func.sum(TranslationProgress.totalPages)
            ).scalar() or 0
            
            # Get most recent translation date
            most_recent = db.query(TranslationProgress).filter(
                TranslationProgress.userId == user_id,
                TranslationProgress.status == "completed"
            ).order_by(
                TranslationProgress.updatedAt.desc()
            ).first()
            
            most_recent_date = most_recent.updatedAt if most_recent else None
            
            return {
                "totalTranslations": completed_count,
                "totalPages": total_pages,
                "mostRecentDate": most_recent_date,
                "mostRecentFileName": most_recent.fileName if most_recent else None
            }
            
        except Exception as e:
            logger.error(f"Error getting translation stats for user {user_id}: {str(e)}")
            return {
                "totalTranslations": 0,
                "totalPages": 0,
                "mostRecentDate": None,
                "mostRecentFileName": None,
                "error": str(e)
            }

# Create a singleton instance
translation_history_service = TranslationHistoryService()