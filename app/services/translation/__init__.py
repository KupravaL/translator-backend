"""Translation services package."""

from app.services.translation.base import BaseTranslationService
from app.services.translation.pdf import PDFTranslationService

__all__ = ['BaseTranslationService', 'PDFTranslationService', 'translation_service']

# Create singleton instance
translation_service = PDFTranslationService() 