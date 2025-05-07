from sqlalchemy import Column, String, Integer, Float, DateTime, ForeignKey, Text, Index, func
from sqlalchemy.orm import relationship
from app.core.database import Base
from app.core.utils import generate_cuid

class TranslationProgress(Base):
    """
    Model for tracking translation progress and metadata.
    
    Attributes:
        id: Unique identifier for the record
        processId: Unique process ID for the translation job
        userId: ID of the user who requested the translation
        totalPages: Total number of pages in the document
        currentPage: Current page being processed
        progress: Progress percentage (0-100)
        status: Current status ('in_progress', 'completed', 'failed')
        fileName: Original file name
        fromLang: Source language code
        toLang: Target language code
        fileType: MIME type of the file
        createdAt: Timestamp of creation
        updatedAt: Timestamp of last update
    """
    __tablename__ = "translation_progresses"

    id = Column(String, primary_key=True, default=generate_cuid)
    processId = Column(String, unique=True, nullable=False)
    userId = Column(String, nullable=False)
    totalPages = Column(Integer, default=0, nullable=False)
    currentPage = Column(Integer, default=0, nullable=False)
    progress = Column(Float, default=0, nullable=False)
    status = Column(String, nullable=False)
    fileName = Column(String, nullable=True)
    fromLang = Column(String, nullable=True)
    toLang = Column(String, nullable=True)
    fileType = Column(String, nullable=True)
    createdAt = Column(DateTime, server_default=func.now())
    updatedAt = Column(DateTime, server_default=func.now(), onupdate=func.now())

    # Relationship with chunks
    chunks = relationship("TranslationChunk", back_populates="translation", cascade="all, delete-orphan")

    __table_args__ = (
        Index(f"ix_{__tablename__}_user_id_status", "userId", "status"),
        Index(f"ix_{__tablename__}_status_created_at", "status", "createdAt"),
        Index(f"ix_{__tablename__}_user_id_created_at", "userId", "createdAt"),
    )

class TranslationChunk(Base):
    """
    Model for storing translated content chunks.
    
    Attributes:
        id: Unique identifier for the record
        processId: Foreign key to TranslationProgress
        content: The translated HTML content
        pageNumber: Page number (1-based) in the original document
        createdAt: Timestamp of creation
    """
    __tablename__ = "translation_chunks"

    id = Column(String, primary_key=True, default=generate_cuid)
    processId = Column(String, ForeignKey("translation_progresses.processId", ondelete="CASCADE"), nullable=False)
    content = Column(Text, nullable=False)
    pageNumber = Column(Integer, nullable=False)
    createdAt = Column(DateTime, server_default=func.now())

    # Relationship with translation progress
    translation = relationship("TranslationProgress", back_populates="chunks")

    __table_args__ = (
        Index(f"ix_{__tablename__}_process_id_page_number", "processId", "pageNumber"),
    )