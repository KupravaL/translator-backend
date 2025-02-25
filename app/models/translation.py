from sqlalchemy import Column, String, Integer, Float, DateTime, ForeignKey, Index, Text
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func
from sqlalchemy.ext.declarative import declared_attr
from app.core.database import Base
import uuid

def generate_cuid():
    """Generate a cuid-like ID."""
    return str(uuid.uuid4())

class TranslationProgress(Base):
    __tablename__ = "translation_progresses"

    id = Column(String, primary_key=True, default=generate_cuid)
    processId = Column(String, unique=True, nullable=False)
    userId = Column(String, nullable=False)
    totalPages = Column(Integer, default=0, nullable=False)
    currentPage = Column(Integer, default=0, nullable=False)
    progress = Column(Float, default=0, nullable=False)  # Store progress as percentage
    status = Column(String, nullable=False)  # 'in_progress' | 'completed' | 'failed'
    fileName = Column(String, nullable=True)
    fromLang = Column(String, nullable=True)
    toLang = Column(String, nullable=True)
    fileType = Column(String, nullable=True)
    createdAt = Column(DateTime, server_default=func.now())
    updatedAt = Column(DateTime, server_default=func.now(), onupdate=func.now())

    # Relationship with chunks
    chunks = relationship("TranslationChunk", back_populates="translation", cascade="all, delete-orphan")

    @declared_attr
    def __table_args__(cls):
        return (
            Index(f"ix_{cls.__tablename__}_user_id_status", "userId", "status"),
            Index(f"ix_{cls.__tablename__}_status_created_at", "status", "createdAt"),
            Index(f"ix_{cls.__tablename__}_user_id_created_at", "userId", "createdAt"),
        )

class TranslationChunk(Base):
    __tablename__ = "translation_chunks"

    id = Column(String, primary_key=True, default=generate_cuid)
    processId = Column(String, ForeignKey("translation_progresses.processId", ondelete="CASCADE"), nullable=False)
    content = Column(Text, nullable=False)
    pageNumber = Column(Integer, nullable=False)
    createdAt = Column(DateTime, server_default=func.now())

    # Relationship with translation progress
    translation = relationship("TranslationProgress", back_populates="chunks")

    @declared_attr
    def __table_args__(cls):
        return (
            Index(f"ix_{cls.__tablename__}_process_id_page_number", "processId", "pageNumber"),
        )